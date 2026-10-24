# -*- coding: utf-8 -*-
"""P 通道唯一入口（2026-10-17 定案）。

正确姿势（三步，缺一不可）：
  1. 新建**独立的** Edge 实例（CDP_Profile，含 ScriptCat 扩展）于 9222 ——
     不碰用户任务栏那个固定窗口；从这里弹开的窗口才是对的。
  2. Playwright `connect_over_cdp(9222)` 拿到 page（P 通道）。
  3. 先 `find_tab` 复用，没有才 `open_tab` 新建；任务结束 `cleanup_owned_tabs()`。

用法（纯 Python，可直接 import 调用）：
    from browser_channels.p_channel import ensure_p, attach_deepseek
    page = attach_deepseek()   # 返回 chat.deepseek.com 的 page
    print(page.evaluate("() => document.title"))
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_FALLBACK = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
UD = os.getenv("CDP_USER_DATA_DIR", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\CDP_Profile"))
PORT = 9222


def _port_open(p: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) == 0


def _kill_port_holder() -> None:
    """杀掉占用 9222 的旧 Edge（避免端口冲突）。"""
    try:
        import psutil
    except Exception:
        return
    for proc in psutil.net_connections():
        try:
            if proc.laddr.port == PORT and proc.status == "LISTEN":
                psutil.Process(proc.pid).kill()
        except Exception:
            pass
    time.sleep(1)


def ensure_edge_cdp() -> bool:
    """新建独立 Edge 实例（CDP_Profile，含 ScriptCat）于 9222。"""
    exe = EDGE if Path(EDGE).exists() else EDGE_FALLBACK
    if not Path(exe).exists():
        raise RuntimeError(f"msedge.exe 未找到: {exe}")
    _kill_port_holder()
    subprocess.Popen(
        [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={UD}",
         "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if _port_open():
            return True
        time.sleep(0.5)
    raise RuntimeError("Edge CDP 9222 未开")


def ensure_p():
    """确保 P 通道就绪（Edge + Playwright attach）。返回 PlaywrightAttachChannel。"""
    from browser_channels.channels.playwright_ch import PlaywrightAttachChannel

    if not _port_open():
        ensure_edge_cdp()
    ch = PlaywrightAttachChannel(port=PORT)
    ch._ensure()
    return ch


def attach_deepseek(wait_bridge_s: float = 30.0):
    """返回 chat.deepseek.com 的 page；若页面无 __BG_BRIDGE 则等待 ScriptCat 注入。"""
    ch = ensure_p()
    pg = ch.open_tab("https://chat.deepseek.com", reuse=True)
    if pg is None:
        return None
    deadline = time.monotonic() + wait_bridge_s
    while time.monotonic() < deadline:
        try:
            v = pg.evaluate("() => (typeof __BG_BRIDGE!=='undefined') ? __BG_BRIDGE.version : null")
        except Exception:
            v = None
        if v:
            return pg
        time.sleep(1.0)
    return pg


def cleanup():
    """任务结束：关闭本通道创建的临时标签页，不关用户已有的。"""
    try:
        ch = ensure_p()
        return ch.cleanup_owned_tabs()
    except Exception:
        return 0


if __name__ == "__main__":
    pg = attach_deepseek()
    print("page:", pg.url if pg else None)
    if pg:
        print("bridge:", pg.evaluate("() => (typeof __BG_BRIDGE!=='undefined') ? __BG_BRIDGE.version : null"))
    cleanup()
    print("P_OK")