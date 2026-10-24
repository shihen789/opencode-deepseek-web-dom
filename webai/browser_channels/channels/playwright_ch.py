"""P 通道：Playwright 附加模式（持久化版）。

持久化保证：
  - 附加的是**本地真实 Default profile**（cookie/登录态/ScriptCat 扩展全在），不用复制 profile
  - 9222 不在时自动用 Default profile 拉起 Edge（复用 native_host.ensure_edge）
  - 打开过的标签页记进 temp/p_channel_state.json，进程重启后仍能识别/清理
  - close_tabs 只关本通道开的页，绝不关浏览器
"""
from __future__ import annotations

import json
import os
import time

import requests
from playwright.sync_api import sync_playwright

from .base import BaseChannel, HealthResult, OpResult

_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "temp", "p_channel_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"owned_urls": []}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


class PlaywrightAttachChannel(BaseChannel):
    code = "P"
    name = "Playwright 附加模式"

    def __init__(self, port: int = 9222) -> None:
        super().__init__()
        self.port = port
        self._pw = None
        self._browser = None
        self._page = None

    # ---- 边缘浏览器生命周期 ----
    def ensure_browser(self) -> bool:
        """确保本地 Edge（Default profile，带扩展）以 CDP 运行；不在则自动拉起。"""
        try:
            requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=2).json()
            return True
        except Exception:
            pass
        try:
            from ..native_host import ensure_edge
        except Exception:
            try:
                from browser_channels.native_host import ensure_edge
            except Exception:
                return False
        ok, _detail = ensure_edge()
        return ok

    def health_check(self, timeout_s: float = 3.0) -> HealthResult:
        t0 = time.monotonic()
        try:
            r = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=timeout_s)
            if r.status_code != 200:
                return HealthResult(False, f"HTTP {r.status_code}", int((time.monotonic() - t0) * 1000))
            r.json()
            self._connected = True
            self.note_success()
            return HealthResult(True, "CDP 端口就绪", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            self._connected = False
            return HealthResult(False, str(exc), int((time.monotonic() - t0) * 1000))

    def _ensure(self) -> None:
        if self._page is not None:
            return
        if not self.ensure_browser():
            raise RuntimeError("本地 Edge（Default profile + CDP 9222）不可用")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # ---- 标签页管理 ----
    def list_tabs(self) -> list[dict]:
        try:
            r = requests.get(f"http://127.0.0.1:{self.port}/json/list", timeout=3)
            return [t for t in r.json() if t.get("type") == "page"]
        except Exception:
            return []

    def find_tab(self, url_pattern: str) -> dict | None:
        for tab in self.list_tabs():
            if url_pattern in (tab.get("url") or ""):
                return tab
        return None

    def attach_page(self, url_pattern: str):
        """按 URL 模式选中已有标签页作为当前工作页（不新建、不导航）。"""
        self._ensure()
        for pg in self._browser.contexts[0].pages:
            if url_pattern in pg.url:
                self._page = pg
                return pg
        return None

    def open_tab(self, url: str, reuse: bool = True):
        """打开标签页：有同 URL 先复用；新建则记为本通道所有。"""
        self._ensure()
        ctx = self._browser.contexts[0]
        if reuse:
            for pg in ctx.pages:
                if url in pg.url or pg.url in url:
                    self._page = pg
                    return pg
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=40000)
        self._page = pg
        st = _load_state()
        owned = st.setdefault("owned_urls", [])
        if url not in owned:
            owned.append(url)
            if len(owned) > 50:
                del owned[:-50]
        _save_state(st)
        return pg

    # ---- 业务操作 ----
    def do_open(self, url: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            self._ensure()
            self._page.goto(url, timeout=int(timeout_s * 1000), wait_until="domcontentloaded")
            self.note_success()
            return OpResult(True, {"url": self._page.url}, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_click(self, selector: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            self._ensure()
            self._page.click(selector, timeout=int(timeout_s * 1000))
            self.note_success()
            return OpResult(True, None, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_type(self, selector: str, text: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            self._ensure()
            self._page.fill(selector, text, timeout=int(timeout_s * 1000))
            self.note_success()
            return OpResult(True, None, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_read(self, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            self._ensure()
            text = self._page.inner_text("body", timeout=int(timeout_s * 1000))
            self.note_success()
            return OpResult(True, text[:5000], "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def close_tabs(self) -> None:
        """只关本通道开过的页，不关浏览器。"""
        st = _load_state()
        owned = list(st.get("owned_urls", []))
        if owned:
            try:
                tabs = self.list_tabs()
                for tab in tabs:
                    u = tab.get("url") or ""
                    if any(o and (o in u or u in o) for o in owned):
                        try:
                            requests.get(
                                f"http://127.0.0.1:{self.port}/json/close/{tab['id']}", timeout=3
                            )
                        except Exception:
                            pass
            except Exception:
                pass
            st["owned_urls"] = []
            _save_state(st)
        # 断开 Playwright 连接，浏览器保持运行
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._page = None
