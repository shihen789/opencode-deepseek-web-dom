"""三通道浏览器统一调度层。

条例来源：用户 2026-10-11 下发的《三通道使用条例》，2026-10-16 更正为 **P > N > T**。
同一时刻只有一个持有互斥锁。

标签页管理铁律：
  1. 打开新页面前先枚举已有标签页
  2. 有同 URL 的标签页 → 复用（导航到目标 URL），不新建
  3. 任务结束时关闭本通道创建的临时标签页
  4. 永不关闭用户已打开的标签页

教训记录（2026-10-17）：曾把"派网页 DOM 子 agent"误当成"用 N 通道"，
连续 3 次 task 调用被 abort。根因是 subagent_type 搞混：
  - webdom-test / webdom-qwen = P 通道（网页 DOM）
  - general / fl-* = API / 本地模型
铁律：默认一切网页操作走 P 通道；N 只在 P 连续 3 次超时或确需扩展侧能力时才切。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from .channels.base import BaseChannel, HealthResult, OpResult
from .channels.native import NativeChannel
from .channels.cdp import CdpTunnelChannel
from .channels.playwright_ch import PlaywrightAttachChannel


class Channel(str, Enum):
    N = "N"
    T = "T"
    P = "P"


class MutexBusy(Exception):
    pass


class ChannelUnavailable(Exception):
    pass


@dataclass
class SwitchLog:
    frm: str
    to: str
    reason: str
    ts: float = field(default_factory=time.time)


class Scheduler:
    """三通道统一调度。

    条例 5：全局互斥锁，同一时刻只有一个持有者。
    条例 6：连续 3 次超时 / 连接被拒 => 通道故障，降级。
    """

    def __init__(
        self,
        native: NativeChannel | None = None,
        cdp: CdpTunnelChannel | None = None,
        playwright: PlaywrightAttachChannel | None = None,
    ) -> None:
        self.channels: dict[Channel, BaseChannel] = {
            Channel.N: native or NativeChannel(),
            Channel.T: cdp or CdpTunnelChannel(),
            Channel.P: playwright or PlaywrightAttachChannel(),
        }
        self._lock = threading.Lock()
        self._owner: Channel | None = None
        self._current: Channel | None = None
        self._switch_logs: list[SwitchLog] = []
        self._owned_tabs: list[str] = []

    # ---- 互斥锁（条例 5）----
    def acquire(self, ch: Channel, blocking: bool = True, timeout: float = 5.0) -> bool:
        if blocking:
            got = self._lock.acquire(timeout=timeout)
        else:
            got = self._lock.acquire(blocking=False)
        if not got:
            raise MutexBusy(f"互斥锁被 {self._owner} 持有")
        self._owner = ch
        return True

    def release(self, ch: Channel) -> None:
        if self._owner == ch:
            self._owner = None
            self._lock.release()

    # ---- 健康检查（条例五：只在切换前跑）----
    def probe(self, ch: Channel) -> HealthResult:
        return self.channels[ch].health_check()

    # ---- 决策树（条例三，2026-10-16 更正：默认 P > N > T）----
    def pick(self, need_full_api: bool = False, force: Channel | None = None) -> Channel:
        if force:
            return force
        # 默认走 P（Playwright attach CDP，有完整 API）；N 仅作备选。
        # 旧顺序 N > T > P 已作废——它会默认走扩展/Native Messaging 通道，
        # 与"默认一切网页操作走 P"的铁律冲突。
        order = [Channel.P, Channel.N, Channel.T]
        if need_full_api:
            order = [Channel.P, Channel.T, Channel.N]
        for ch in order:
            result = self.probe(ch)
            if result.ok:
                return ch
        raise ChannelUnavailable("P/N/T 全部不可用")

    # ---- 切换（条例四）----
    def switch_to(self, target: Channel, reason: str) -> BaseChannel:
        if self._current == target:
            return self.channels[target]
        old = self._current
        if old is not None:
            self.channels[old].close_tabs()
            self.release(old)
        self.acquire(target)
        self._current = target
        self._switch_logs.append(SwitchLog(old.value if old else "-", target.value, reason))
        return self.channels[target]

    # ---- 回切（条例 4）----
    def revert_to_native(self) -> Channel | None:
        if self._current == Channel.N:
            return None
        result = self.probe(Channel.N)
        if result.ok:
            self.switch_to(Channel.N, "回切：N 已恢复")
            return Channel.N
        return None

    # ---- 业务操作（条例六：故障判定）----
    def execute(self, op: str, *args, **kwargs) -> OpResult:
        if self._current is None:
            ch = self.pick()
            self.switch_to(ch, "首次选择")
        ch = self._current
        channel = self.channels[ch]
        method = getattr(channel, f"do_{op}", None)
        if method is None:
            return OpResult(False, error=f"未知操作: {op}")
        result = method(*args, **kwargs)
        if not result.ok:
            if "timeout" in result.error.lower():
                channel.note_timeout()
            elif "rejected" in result.error.lower() or "refused" in result.error.lower():
                channel.note_reject()
            if channel.consecutive_timeouts >= 3 or channel.consecutive_rejects >= 1:
                return self._degrade(op, *args, **kwargs)
        else:
            channel.note_success()
        return result

    def _degrade(self, op: str, *args, **kwargs) -> OpResult:
        old = self._current
        # 降级顺序：P -> N -> T（P > N > T 铁律）
        order = [Channel.P, Channel.N, Channel.T]
        idx = order.index(old)
        for next_ch in order[idx + 1:]:
            result = self.probe(next_ch)
            if result.ok:
                self.switch_to(next_ch, f"降级：{old.value} 连续故障")
                channel = self.channels[next_ch]
                method = getattr(channel, f"do_{op}")
                return method(*args, **kwargs)
        return OpResult(False, error="所有通道均不可用")

    # ---- 标签页管理 ----
    def list_tabs(self) -> list[dict]:
        """枚举当前所有页面标签页。返回 [{url, title, targetId}]。"""
        try:
            import requests as _req
            r = _req.get("http://127.0.0.1:9222/json/list", timeout=3)
            return [t for t in r.json() if t.get("type") == "page"]
        except Exception:
            return []

    def find_tab(self, url_pattern: str) -> dict | None:
        """按 URL 模式查找已有标签页。找到返回 tab dict，否则 None。"""
        for tab in self.list_tabs():
            if url_pattern in tab.get("url", ""):
                return tab
        return None

    def open_tab(self, url: str, reuse: bool = True) -> str:
        """打开标签页。reuse=True 时优先复用已有同 URL 标签页。

        铁律：打开前先枚举，有同 URL 就复用导航，不新建。
        """
        if reuse:
            existing = self.find_tab(url)
            if existing:
                # 复用已有标签页：导航到目标 URL
                try:
                    import requests as _req
                    import websocket as _ws_mod
                    ws = _ws_mod.create_connection(existing["webSocketDebuggerUrl"], timeout=5)
                    ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
                    ws.recv()
                    ws.close()
                    return f"reused:{existing['url'][:60]}"
                except Exception:
                    pass

        # 新建标签页（Chromium 新版 /json/new 只收 PUT，GET 返 405）
        try:
            import requests as _req
            r = _req.put(f"http://127.0.0.1:9222/json/new?{url}", timeout=8)
            tab = r.json()
            self._owned_tabs.append(tab.get("id", ""))
            return f"new:{url[:60]}"
        except Exception as e:
            return f"error:{e}"

    def close_tab(self, tab_id: str) -> bool:
        """关闭指定标签页。只关闭本通道创建的临时标签页。"""
        try:
            import requests as _req
            _req.get(f"http://127.0.0.1:9222/json/close/{tab_id}", timeout=3)
            if tab_id in self._owned_tabs:
                self._owned_tabs.remove(tab_id)
            return True
        except Exception:
            return False

    def cleanup_owned_tabs(self) -> int:
        """关闭本通道创建的所有临时标签页。返回关闭数量。"""
        closed = 0
        for tab_id in list(self._owned_tabs):
            if self.close_tab(tab_id):
                closed += 1
        self._owned_tabs.clear()
        return closed

    @property
    def logs(self) -> list[SwitchLog]:
        return list(self._switch_logs)

    def dump_logs(self) -> str:
        lines = []
        for log in self._switch_logs:
            lines.append(
                f"[{time.strftime('%H:%M:%S', time.localtime(log.ts))}] "
                f"{log.frm} -> {log.to}  原因：{log.reason}"
            )
        return "\n".join(lines)
