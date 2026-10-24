# -*- coding: utf-8 -*-
"""N 通道 Native Messaging Host：打开并控制本地浏览器（Default profile，带扩展）。

协议：native messaging（4 字节 LE 长度 + JSON），stdin 读 / stdout 写。
命令：
  ping                      -> {"ok":true}
  open {url}                -> 确保本地 Edge（Default profile，含 ScriptCat 扩展）
                                以 CDP 9222 运行，新开标签页打开 url
  click {selector}          -> CDP Runtime.evaluate 点击
  type {selector,text}      -> CDP 置值 + input/change 事件
  read                      -> 取当前标签页 body.innerText
  close_tabs                -> 关闭本 host 打开过的标签页

说明：若 Edge 已在跑但没开 9222（CDP 不可用），open 会优雅关闭后带
--remote-debugging-port 重启——P 通道（evaluate 完整 API）依赖 9222。
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORT = 9222
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
PROFILE = "Default"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "native_state.json")


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(os.path.join(os.path.dirname(STATE_PATH), "native_host.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------- Edge 生命周期 ----------
def _edge_exe() -> str | None:
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def port_open(port: int = PORT) -> bool:
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _msedge_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
            capture_output=True, text=True, timeout=10,
        )
        return "msedge.exe" in (out.stdout or "")
    except Exception:
        return False


def _kill_edge() -> bool:
    try:
        subprocess.run(["taskkill", "/IM", "msedge.exe"], capture_output=True, timeout=15)
        for _ in range(15):
            if not _msedge_running():
                return True
            time.sleep(1)
        subprocess.run(["taskkill", "/IM", "msedge.exe", "/F"], capture_output=True, timeout=15)
        time.sleep(2)
        return not _msedge_running()
    except Exception as exc:
        _log(f"kill_edge fail: {exc}")
        return False


def ensure_edge(restart_without_cdp: bool = True) -> tuple[bool, str]:
    """确保本地 Edge（Default profile，带扩展）以 CDP 9222 运行。"""
    if port_open():
        return True, "cdp-already"
    exe = _edge_exe()
    if not exe:
        return False, f"msedge.exe not found: {EDGE_CANDIDATES}"
    if _msedge_running():
        if not restart_without_cdp:
            return False, "edge-running-without-cdp"
        _log("edge running without CDP -> graceful restart")
        _kill_edge()
    subprocess.Popen(
        [exe,
         f"--remote-debugging-port={PORT}",
         f"--user-data-dir={USER_DATA}",
         f"--profile-directory={PROFILE}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _log("edge launched with CDP, waiting port ...")
    for _ in range(40):
        if port_open():
            time.sleep(1.5)  # 等首屏/扩展就绪
            return True, "cdp-launched"
        time.sleep(0.5)
    return False, "cdp port timeout"


# ---------- CDP ----------
def cdp_list() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        return json.load(r)


def _pages() -> list[dict]:
    return [t for t in cdp_list() if t.get("type") == "page"]


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"owned": []}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def _pick_target() -> dict | None:
    pages = _pages()
    if not pages:
        return None
    st = _load_state()
    alive = {p["id"]: p for p in pages}
    for tid in reversed(st.get("owned", [])):
        if tid in alive:
            return alive[tid]
    http_pages = [p for p in pages if (p.get("url") or "").startswith("http")]
    return http_pages[-1] if http_pages else pages[-1]


def _ws_eval(ws_url: str, expression: str, timeout_s: float = 15.0) -> dict:
    import websocket
    ws = websocket.create_connection(ws_url, timeout=timeout_s)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expression, "returnByValue": True}}))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = ws.recv()
            if not isinstance(raw, str):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == 1:
                return msg
        raise TimeoutError("CDP eval timeout")
    finally:
        ws.close()


def _eval_ok(result_msg: dict) -> tuple[bool, object]:
    res = result_msg.get("result") or {}
    if res.get("exceptionDetails"):
        return False, json.dumps(res["exceptionDetails"], ensure_ascii=False)[:500]
    val = (res.get("result") or {}).get("value")
    return True, val


# ---------- 命令实现 ----------
def cmd_open(url: str) -> dict:
    ok, detail = ensure_edge()
    if not ok:
        return {"ok": False, "error": detail}
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{urllib.parse.quote(url, safe=':/?&=%#')}",
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            tab = json.load(r)
    except urllib.error.HTTPError:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/json/new?{urllib.parse.quote(url, safe=':/?&=%#')}", timeout=8
        ) as r:
            tab = json.load(r)
    except Exception as exc:
        return {"ok": False, "error": f"open tab fail: {exc}"}
    st = _load_state()
    st.setdefault("owned", [])
    if tab.get("id") and tab["id"] not in st["owned"]:
        st["owned"].append(tab["id"])
    _save_state(st)
    _log(f"open {url} -> {tab.get('id')}")
    time.sleep(1.0)
    return {"ok": True, "id": tab.get("id"), "url": tab.get("url"), "edge": detail}


def cmd_click(selector: str) -> dict:
    tgt = _pick_target()
    if not tgt:
        return {"ok": False, "error": "no target page"}
    expr = ("(()=>{const el=document.querySelector(%s);"
            "if(!el)return 'no-element';el.click();return 'clicked';})()" % json.dumps(selector))
    msg = _ws_eval(tgt["webSocketDebuggerUrl"], expr)
    ok, val = _eval_ok(msg)
    if not ok:
        return {"ok": False, "error": val}
    return {"ok": val == "clicked", "result": val}


def cmd_type(selector: str, text: str) -> dict:
    tgt = _pick_target()
    if not tgt:
        return {"ok": False, "error": "no target page"}
    expr = (
        "(()=>{const el=document.querySelector(%s);if(!el)return 'no-element';"
        "el.focus();const proto=(el.tagName==='TEXTAREA')?HTMLTextAreaElement:HTMLInputElement;"
        "if('value' in el){const d=Object.getOwnPropertyDescriptor(proto,'value');"
        "if(d&&d.set)d.set.call(el,%s);else el.value=%s;}"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));return 'typed';})()"
        % (json.dumps(selector), json.dumps(text), json.dumps(text))
    )
    msg = _ws_eval(tgt["webSocketDebuggerUrl"], expr)
    ok, val = _eval_ok(msg)
    if not ok:
        return {"ok": False, "error": val}
    return {"ok": val == "typed", "result": val}


def cmd_read() -> dict:
    tgt = _pick_target()
    if not tgt:
        return {"ok": False, "error": "no target page"}
    msg = _ws_eval(tgt["webSocketDebuggerUrl"], "document.body.innerText")
    ok, val = _eval_ok(msg)
    if not ok:
        return {"ok": False, "error": val}
    return {"ok": True, "text": val, "url": tgt.get("url")}


def cmd_close_tabs() -> dict:
    st = _load_state()
    closed = 0
    try:
        pages = {p["id"] for p in _pages()}
    except Exception:
        pages = set()
    for tid in list(st.get("owned", [])):
        if tid in pages:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/close/{tid}", timeout=3)
                closed += 1
            except Exception:
                pass
    st["owned"] = []
    _save_state(st)
    return {"ok": True, "closed": closed}


# ---------- native messaging 主循环 ----------
def read_msg() -> dict | None:
    hdr = sys.stdin.buffer.read(4)
    if len(hdr) < 4:
        return None
    n = struct.unpack("<I", hdr)[0]
    body = sys.stdin.buffer.read(n)
    return json.loads(body.decode("utf-8"))


def write_msg(obj: dict) -> None:
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(b)) + b)
    sys.stdout.buffer.flush()


def handle(msg: dict) -> dict:
    cmd = msg.get("cmd")
    try:
        if cmd == "ping":
            return {"ok": True, "pong": True, "pid": os.getpid()}
        if cmd == "open":
            return cmd_open(msg.get("url", "about:blank"))
        if cmd == "click":
            return cmd_click(msg.get("selector", ""))
        if cmd == "type":
            return cmd_type(msg.get("selector", ""), msg.get("text", ""))
        if cmd == "read":
            return cmd_read()
        if cmd == "close_tabs":
            return cmd_close_tabs()
        return {"ok": False, "error": f"unknown cmd: {cmd}"}
    except Exception as exc:
        _log(f"cmd {cmd} error: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    _log(f"host start pid={os.getpid()} args={sys.argv[1:]}")
    if len(sys.argv) > 1:  # 调试模式：python native_host.py '{"cmd":"ping"}'
        print(json.dumps(handle(json.loads(sys.argv[1])), ensure_ascii=False))
        return
    while True:
        try:
            msg = read_msg()
        except Exception as exc:
            _log(f"read fail: {exc}")
            break
        if msg is None:
            break
        write_msg(handle(msg))


if __name__ == "__main__":
    main()
