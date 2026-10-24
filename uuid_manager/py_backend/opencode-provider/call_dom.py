#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""opencode web-dom provider 的 Python 桥（Phase 2：带会话动作）。"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

_STDERR_BUF = io.StringIO()


def _stderr_tail(n: int = 1500) -> str:
    try:
        return _STDERR_BUF.getvalue()[-n:]
    except Exception:
        return ""

ROOT = Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
for _p in (str(PROJ), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def _log(*parts) -> None:
    try:
        d = Path(__file__).resolve().parent / "logs"
        d.mkdir(parents=True, exist_ok=True)
        line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] " + " ".join(
            p if isinstance(p, str) else json.dumps(p, ensure_ascii=False) for p in parts
        )
        with open(d / "bridge.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _dump_prompt(prompt: str) -> None:
    try:
        d = Path(__file__).resolve().parent / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "last_prompt.txt", "w", encoding="utf-8", newline="") as fh:
            fh.write(prompt)
    except Exception:
        pass


def _load_backend(name: str):
    import ai_call
    if name == "qwen":
        return ai_call._load_qwen()
    return ai_call._load_deepseek()


MAX_PROMPT_CHARS = 50000

LIMIT_PATTERNS = (
    "对话已达上限", "对话已达", "达到上限", "已达上限",
    "内容过长", "上下文过长", "超出长度", "超过长度",
    "请开启新对话", "请开始新对话", "开启新对话", "开始新对话",
    "新建对话", "消息过长", "too long", "context length",
    "limit_reached", "session_drift",
)


def _is_limit_error(text: str) -> bool:
    t = (text or "").lower()
    for p in LIMIT_PATTERNS:
        if p.lower() in t:
            return True
    return False


def _do_ask(mod, files, prompt, timeout_s, model):
    supports_model = hasattr(mod, 'ask_files') and 'model' in getattr(mod.ask_files, '__code__', type('x', (), {'co_varnames': ()})).co_varnames
    kw = {'model': model} if supports_model else {}
    if files:
        return mod.ask_files(files, prompt, timeout_s=timeout_s, **kw)
    return mod.ask(prompt, timeout_s=timeout_s, **kw)


def main() -> None:
    try:
        req = json.loads(sys.stdin.read().lstrip('\ufeff'))
    except Exception as exc:
        _emit({"ok": False, "text": "", "uuid": None, "error": f"bad stdin json: {exc}"})
        return

    sys.stderr = _STDERR_BUF

    action = req.get("action") or "ask"
    backend = req.get("backend") or None
    be = backend or "qwen"
    prompt = _normalize_newlines(req.get("prompt") or "")
    files = [str(p) for p in (req.get("files") or [])]
    timeout_s = float(req.get("timeout_s") or 300)
    uuid_req = req.get("uuid") or None
    model_req = req.get("model") or ""

    try:
        _log("REQ", {"action": action, "backend": be, "uuid_req": uuid_req,
                     "files": len(files), "prompt_chars": len(prompt)})
        _dump_prompt(prompt)

        if action == "current_uuid":
            if be != "deepseek":
                _emit({"ok": True, "text": "", "uuid": None, "error": None})
                return
            saved = None
            try:
                sm_path = Path(__file__).resolve().parent / "state" / "session_map.json"
                if sm_path.exists():
                    sm = json.loads(sm_path.read_text(encoding="utf-8"))
                    saved = (sm.get("__current__") or {}).get("uuid") or None
            except Exception as e:
                _log("read session_map failed", str(e))
            try:
                page_uuid = _load_backend(be).current_uuid()
            except Exception:
                page_uuid = None
            out_uuid = saved or page_uuid
            _emit({"ok": True, "text": "", "uuid": out_uuid, "error": None})
            return

        if action == "get_titles":
            try:
                mod = _load_backend(be)
                page = mod._attach_page()
                if page:
                    titles = page.evaluate("""() => {
                        const result = {};
                        document.querySelectorAll('a[href^="/a/chat/s/"]').forEach(a => {
                            const href = a.getAttribute('href');
                            const uuid = href.split('/').pop();
                            let title = a.innerText.trim();
                            if (!title) {
                                const span = a.querySelector('span');
                                if (span) title = span.innerText.trim();
                            }
                            if (uuid && title) result[uuid] = title;
                        });
                        return result;
                    }""")
                    _emit({"ok": True, "titles": titles})
                else:
                    _emit({"ok": False, "error": "no page"})
            except Exception as e:
                _emit({"ok": False, "error": str(e)})
            return

        if action == "new_session":
            if be != "deepseek":
                _emit({"ok": False, "text": "", "uuid": None, "error": "new_session 仅支持 deepseek"})
                return
            _emit({"ok": True, "text": "", "uuid": _load_backend(be).new_session(), "error": None})
            return

        if action == "goto":
            if be != "deepseek" or not uuid_req:
                _emit({"ok": False, "text": "", "uuid": None, "error": "goto 需要 deepseek + uuid"})
                return
            ok = _load_backend(be).goto_session(uuid_req)
            _emit({"ok": ok, "text": "", "uuid": uuid_req if ok else None, "error": None if ok else "goto_session 失败"})
            return

        mod = _load_backend(be)

        if be == "deepseek":
            if action == "new_session_ask":
                _ok_new = mod.start_new_session()
                _log("new_session_ask ->", {"start_new_session": bool(_ok_new)})
                if not _ok_new:
                    _emit({"ok": False, "text": "", "uuid": None, "error": "new_session 失败（未能开启新对话）"})
                    return
            if uuid_req and action != "new_session_ask":
                try:
                    cur = mod.current_uuid()
                except Exception as _e:
                    _log("current_uuid raised", str(_e))
                    cur = None
                _log("goto check", {"cur": cur, "want": uuid_req})
                if cur != uuid_req:
                    if not mod.goto_session(uuid_req):
                        _log("goto_session 失败，上报 goto_failed", {"cur": cur, "want": uuid_req})
                        _emit({"ok": False, "text": "", "uuid": None, "error": "goto_failed", "bad_uuid": uuid_req})
                        return
                    uuid_req = None

        # 🔴 彻底禁用超长自动转附件逻辑，交由 index.mjs 强制截断
        # if len(prompt) > MAX_PROMPT_CHARS:
        #     d = Path(__file__).resolve().parent / "logs" / "overflow"
        #     d.mkdir(parents=True, exist_ok=True)
        #     fpath = d / f"context_{int(time.time())}.txt"
        #     fpath.write_text(prompt, encoding="utf-8")
        #     files = files + [str(fpath)]
        #     prompt = "由于上下文超长，我已将完整内容作为附件发送。请读取该 .txt 附件，并按照其中的最新要求继续任务。"
        #     _log("prompt 超长，已落盘为附件", str(fpath), "新 files 数:", len(files))

        if files and not prompt.strip():
            prompt = "请分析以下附件。"

        r = _do_ask(mod, files, prompt, timeout_s, model_req)

        retried_new_session = False
        rerr = str(r.get("error") or "")
        if (not r.get("ok")) and be == "deepseek" and _is_limit_error(rerr + " " + str(r.get("text") or "")):
            _log("检测到上限，自动开启新对话后重发全文", {"err": rerr[:300]})
            try:
                if mod.start_new_session():
                    retried_new_session = True
                    r = _do_ask(mod, files, prompt, timeout_s, model_req)
                    _log("上限重发结果", {"ok": bool(r.get("ok")), "err": str(r.get("error"))[:200]})
                else:
                    _log("上限重发失败：start_new_session 返回 False")
            except Exception as _e:
                _log("上限重发异常", str(_e))

        uuid_out = None
        if be == "deepseek":
            try:
                uuid_out = mod.current_uuid()
                _log("current_uuid after ask ->", repr(uuid_out))
            except Exception as _e:
                _log("current_uuid after ask RAISED", str(_e))
                uuid_out = None

        _log("DONE", {"ok": bool(r.get("ok")), "text_chars": len(r.get("text") or ""),
                      "uuid_out": uuid_out, "error": r.get("error"), "retried_new_session": retried_new_session})
        _tail = _stderr_tail()
        if _tail:
            _log("STDERR tail", _tail.replace("\n", " | ")[-1200:])
        _err = r.get("error")
        if not r.get("ok") and _tail:
            _err = f"{_err or 'ask failed'} || stderr: {_tail[-800:]}"

        def _unescape_tc(v):
            import re
            if not isinstance(v, str):
                return v
            if "&lt;" not in v and "｜｜DSML" not in v:
                return v
            o = (v.replace("&lt;", chr(60)).replace("&gt;", chr(62))
                  .replace("&quot;", chr(34)).replace("&#39;", chr(39))
                  .replace("&amp;", "&"))
            o = re.sub(r"</?｜｜DSML｜｜[^>]*>", "", o)
            no = o.count("<tool_call>")
            nc = o.count("</tool_call>")
            if no > nc:
                o += "</tool_call>" * (no - nc)
            return o

        _emit({
            "ok": bool(r.get("ok")),
            "text": _unescape_tc(r.get("text") or ""),
            "uuid": uuid_out,
            "error": _err,
            "elapsed_ms": r.get("elapsed_ms"),
            "reasoning": r.get("reasoning") or "",
            "new_session": retried_new_session,
            "need_retry": bool(r.get("need_retry")),
        })
    except Exception as exc:
        _log("EXC", str(exc), _stderr_tail(800).replace("\n", " | "))
        _emit({"ok": False, "text": "", "uuid": None, "error": f"bridge exception: {exc} || stderr: {_stderr_tail(600)}"})


if __name__ == "__main__":
    main()