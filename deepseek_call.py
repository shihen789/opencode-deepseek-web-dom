# -*- coding: utf-8 -*-
"""DeepSeek 统一调用封装。"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from websockets.sync.client import connect

AGENT_DIR = Path(r"C:\Users\39841\PyCharmMiscProject\deepseek-agent")
BRIDGE_JS = AGENT_DIR / "scripts" / "ds_bg_bridge.user.js"
REPLY_LOG = AGENT_DIR / "logs" / "chat_replies.jsonl"
EXECUTOR_LOG = AGENT_DIR / "logs" / "executor.log"
LOCK_PATH = AGENT_DIR / "logs" / "session.lock"
WS_HOST, WS_PORT = "localhost", 8765
DEEPSEEK_URL = "https://chat.deepseek.com"

MAX_TEXT_CHARS = 50000
LOCK_TIMEOUT_S = 900.0
_SESSION_RE = re.compile(r"/a/chat/s/([0-9a-zA-Z_-]+)")


def _stderr(*args):
    try:
        print(*args, file=sys.stderr, flush=True)
    except Exception:
        pass


# ---- 1. 服务端 ----
def server_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", WS_PORT)) == 0


def ensure_server() -> str:
    if server_up():
        return "already-up"
    subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(AGENT_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(0.5)
        if server_up():
            return "started"
    raise RuntimeError("执行器 8765 启动超时")


# ---- 2. 页面 attach ----
_SCHED = None


def _scheduler():
    global _SCHED
    if _SCHED is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from browser_channels.scheduler import Scheduler, Channel
        from browser_channels.channels.playwright_ch import PlaywrightAttachChannel
        _SCHED = Scheduler(playwright=PlaywrightAttachChannel(port=9222))
        _SCHED.switch_to(Channel.P, "deepseek_call")
    return _SCHED


def _iter_pages():
    """枚举 P 通道所有页面（多种访问方式，任一可用即可）。"""
    s = _scheduler()
    from browser_channels.scheduler import Channel as _Ch
    ch = s.channels[_Ch.P]
    ctx = None
    for attr in ("_context", "context", "_ctx", "ctx"):
        ctx = getattr(ch, attr, None)
        if ctx is not None:
            break
    if ctx is None:
        return []
    try:
        return list(ctx.pages)
    except Exception:
        return []


def _attach_page():
    """优先返回 URL 已带 uuid 的 DeepSeek 标签页；否则按域名 attach。"""
    for pg in _iter_pages():
        try:
            u = pg.url or ""
        except Exception:
            continue
        if "chat.deepseek.com" in u and _SESSION_RE.search(u):
            return pg

    s = _scheduler()
    from browser_channels.scheduler import Channel as _Ch
    ch = s.channels[_Ch.P]
    for _ in range(3):
        pg = ch.attach_page("chat.deepseek.com")
        if pg is not None:
            return pg
        time.sleep(0.8)
    try:
        return ch.open_tab(DEEPSEEK_URL, reuse=True)
    except Exception:
        return ch.attach_page("chat.deepseek.com")


# ---- 3. bridge ----
def _bridge_version() -> str:
    for line in BRIDGE_JS.read_text(encoding="utf-8").splitlines()[:20]:
        if line.startswith("// @version"):
            return line.split()[-1]
    raise RuntimeError("读不到 @version")


def _inject_code(page, code):
    return page.evaluate(
        """(code) => {
            try {
                var prev = window.__BG_BRIDGE;
                if (prev && typeof prev.destroy === 'function') {
                    try { prev.destroy(); } catch (e) {}
                }
                try {
                    var fr = document.createElement('iframe');
                    fr.style.display = 'none';
                    (document.documentElement || document.body).appendChild(fr);
                    var w = fr.contentWindow;
                    if (w) {
                        try { if (typeof w.fetch === 'function') window.fetch = w.fetch; } catch (e) {}
                        try { if (w.XMLHttpRequest && w.XMLHttpRequest.prototype) {
                            window.XMLHttpRequest.prototype.open = w.XMLHttpRequest.prototype.open;
                            window.XMLHttpRequest.prototype.send = w.XMLHttpRequest.prototype.send;
                        } } catch (e) {}
                    }
                    if (fr.parentNode) fr.parentNode.removeChild(fr);
                } catch (e) {}
                try { delete window.__BG_BRIDGE; } catch (e) { window.__BG_BRIDGE = undefined; }
                try { delete window.__dsNetHook; } catch (e) { window.__dsNetHook = undefined; }
                new Function(code)();
                try { if (window.__BG_BRIDGE && window.__BG_BRIDGE.setExternal) window.__BG_BRIDGE.setExternal(true); } catch (e) {}
            } catch (e) { return 'err:' + e.message; }
            return (typeof __BG_BRIDGE !== 'undefined' && __BG_BRIDGE) ? __BG_BRIDGE.version : 'no';
        }""",
        code,
    )


def _wait_bridge_ready(timeout_s: float = 25.0, want: str | None = None) -> bool:
    if want is None:
        try:
            want = _bridge_version()
        except Exception:
            want = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            st = bridge_status()
            if st.get("ws") == "open" and st.get("baselineDone") and not st.get("generating"):
                if want is None or st.get("version") == want:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _force_external(page) -> None:
    if page is None:
        return
    try:
        page.evaluate(
            "() => { try { if (typeof __BG_BRIDGE!=='undefined' && __BG_BRIDGE.setExternal) "
            "return __BG_BRIDGE.setExternal(true); } catch (e) {} return null; }"
        )
    except Exception:
        pass


def ensure_bridge(force: bool = False) -> dict:
    s = _scheduler()
    from browser_channels.scheduler import Channel as _Ch
    p = s.channels[_Ch.P]
    want = _bridge_version()
    page = p.attach_page("chat.deepseek.com")
    if page is None:
        s.open_tab(DEEPSEEK_URL, reuse=True)
        page = p.attach_page("chat.deepseek.com")
    if page is None:
        return {"ok": False, "version": None, "error": "无 DeepSeek 标签页且新建失败"}

    ver = page.evaluate("() => (typeof __BG_BRIDGE!=='undefined') ? __BG_BRIDGE.version : null")
    if ver == want and not force:
        _force_external(page)
        return {"ok": True, "version": ver, "method": "already"}

    code = BRIDGE_JS.read_text(encoding="utf-8")
    new_ver = _inject_code(page, code)
    if new_ver != want:
        return {"ok": False, "version": new_ver, "error": "注入失败"}

    if not _wait_bridge_ready(25):
        return {"ok": False, "version": new_ver, "error": "注入后 25s 内未就绪"}

    _force_external(page)
    return {"ok": True, "version": new_ver, "method": "injected"}


def bridge_status() -> dict:
    page = _attach_page()
    if page is None:
        return {}
    try:
        raw = page.evaluate(
            "() => (typeof __BG_BRIDGE!=='undefined') ? JSON.stringify({v:__BG_BRIDGE.version, s:__BG_BRIDGE.status()}) : '{}'"
        )
    except Exception:
        return {}
    box = json.loads(raw)
    if not box:
        return {}
    out = dict(box.get("s") or {})
    out["version"] = box.get("v")
    return out


# ---- 4. 会话锁 ----
_LOCK_OWNER_PID = None
_LOCK_DEPTH = 0


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if pid == os.getpid():
        return True
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return bool(re.search(r"\s%d\s" % pid, out.stdout or ""))
    except Exception:
        return True


@contextmanager
def _session_lock(timeout_s: float = LOCK_TIMEOUT_S, poll_s: float = 0.5):
    global _LOCK_OWNER_PID, _LOCK_DEPTH
    me = os.getpid()
    if _LOCK_OWNER_PID == me:
        _LOCK_DEPTH += 1
        try:
            yield {"uuid": current_uuid(), "reentrant": True}
        finally:
            _LOCK_DEPTH -= 1
        return

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    info = None
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            info = {"pid": me, "ts": time.time(), "uuid": current_uuid(), "label": "ask"}
            os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            holder = {}
            try:
                holder = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
                pid = int(holder.get("pid") or 0)
                ts = float(holder.get("ts") or 0)
                if not _pid_alive(pid) or (time.time() - ts) > timeout_s:
                    _stderr("[lock] 清理 stale 锁 pid=%d age=%.0fs" % (pid, time.time() - ts))
                    LOCK_PATH.unlink()
                    continue
            except FileNotFoundError:
                continue
            except Exception:
                try:
                    LOCK_PATH.unlink()
                except Exception:
                    pass
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("会话锁等待超时(%.0fs)" % timeout_s)
            time.sleep(poll_s)

    _LOCK_OWNER_PID = me
    _LOCK_DEPTH = 1
    try:
        yield info or {}
    finally:
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH <= 0:
            _LOCK_OWNER_PID = None
            _LOCK_DEPTH = 0
            try:
                cur = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
                if int(cur.get("pid") or 0) == me:
                    LOCK_PATH.unlink()
            except Exception:
                pass


def _update_lock_uuid(uuid_val):
    try:
        if not LOCK_PATH.exists():
            return
        info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if int(info.get("pid") or 0) != os.getpid():
            return
        info["uuid"] = uuid_val
        info["ts"] = time.time()
        LOCK_PATH.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---- 5. 会话 ----
def current_uuid() -> str | None:
    try:
        page = _attach_page()
        if page is None:
            return None
        m = _SESSION_RE.search(page.url or "")
        return m.group(1) if m else None
    except Exception:
        return None


def _uuid_from_replies(limit: int = 30) -> str | None:
    """从回复日志里补回最近使用过的 chatId（页面读不到时的兜底）。"""
    try:
        if not REPLY_LOG.exists():
            return None
        lines = REPLY_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cid = str(d.get("chatId") or "").strip()
            if cid and cid not in ("undefined", "null"):
                return cid
    except Exception:
        pass
    return None


def new_session() -> str | None:
    if not _start_new_session():
        return None
    return current_uuid()


def start_new_session() -> bool:
    return _start_new_session()


def goto_session(uuid: str) -> bool:
    if not uuid:
        return False
    try:
        page = _attach_page()
        if page is None:
            return False
        url = f"https://chat.deepseek.com/a/chat/s/{uuid}"
        for attempt in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(1800)
            if current_uuid() == uuid:
                _update_lock_uuid(uuid)
                return True
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(1800)
            if current_uuid() == uuid:
                _update_lock_uuid(uuid)
                return True
            if attempt == 0:
                continue
        return False
    except Exception:
        return False


def _require_session(action: str = "inject") -> dict | None:
    """会话守卫。"""
    u = current_uuid()
    if u:
        return None

    _stderr("[session] 无 uuid（新对话），先发引导消息建立会话")
    try:
        boot = say_verified("（准备接收内容）请只回复 ok。", lock=False)
        if boot.get("sent"):
            sent_at = time.time() - boot.get("confirm_ms", 0) / 1000
            wait_answer_done(sent_at, timeout_s=60, req_id=boot.get("req_id"))
        else:
            _stderr("[session] 引导消息未发出: %s" % boot.get("error"))
    except Exception as e:
        _stderr("[session] bootstrap 异常: %s" % e)

    for _i in range(30):
        u2 = current_uuid()
        if u2:
            _stderr("[session] 会话已建立 uuid=%s（第 %d 轮）" % (u2, _i + 1))
            return None
        u3 = _uuid_from_replies()
        if u3:
            _stderr("[session] 从日志补回 uuid=%s（第 %d 轮）" % (u3, _i + 1))
            return None
        time.sleep(0.5)

    return {
        "ok": False,
        "error": "no_session",
        "action": action,
        "hint": "引导消息后 15s 内仍无 uuid，附件无法发送。",
    }

def _ensure_session_uuid(want_uuid):
    if not want_uuid:
        return True, current_uuid()
    cur = current_uuid()
    if cur == want_uuid:
        return True, cur
    return False, cur


def _hit_limit(since_ts: float | None = None) -> bool:
    try:
        page = _attach_page()
        if page is None:
            return False
        return bool(page.evaluate("""() => {
            const ta = document.querySelector('textarea');
            if (ta && (ta.disabled || ta.readOnly)) return true;
            if (ta && /达到对话长度上限|对话已达上限|请开启新对话/.test(ta.placeholder || '')) return true;
            const vh = window.innerHeight;
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length) continue;
                const t = (el.textContent || '').trim();
                if (!t || t.length > 80) continue;
                if (!/达到对话长度上限|本次对话已达上限|已达到对话上限|对话长度已达上限|内容过长|请开启新对话/.test(t)) continue;
                if (el.closest('.ds-markdown')) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.top > 0 && r.top < vh) return true;
            }
            return false;
        }"""))
    except Exception:
        return False


def _start_new_session() -> bool:
    try:
        page = _attach_page()
        if page is None:
            return False
        if not _SESSION_RE.search(page.url or ""):
            _update_lock_uuid(None)
            return True
        for sel in ["text=开启新对话", "text=新对话",
                    "button:has-text('开启新对话')",
                    "[data-testid*=new-chat]"]:
            try:
                loc = page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            for i in range(min(n, 3)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    el.click(timeout=4000)
                    page.wait_for_timeout(1500)
                    if not _SESSION_RE.search(page.url or ""):
                        _update_lock_uuid(None)
                        return True
                except Exception:
                    continue
        try:
            page.goto(DEEPSEEK_URL + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            _update_lock_uuid(None)
            return True
        except Exception:
            return False
    except Exception:
        return False


# ---- 6. 收发 ----
def _tail_replies(after_ts: float, req_id: str | None = None) -> list[dict]:
    if not REPLY_LOG.exists():
        return []
    out = []
    for line in REPLY_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if float(d.get("server_ts") or 0) > after_ts:
            out.append(d)
    if req_id:
        hit = [d for d in out if d.get("reqId") == req_id]
        if hit:
            return hit
    return out


def _ask_sync(text: str, timeout_s: float, expect_words: int = 0, lock: bool = True) -> dict:
    if lock:
        with _session_lock(timeout_s=min(LOCK_TIMEOUT_S, max(120.0, timeout_s * 3))):
            return _ask_sync(text, timeout_s, expect_words, lock=False)
    t0 = time.time()
    ensure_server()
    req_id = uuid.uuid4().hex
    with connect(f"ws://{WS_HOST}:{WS_PORT}", open_timeout=5, close_timeout=3) as ws:
        ws.send(json.dumps(
            {"action": "say", "reqId": req_id, "payload": {"text": text, "reqId": req_id}},
            ensure_ascii=False))
        ack = None
        for _ in range(5):
            try:
                msg = json.loads(ws.recv(timeout=3))
            except TimeoutError:
                break
            if msg.get("result") in ("said", "dup") or "error" in msg:
                ack = msg
                break
        if ack and "error" in ack:
            return {"ok": False, "error": ack["error"], "elapsed_ms": 0}
        if ack and ack.get("result") == "dup":
            return {"ok": False, "error": "say 去重拦截（10秒内重复）", "elapsed_ms": 0}

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(1.0)
        replies = _tail_replies(t0 - 1, req_id=req_id)
        if replies:
            last = replies[-1]
            text_r = str(last.get("text") or "")
            if expect_words and len(text_r) < expect_words:
                continue
            return {
                "ok": True, "text": text_r, "msgId": last.get("msgId"),
                "src": last.get("src"), "source": last.get("source"),
                "reqId": last.get("reqId"),
                "reqId_matched": (last.get("reqId") == req_id),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
    return {"ok": False, "error": "等待回复超时(%ss)" % timeout_s, "elapsed_ms": int(timeout_s * 1000)}


def _read_reasoning_from_dom(skip_text: str = "") -> str:
    try:
        page = _attach_page()
        if page is None:
            return ""
        return str(page.evaluate("""(skip) => {
            const nodes = document.querySelectorAll('div.ds-think-content');
            if (!nodes.length) return '';
            for (let i = nodes.length - 1; i >= 0; i--) {
                const t = (nodes[i].innerText || '').trim();
                if (t && t !== skip) return t;
            }
            return '';
        }""", skip_text) or "")
    except Exception:
        return ""


def _read_last_assistant_from_dom() -> tuple[int, str]:
    try:
        page = _attach_page()
        if page is None:
            return 0, ""
        result = page.evaluate("""() => {
            const nodes = document.querySelectorAll('div.ds-markdown');
            if (nodes.length === 0) return { count: 0, text: '' };
            const last = nodes[nodes.length - 1];
            return { count: nodes.length, text: (last.innerText || '').trim() };
        }""")
        return result.get("count", 0), result.get("text", "")
    except Exception:
        return 0, ""


def _last_gen_error(since_ts: float | None = None) -> str | None:
    f = AGENT_DIR / "logs" / "sse_raw.jsonl"
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    for raw in reversed(lines[-6:]):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        t0 = d.get("t0")
        if since_ts and t0 and (float(t0) / 1000.0) < since_ts - 3:
            return None
        if str(d.get("status") or "").upper() == "INCOMPLETE" and not d.get("answer"):
            for u in (d.get("unknown") or []):
                dd = str(u.get("d") or "")
                if "generation_err" in dd or "暂时不可用" in dd:
                    try:
                        return json.loads(dd).get("content") or dd[:80]
                    except Exception:
                        return dd[:80]
            return "generation_err"
        return None
    return None


def wait_answer_done(sent_at: float, timeout_s: float = 300.0, poll_s: float = 0.5,
                     req_id: str | None = None, expect_uuid: str | None = None,
                     pre_text: str = "", pre_reasoning: str = "") -> dict:
    deadline = time.time() + timeout_s
    saw_running = False
    t_done = None
    drift_hits = 0
    while time.time() < deadline:
        if expect_uuid:
            cur = current_uuid()
            if cur and cur != expect_uuid:
                drift_hits += 1
                if drift_hits >= 2:
                    return {"ok": False, "error": "session_drift", "uuid": cur, "want": expect_uuid,
                            "elapsed_ms": int((time.time() - sent_at) * 1000)}
            else:
                drift_hits = 0
        if _hit_limit(sent_at):
            _stderr("[wait_answer_done] 检测到对话上限，开启新对话")
            if _start_new_session():
                expect_uuid = current_uuid()
                return {"ok": False, "error": "limit_reached", "need_retry": True, "uuid": expect_uuid,
                        "elapsed_ms": int((time.time() - sent_at) * 1000)}
            _stderr("[wait_answer_done] 开启新对话失败，继续等待")
        gen = bool(bridge_status().get("generating"))
        if gen:
            saw_running = True
        elif saw_running:
            time.sleep(0.45)
            t_done = time.time()
            break
        time.sleep(poll_s)
    if t_done is None:
        return {"ok": False, "error": "回答未在 %.0fs 内完成（generating 未回到 False）" % timeout_s,
                "elapsed_ms": int(timeout_s * 1000)}
    reasoning_text = _read_reasoning_from_dom(pre_reasoning)
    replies = _tail_replies(sent_at - 1, req_id=req_id)
    if replies:
        last = replies[-1]
        text = str(last.get("text") or "")
        if text and text != pre_text:
            rid = last.get("reqId")
            return {
                "ok": True, "text": text, "msgId": last.get("msgId"),
                "src": last.get("src"), "source": last.get("source"), "reqId": rid,
                "reqId_matched": (rid == req_id) if req_id else None,
                "elapsed_ms": int((t_done - sent_at) * 1000),
                "reasoning": reasoning_text,
            }
    
    for _ in range(20):
        _, dom_text = _read_last_assistant_from_dom()
        if dom_text.strip() and dom_text != pre_text:
            return {"ok": True, "text": dom_text, "msgId": None, "src": "dom", "source": "dom",
                    "reqId": None, "reqId_matched": False if req_id else None,
                    "elapsed_ms": int((time.time() - sent_at) * 1000),
                    "reasoning": reasoning_text}
        time.sleep(0.5)
        
    _, dom_text = _read_last_assistant_from_dom()
    if dom_text.strip():
        return {"ok": True, "text": dom_text, "msgId": None, "src": "dom", "source": "dom",
                "reqId": None, "reqId_matched": False if req_id else None,
                "elapsed_ms": int((time.time() - sent_at) * 1000),
                "reasoning": reasoning_text}
    ge = _last_gen_error(sent_at)
    if ge:
        return {"ok": False, "error": "generation_err: %s" % ge, "need_retry": True,
                "elapsed_ms": int((t_done - sent_at) * 1000)}
    return {"ok": False, "error": "生成结束了但日志里没有新回复行",
            "elapsed_ms": int((t_done - sent_at) * 1000)}


# ---- 7. 发送确认 ----
def _status_healthy(st: dict | None) -> bool:
    if not st:
        return False
    return (st.get("ws") == "open" and st.get("generating") is not None and bool(st.get("baselineDone")))


def _guard_long_text(text: str):
    n = len(text or "")
    if n <= MAX_TEXT_CHARS:
        return None
    out_dir = AGENT_DIR / "logs" / "overflow"
    saved = None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        p = out_dir / ("overflow_%s_%d.txt" % (stamp, n))
        p.write_text(text, encoding="utf-8")
        saved = str(p)
    except Exception as e:
        _stderr("[guard] 落盘失败：%s" % e)
    _stderr("[guard][warn] 文本 %d 字符 > 建议值 %d，已留档（%s）" % (n, MAX_TEXT_CHARS, saved))
    return None


def say_verified(text: str, confirm_max_s: float = 120.0, max_resend: int = 1,
                 ensure: bool = True, lock: bool = True, lock_uuid: str | None = None) -> dict:
    if lock:
        with _session_lock(timeout_s=min(LOCK_TIMEOUT_S, max(120.0, confirm_max_s * 4))) as h:
            return _say_verified_impl(text, confirm_max_s, max_resend, ensure, h.get("uuid"))
    return _say_verified_impl(text, confirm_max_s, max_resend, ensure, lock_uuid)


def _say_verified_impl(text: str, confirm_max_s: float, max_resend: int,
                       ensure: bool, lock_uuid: str | None) -> dict:
    _guard_long_text(text)
    if ensure:
        ensure_server()
        br = ensure_bridge()
        if not br.get("ok"):
            return {"ok": False, "error": "bridge 不可用: %s" % br}

    last_err = "未尝试"
    for attempt in range(max_resend + 1):
        req_id = uuid.uuid4().hex
        if not _status_healthy(bridge_status()):
            _stderr("[say_verified] 预检不健康，原地重注入并等待")
            br2 = ensure_bridge(force=True)
            if not br2.get("ok") or not _wait_bridge_ready(25):
                last_err = "原地注入后 25s 内页面仍未就绪"
                _stderr("[say_verified] 第 %d 次预检失败：%s" % (attempt + 1, last_err))
                continue

        if lock_uuid:
            ok_u, cur_u = _ensure_session_uuid(lock_uuid)
            if not ok_u:
                return {"ok": False, "error": "session_drift", "uuid": cur_u, "want": lock_uuid}

        idle_waited = 0.0
        while True:
            try:
                gen_now = bridge_status().get("generating")
            except Exception:
                gen_now = None
            if gen_now is not True or idle_waited >= 30:
                break
            time.sleep(0.5)
            idle_waited += 0.5
        time.sleep(0.30)

        try:
            page = _attach_page()
            if page:
                has_thumb = page.evaluate("""() => {
                    const imgs = [...document.querySelectorAll('img')].map(img => img.src);
                    return imgs.some(src => src.startsWith('blob:') || src.startsWith('data:image'));
                }""")
                n_zombie = page.evaluate("() => document.querySelectorAll('.ds-animated-size-item .ds-loading').length")
                if has_thumb:
                    n_zombie = 0
            else:
                n_zombie = 0
        except Exception:
            n_zombie = 0
            
        if n_zombie:
            _stderr("[say_verified] 残留 %d 个上传中 chip，先清理" % n_zombie)
            if not _wait_bridge_ready(25):
                last_err = "清 zombie chip 后 25s 内页面仍未就绪"
                continue
            clear_attachments()
            time.sleep(1.0)

        st0 = bridge_status()
        gen0 = bool(st0.get("generating"))
        sent0 = int(st0.get("sentReplies") or 0)
        t0 = time.time()
        try:
            with connect(f"ws://{WS_HOST}:{WS_PORT}", open_timeout=5, close_timeout=3) as ws:
                ws.send(json.dumps(
                    {"action": "say", "reqId": req_id, "payload": {"text": text, "reqId": req_id}},
                    ensure_ascii=False))
                ack = None
                try:
                    msg = json.loads(ws.recv(timeout=3))
                    if msg.get("result") in ("said", "dup", "deduped", "empty") or "error" in msg:
                        ack = msg
                except TimeoutError:
                    pass
        except Exception as e:
            return {"ok": False, "error": "WS 发送异常: %s" % e}

        if ack and "error" in ack:
            last_err = ack["error"]
            time.sleep(1.0)
            continue
        if ack and ack.get("result") == "dup":
            return {"ok": False, "error": "say 去重拦截（10秒内重复）", "elapsed_ms": 0}
        if ack and ack.get("result") == "empty":
            return {"ok": False, "error": "空文本，未发送", "elapsed_ms": 0}
        if ack and ack.get("result") == "deduped":
            last_err = "服务端 deduped（未广播），换 req_id 重发"
            time.sleep(0.2)
            continue

        sent_at = None
        last_log = 0.0
        while time.time() - t0 < confirm_max_s:
            time.sleep(0.5)
            st = bridge_status()
            sentN = int(st.get("sentReplies") or 0)
            gen = bool(st.get("generating"))
            if sentN > sent0:
                sent_at = time.time() - t0
                _stderr("[say_verified] 确认信号1：sentReplies %d→%d（%.1fs）" % (sent0, sentN, sent_at))
                break
            if not gen0 and gen:
                sent_at = time.time() - t0
                _stderr("[say_verified] 确认信号2：generating False→True（%.1fs）" % sent_at)
                break
            if gen0 and not gen:
                gen0 = gen
            now = time.time() - t0
            if now - last_log >= 5.0:
                last_log = now
                _stderr("[say_verified] 等待发送确认 %.1fs（sent0=%d gen0=%s 现sent=%d 现gen=%s）" % (now, sent0, gen0, sentN, gen))

        if sent_at is not None:
            try:
                ls = bridge_status().get("lastSend") or {}
                bridge_rid = ls.get("reqId")
            except Exception:
                bridge_rid = None
            return {"ok": True, "sent": True, "confirm_ms": int(sent_at * 1000),
                    "attempt": attempt + 1, "req_id": req_id, "bridge_reqId": bridge_rid,
                    "bridge_matched": (bridge_rid == req_id) if bridge_rid else None,
                    "elapsed_ms": int((time.time() - t0) * 1000)}
        last_err = "%.0fs 内 sentReplies/generating 均无变化（没发出去）" % confirm_max_s
        _stderr("[say_verified] 第 %d 次未确认发出：%s，重发" % (attempt + 1, last_err))
        if attempt < max_resend:
            if not _wait_bridge_ready(25):
                last_err = "重发前页面未就绪"
                continue
            time.sleep(1.0)
    return {"ok": False, "sent": False, "error": last_err}


# ---- 8. 附件 ----
def composer_files(page) -> list[str]:
    try:
        return page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('.ds-animated-size-item').forEach(c => {
                    const im = c.querySelector('img[alt]');
                    const n = im ? im.alt : (c.innerText || '').trim();
                    if (n) out.push(n.replace(/\\s+/g, ' ').slice(0, 200));
                });
                return out.slice(0, 20);
            }"""
        ) or []
    except Exception:
        return []


def _chip_state(page, fname: str) -> dict:
    try:
        return page.evaluate(
            """(n) => {
                const chips = [...document.querySelectorAll('.ds-animated-size-item')];
                const hit = chips.filter(c => {
                    const im = c.querySelector('img[alt]');
                    if (im && im.alt === n) return true;
                    return (c.innerText || '').includes(n);
                });
                if (!hit.length) return {found: false, uploading: false, failed: false, total: chips.length};
                const txts = hit.map(c => c.innerText || '');
                const spin = hit.some(c => !!c.querySelector('.ds-loading'));
                const uploading = spin || txts.some(t => /上传中|uploading/i.test(t));
                const failed = txts.some(t => /失败|error|重试|retry/i.test(t));
                return {found: true, uploading, failed, total: chips.length};
            }""", fname) or {"found": False, "uploading": False, "failed": False, "total": -1}
    except Exception as e:
        return {"found": False, "uploading": False, "failed": False, "total": -1, "exc": str(e)[:80]}


def clear_attachments(page=None) -> dict:
    if page is None:
        page = _attach_page()
    if page is None:
        return {"ok": False, "error": "无 DeepSeek 标签页", "left": -1}
    removed = 0
    for _ in range(5):
        try:
            n = page.evaluate("""() => {
                let n = 0;
                document.querySelectorAll('.ds-animated-size-item').forEach(chip => {
                    const x = [...chip.querySelectorAll('div[tabindex="0"]')].find(d => d.querySelector('.ds-icon') && !d.querySelector('img'));
                    if (x) { x.style.opacity = '1'; x.style.pointerEvents = 'auto'; x.style.zIndex = '9999'; x.click(); n++; }
                });
                return n;
            }""")
        except Exception:
            n = 0
        removed += n
        time.sleep(0.4)
        if page.evaluate("() => document.querySelectorAll('.ds-animated-size-item').length") == 0:
            break
    if page.evaluate("() => document.querySelectorAll('.ds-animated-size-item').length"):
        try:
            page.evaluate("""() => {
                document.querySelectorAll('.ds-animated-size-item').forEach(el => el.remove());
                const ta = document.querySelector('textarea');
                const root = ta ? (ta.closest('form') || ta.parentElement) : null;
                document.querySelectorAll('button').forEach(b => {
                    if (!b.disabled || !b.querySelector('svg')) return;
                    const inComp = root && (root.contains(b) || b.closest('[class*="composer"]'));
                    if (inComp) { b.disabled = false; b.removeAttribute('disabled'); }
                });
            }""")
        except Exception as e:
            return {"ok": False, "error": "clear 兜底异常: %s" % e, "left": -1}
    time.sleep(0.6)
    def _left():
        try: return page.evaluate("() => document.querySelectorAll('.ds-animated-size-item').length")
        except Exception: return -1
    left = _left()
    if left and left > 0:
        try: page.keyboard.press("Escape")
        except Exception: pass
        time.sleep(0.5)
        left = _left()
    return {"ok": left == 0, "removed": removed, "left": left}


def clear_chip(page, fname: str) -> int:
    try:
        n = page.evaluate("""(n) => {
            let removed = 0;
            document.querySelectorAll('.ds-animated-size-item').forEach(chip => {
                const im = chip.querySelector('img[alt]');
                const match = (im && im.alt === n) || (chip.innerText || '').includes(n);
                if (!match) return;
                const x = [...chip.querySelectorAll('div[tabindex="0"]')].find(d => d.querySelector('.ds-icon') && !d.querySelector('img'));
                if (x) { x.style.opacity = '1'; x.style.pointerEvents = 'auto'; x.style.zIndex = '9999'; x.click(); removed++; }
            });
            return removed;
        }""", fname) or 0
    except Exception:
        return 0
    if n: time.sleep(0.4)
    return n


def set_think_mode(target_state: bool):
    """强制设置网页版的深度思考状态（True 为开，False 为关）"""
    page = _attach_page()
    if not page:
        return
        
    for attempt in range(3):
        try:
            btn = page.locator('div.ds-toggle-button', has_text='深度思考').first
            if btn.count() == 0:
                return
            
            class_str = btn.get_attribute('class') or ""
            is_active = '--selected' in class_str
            
            if is_active == target_state:
                return
                
            btn.click(timeout=3000)
            time.sleep(0.6)
        except Exception:
            time.sleep(0.5)
            
    _stderr(f"[set_think_mode] 警告：尝试 {attempt+1} 次后仍未切换至 {target_state}")


def sync_model_state(model_name: str):
    if not model_name:
        return
    name = model_name.lower()
    _stderr(f"[sync_model_state] 收到模型名: {name}")
    if 'pro' in name or 'reasoner' in name:
        set_think_mode(True)
    elif 'flash' in name or 'chat' in name:
        set_think_mode(False)


def send_file(path: str | Path, timeout_s: float = 60.0) -> dict:
    p = Path(path).resolve()
    if not p.exists():
        return {"ok": False, "error": "文件不存在: %s" % p}
    gate = _require_session("send_file")
    if gate:
        return gate
    ensure_server()
    br = ensure_bridge()
    if not br.get("ok"):
        return {"ok": False, "error": "bridge 不可用: %s" % br}
    page = _attach_page()
    if page is None:
        return {"ok": False, "error": "无 DeepSeek 标签页"}

    def _attach_once():
        try:
            inputs = page.locator('input[type="file"]')
            for i in range(inputs.count()):
                try:
                    inputs.nth(i).set_input_files(str(p), timeout=5000)
                    return True, "direct-input#%d" % i
                except Exception:
                    continue
        except Exception:
            pass
        btn_sel = ('button[aria-label*="file" i],button[aria-label*="attach" i],'
                   'button[aria-label*="上传" i],button[title*="file" i],'
                   'button[class*="attach" i],button[class*="upload" i]')
        try:
            btns = page.locator(btn_sel)
            for i in range(btns.count()):
                try:
                    with page.expect_file_chooser(timeout=4000) as fc:
                        btns.nth(i).click(timeout=3000)
                    fc.value.set_files(str(p))
                    return True, "filechooser#%d" % i
                except Exception:
                    continue
        except Exception:
            pass
        return False, None

    fname = p.name
    last_state = {}
    how = None
    for attempt in (1, 2):
        done, how = _attach_once()
        if not done:
            if attempt == 1: continue
            return {"ok": False, "error": "找不到 file input 也点不出 file chooser", "cleared": True}
        wait_time = min(timeout_s, 15.0)
        deadline = time.monotonic() + wait_time
        state = {"found": False, "uploading": False, "failed": False}
        done_upload = False
        while time.monotonic() < deadline:
            has_thumb = False
            try:
                has_thumb = page.evaluate("""() => {
                    const imgs = [...document.querySelectorAll('img')].map(img => img.src);
                    return imgs.some(src => src.startsWith('blob:') || src.startsWith('data:image'));
                }""")
            except Exception:
                pass
                
            if has_thumb:
                done_upload = True
                break
                
            state = _chip_state(page, fname)
            if state.get("failed"): break
            if state.get("found") and not state.get("uploading"):
                done_upload = True
                break
            time.sleep(0.2)
        if done_upload:
            return {"ok": True, "how": how, "file": str(p), "composer_files": composer_files(page)}
        last_state = state
        clear_chip(page, fname)
        time.sleep(1.0)
    err = ("上传失败（chip 报错文本）" if last_state.get("failed")
           else "上传卡死（chip 长期 loading，已清理）" if last_state.get("found")
           else "未出现附件 chip（可能被拒，已清理）")
    return {"ok": False, "how": how, "file": str(p), "error": err, "cleared": True, "state": last_state}


def _wait_uploads_settled(page, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    quiet_since = None
    while time.monotonic() < deadline:
        try:
            busy = page.evaluate("""() => {
                const spinners = [...document.querySelectorAll('.ds-animated-size-item .ds-loading')].filter(el => {
                    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0;
                });
                return spinners.length > 0;
            }""")
        except Exception:
            busy = True
        if not busy:
            if quiet_since is None: quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= 1.0: return True
        else:
            quiet_since = None
        time.sleep(0.5)
    return False


def ask_files(files: list, text: str, timeout_s: float = 180.0, ensure: bool = True, lock: bool = True, model: str = "") -> dict:
    sync_model_state(model)
    if lock:
        with _session_lock(timeout_s=min(LOCK_TIMEOUT_S, max(180.0, timeout_s * 2))) as h:
            return _ask_files_impl(files, text, timeout_s, ensure, h.get("uuid"))
    return _ask_files_impl(files, text, timeout_s, ensure, None)


def _ask_files_impl(files: list, text: str, timeout_s: float, ensure: bool, lock_uuid: str | None) -> dict:
    if files:
        gate = _require_session("ask_files")
        if gate:
            return gate

    def attach_all():
        attached_ok, failed, results = [], [], []
        for f in files:
            r = send_file(f)
            if r.get("ok"):
                attached_ok.append(str(f))
                results.append({"file": str(f), "ok": True, "how": r.get("how")})
                continue
            results.append({"file": str(f), "ok": False, "how": r.get("how"), "error": r.get("error")})
            failed.append(str(f))
            pg = _attach_page()
            try: chips = composer_files(pg) if pg is not None else []
            except Exception: chips = []
            keep = []
            for g in attached_ok:
                gname = Path(g).name
                if any(gname in str(c) for c in chips): keep.append(g)
                elif pg is not None and send_file(g).get("ok"): keep.append(g)
            attached_ok = keep
        return attached_ok, failed, results

    try:
        st_pre = bridge_status()
    except Exception:
        st_pre = None
    if not _status_healthy(st_pre):
        _stderr("[ask_files] 预检不健康，原地注入等就绪")
        br = ensure_bridge(force=True)
        if not br.get("ok") or not _wait_bridge_ready(25):
            return {"ok": False, "error": "bridge 原地修复后 25s 内页面仍未就绪"}

    if lock_uuid:
        ok_u, cur_u = _ensure_session_uuid(lock_uuid)
        if not ok_u:
            return {"ok": False, "error": "session_drift", "uuid": cur_u, "want": lock_uuid}

    attached_ok, failed, results = attach_all()
    note = ""
    if failed:
        names = ", ".join(Path(x).name for x in failed)
        note = "\n\n（注：以下文件未能挂上、未随本消息发送：%s。请基于已有内容回答。）" % names

    pre_reasoning = ""
    if attached_ok:
        pg = _attach_page()
        if pg:
            pre_count, pre_text = _read_last_assistant_from_dom()
            pre_reasoning = _read_reasoning_from_dom()
            sent_at = time.time()
            try:
                pg.locator('textarea').fill(text + note)
                time.sleep(0.5)
                
                # 1. 死等按钮不禁用（避免图片上传时按钮还在 loading）
                for _ in range(200):
                    btn_state = pg.evaluate("""() => {
                        const btns = [...document.querySelectorAll('div[role="button"]')];
                        const sendBtn = btns.find(b => b.className.includes('ds-button--circle'));
                        if (!sendBtn) return 'not_found';
                        if (sendBtn.className.includes('ds-button--disabled')) return 'disabled';
                        return 'ready';
                    }""")
                    if btn_state == 'ready':
                        break
                    time.sleep(0.1)
                
                # 2. 点击发送
                pg.evaluate("""() => {
                    const btns = [...document.querySelectorAll('div[role="button"]')];
                    const sendBtn = btns.find(b => b.className.includes('ds-button--circle'));
                    if (sendBtn && !sendBtn.className.includes('ds-button--disabled')) sendBtn.click();
                }""")
                
                # 3. 监控是否发出：按钮再次变为禁用，或出现 Stop 图标，或输入框清空
                sent_confirmed = False
                for _ in range(50):
                    time.sleep(0.1)
                    state = pg.evaluate("""() => {
                        const btns = [...document.querySelectorAll('div[role="button"]')];
                        const sendBtn = btns.find(b => b.className.includes('ds-button--circle'));
                        const ta = document.querySelector('textarea');
                        const empty = ta && (ta.value || '').trim().length === 0;
                        const disabled = sendBtn && sendBtn.className.includes('ds-button--disabled');
                        const isGenerating = !!document.querySelector('div.ds-loading, .ds-icon-stop');
                        return { empty, disabled, isGenerating };
                    }""")
                    if state.get("empty") or state.get("disabled") or state.get("isGenerating"):
                        sent_confirmed = True
                        break
                
                if not sent_confirmed:
                    pg.keyboard.press("Enter")
                    time.sleep(0.5)
                
                r = wait_answer_done(sent_at, timeout_s=timeout_s, expect_uuid=lock_uuid, pre_text=pre_text, pre_reasoning=pre_reasoning)
            except Exception as e:
                r = {"ok": False, "error": f"playwright_send_error: {e}"}
        else:
            r = _ask_impl(text + note, timeout_s, 0, ensure, lock_uuid)
    else:
        r = _ask_impl(text + note, timeout_s, 0, ensure, lock_uuid)

    r["files"] = results
    if failed: r["files_failed"] = failed
    return r


# ---- 9. 主入口 ----
def ask(text: str, timeout_s: float = 90.0, expect_words: int = 0, ensure: bool = True, lock: bool = True, model: str = "") -> dict:
    sync_model_state(model)
    # 🔴 彻底禁用超长自动转附件逻辑，交由 index.mjs 强制截断
    # if len(text) > MAX_TEXT_CHARS:
    #     _stderr("[ask] 文本超长(%d > %d)，自动落盘并挂附件发送" % (len(text), MAX_TEXT_CHARS))
    #     out_dir = AGENT_DIR / "logs" / "overflow"
    #     out_dir.mkdir(parents=True, exist_ok=True)
    #     fpath = out_dir / ("context_%d.txt" % int(time.time()))
    #     fpath.write_text(text, encoding="utf-8")
    #     short_prompt = "由于上下文超长，我已将完整内容作为附件发送。请读取该 .txt 附件，并按照其中的最新要求继续任务。"
    #     return ask_files([str(fpath)], short_prompt, timeout_s=timeout_s, ensure=ensure, lock=lock)

    if lock:
        with _session_lock(timeout_s=min(LOCK_TIMEOUT_S, max(120.0, timeout_s * 4))) as h:
            return _ask_impl(text, timeout_s, expect_words, ensure, h.get("uuid"))
    return _ask_impl(text, timeout_s, expect_words, ensure, None)

def _ask_impl(text: str, timeout_s: float, expect_words: int, ensure: bool, lock_uuid: str | None) -> dict:
    last = None
    for _attempt in range(3):
        last = _ask_once(text, timeout_s, expect_words, ensure, lock_uuid)
        if last.get("ok") or not last.get("need_retry"):
            break
        _stderr("[ask] 第 %d 次生成异常（%s），重试" % (_attempt + 1, last.get("error")))
        time.sleep(1.5)

    if last and last.get("ok") and isinstance(last.get("text"), str):
        _t = last["text"]
        if "&lt;" in _t or "</｜｜DSML｜｜" in _t:
            _t = (_t.replace("&lt;", "<").replace("&gt;", ">")
                    .replace("&quot;", chr(34)).replace("&#39;", chr(39))
                    .replace("&amp;", "&"))
            import re as _re
            _t = _re.sub(r"</?｜｜DSML｜｜[^>]*>", "", _t)
            _o = _t.count("<tool_call>")
            _c = _t.count("</tool_call>")
            if _o > _c:
                _t += "</tool_call>" * (_o - _c)
            last["text"] = _t
    return last


def _ask_once(text: str, timeout_s: float, expect_words: int, ensure: bool, lock_uuid: str | None) -> dict:
    pre_count, pre_text = _read_last_assistant_from_dom()
    pre_reasoning = _read_reasoning_from_dom()
    
    r = say_verified(text, ensure=ensure, lock=False, lock_uuid=lock_uuid)
    if not r.get("sent"):
        return {"ok": False, "error": r.get("error"), "elapsed_ms": r.get("elapsed_ms", 0),
                "req_id": r.get("req_id"), "bridge_matched": r.get("bridge_matched")}
                
    sent_at = time.time() - r.get("confirm_ms", 0) / 1000
    out = wait_answer_done(sent_at, timeout_s=timeout_s, req_id=r.get("req_id"), expect_uuid=lock_uuid, pre_text=pre_text, pre_reasoning=pre_reasoning)
    out["req_id"] = r.get("req_id")
    out["bridge_reqId"] = r.get("bridge_reqId")
    out["bridge_matched"] = r.get("bridge_matched")
    return out


def dbg() -> dict:
    out = {}
    try:
        with connect(f"ws://{WS_HOST}:{WS_PORT}", open_timeout=5, close_timeout=3) as ws:
            ws.send(json.dumps({"action": "relay", "payload": {"action": "dbg"}}))
            for _ in range(8):
                try: msg = json.loads(ws.recv(timeout=3))
                except TimeoutError: break
                if "ver" in msg or "xhrDiag" in msg:
                    out = msg; break
    except Exception:
        pass
    if not out and (AGENT_DIR / "logs" / "bridge_dbg.json").exists():
        try: out = json.loads((AGENT_DIR / "logs" / "bridge_dbg.json").read_text(encoding="utf-8"))
        except Exception: pass
    return out


if __name__ == "__main__":
    _stderr("server:", ensure_server())
    _stderr("bridge:", ensure_bridge())
    _stderr("ask:", json.dumps(ask("ping，请只回pong两个字", timeout_s=60), ensure_ascii=False))