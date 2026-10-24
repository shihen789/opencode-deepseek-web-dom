# -*- coding: utf-8 -*-
"""千问（Qwen）统一调用封装 v1.0（2026-10-15 新建，网页AI 通用方案后端之一）。

流程（对应 .archify/workflow-webai-generic-call-20261015-102633/webai-call.html）：
  1. ensure_page(): 先查后连 —— 已有 chat.qwen.ai 页复用（attach_page），无则
                    open_tab 新建；年龄确认 modal 自动放行（一次性）。
  2. ask(text)    : fill composer → click 发送 → 轮询（停止按钮消失 + 正文 1.4s 稳定）
                    → 取最后一条 assistant 气泡的 .qwen-markdown-text 返回。
  3. ask_files()  : 隐藏 input[type=file] 直注 / filechooser 兜底 → 再发文字问。

纯 P 通道（CDP 9222）DOM 收发，无 WS/bridge 依赖。
与网页AI/deepseek/deepseek_call.py 同构：ask(text) → {ok, text, elapsed_ms}。
约定：同标签页连续追问（复用会话，不清空），与 DeepSeek 行为一致。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # 网页AI/qwen/../.. → 项目根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

QWEN_URL = "https://chat.qwen.ai/"
SEL_TA = "textarea.message-input-textarea"
SEL_SEND = "button.send-button"
SEL_ASSIST = ".qwen-chat-message-assistant"
SEL_MARKDOWN = ".qwen-markdown-text"

# 页面状态探测：生成中（停止按钮在）/ 发送按钮是否恢复 / 可见 modal
_JS_STATE = """() => {
    const stopBtn = [...document.querySelectorAll('button')].find(b =>
        /stop|停止/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.className || '')));
    const visModals = [...document.querySelectorAll('.ant-modal-wrap')].filter(w => {
        const cs = getComputedStyle(w);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && w.offsetHeight > 0;
    });
    return {
        generating: !!stopBtn,
        sendVisible: !!document.querySelector('button.send-button'),
        hasComposer: !!document.querySelector('textarea.message-input-textarea'),
        ageModal: document.body.innerText.includes('请确认你的年龄'),
        modalsVisible: visModals.length,
        modalTexts: visModals.map(w => (w.innerText || '').slice(0, 120)),
        url: location.href
    };
}"""

_channel = None  # 模块级单例（同进程只允许一个 Playwright sync 实例）


def _chan():
    global _channel
    if _channel is None:
        from browser_channels.channels.playwright_ch import PlaywrightAttachChannel

        _channel = PlaywrightAttachChannel()
    return _channel


def _dismiss_modals(page) -> bool:
    """清场挡路 modal：年龄确认点「继续」，欢迎/登录等点 X（兜底 ESC）。"""
    try:
        st = page.evaluate(_JS_STATE)
    except Exception:
        return False
    if not st.get("modalsVisible") and not st.get("ageModal"):
        return False
    closed = False
    for t in st.get("modalTexts") or []:
        if "请确认你的年龄" in t:
            try:
                page.click(".ant-modal-wrap:visible button:has-text('继续')", timeout=2000)
                closed = True
            except Exception:
                pass
    try:
        if page.locator(".ant-modal-wrap:visible .ant-modal-close").count():
            page.click(".ant-modal-wrap:visible .ant-modal-close", timeout=2000)
            closed = True
        elif page.locator(".ant-modal-wrap:visible").count():
            page.keyboard.press("Escape")
            closed = True
    except Exception:
        pass
    if closed:
        time.sleep(0.4)
    return closed


def ensure_page():
    """先查后连：已有 qwen 页复用，没有才新建。返回 Playwright page。"""
    c = _chan()
    pg = c.attach_page("chat.qwen.ai")
    if pg is None:
        c.open_tab(QWEN_URL)
        pg = c._page
        pg.wait_for_selector(SEL_TA, timeout=25000)
    pg.wait_for_selector(SEL_TA, timeout=25000)
    _dismiss_modals(pg)
    return pg


def _last_assistant_text(page, fallback: str = "") -> str:
    """取最后一条 assistant 气泡正文（qwen-markdown-text 优先，剥离思考折叠区），**完整返回，不截断**。"""
    txt = page.evaluate(
        """() => {
        const nodes = document.querySelectorAll('.qwen-chat-message-assistant');
        const last = nodes[nodes.length - 1];
        if (!last) return '';
        const parts = [...last.querySelectorAll('.qwen-markdown-text')].map(e => e.innerText);
        const joined = parts.join('\\n').trim();
        return joined || last.innerText;
    }"""
    )
    txt = (txt or "").strip()
    if txt == fallback:
        return txt
    # 剥离思考折叠标签（"已经完成思考" 等整行）
    lines = [ln for ln in txt.splitlines() if ln.strip() and "已经完成思考" not in ln and "思考过程" not in ln]
    return "\n".join(lines).strip() or txt


def ask(text: str, timeout_s: float = 90.0) -> dict:
    """千问网页发问，等待回复完成。返回 {ok, text, elapsed_ms}。"""
    t0 = time.monotonic()
    pg = ensure_page()

    before = pg.locator(SEL_ASSIST).count()
    pg.fill(SEL_TA, text)
    _dismiss_modals(pg)  # 发送前清场（欢迎/登录弹窗可能刚弹出）
    sent = False
    for attempt in range(3):
        try:
            pg.click(SEL_SEND, timeout=6000)
            sent = True
            break
        except Exception:
            _dismiss_modals(pg)
            time.sleep(0.5)
    if not sent:
        return {"ok": False, "text": "", "error": "send blocked by modal/login wall",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}

    deadline = t0 + timeout_s
    prev, same = None, 0
    last_err = ""
    while time.monotonic() < deadline:
        time.sleep(0.7)
        try:
            st = pg.evaluate(_JS_STATE)
        except Exception as exc:
            last_err = str(exc)
            continue
        if st.get("modalsVisible") or st.get("ageModal"):
            _dismiss_modals(pg)
            continue
        n = pg.locator(SEL_ASSIST).count()
        if n <= before:
            # 新气泡还没出现（可能被登录墙等拦下）
            if not st["generating"] and time.monotonic() - t0 > 8 and n <= before:
                if pg.locator("textarea.message-input-textarea").count() == 0:
                    return {"ok": False, "text": "", "error": "composer missing (login wall?)",
                            "elapsed_ms": int((time.monotonic() - t0) * 1000)}
            continue
        if st["generating"]:
            continue
        cur = _last_assistant_text(pg)
        if cur and cur == prev:
            same += 1
            if same >= 2:  # ~1.4s 稳定
                return {"ok": True, "text": cur, "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        else:
            prev, same = cur, 0

    partial = ""
    try:
        if pg.locator(SEL_ASSIST).count() > before:
            partial = _last_assistant_text(pg)
    except Exception:
        pass
    return {"ok": False, "text": partial, "error": last_err or "timeout",
            "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def send_file(path) -> bool:
    """把文件挂进 composer（隐藏 input[type=file] 直注，兜底 filechooser）。"""
    pg = ensure_page()
    p = str(Path(path).resolve())
    n_inputs = pg.evaluate(
        "() => document.querySelectorAll('input[type=file]').length"
    )
    if n_inputs:
        try:
            pg.set_input_files("input[type=file]", p, timeout=5000)
            time.sleep(1.0)
            return True
        except Exception:
            pass
    try:
        with pg.expect_file_chooser(timeout=5000) as fc_info:
            pg.click("button[class*=attach], button[class*=plus], button[class*=upload], "
                     ".message-input button:first-of-type", timeout=3000)
        fc_info.value.set_files(p)
        time.sleep(1.0)
        return True
    except Exception:
        return False


def ask_files(paths, text: str = "", timeout_s: float = 120.0) -> dict:
    """先挂文件（原文件直发，不转述）再发文字问。"""
    t0 = time.monotonic()
    pg = ensure_page()
    ok_files = []
    for p in (paths if isinstance(paths, (list, tuple)) else [paths]):
        if send_file(p):
            ok_files.append(Path(p).name)
        else:
            return {"ok": False, "text": "", "error": f"file attach failed: {p}",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    if not text:
        return {"ok": True, "text": f"files attached: {', '.join(ok_files)}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    return ask(text, timeout_s=timeout_s)


def dbg() -> dict:
    """当前页状态（诊断用）。"""
    pg = ensure_page()
    st = pg.evaluate(_JS_STATE)
    st["assistantCount"] = pg.locator(SEL_ASSIST).count()
    return st


if __name__ == "__main__":
    import json

    print(json.dumps(ask("回复两个字：收到"), ensure_ascii=False))
