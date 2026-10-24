"""T 通道：CDP 隧道。备 1。"""
from __future__ import annotations

import json
import time

import requests
import websocket

from .base import BaseChannel, HealthResult, OpResult


class CdpTunnelChannel(BaseChannel):
    code = "T"
    name = "CDP 隧道"

    def __init__(self, port: int = 9222) -> None:
        super().__init__()
        self.port = port
        self._ws = None
        self._session_id: str | None = None
        self._target_id: str | None = None

    def health_check(self, timeout_s: float = 5.0) -> HealthResult:
        t0 = time.monotonic()
        try:
            r = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=timeout_s)
            info = r.json()
        except Exception as exc:
            return HealthResult(False, f"端口探测失败: {exc}", int((time.monotonic() - t0) * 1000))
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            return HealthResult(False, "无 webSocketDebuggerUrl", int((time.monotonic() - t0) * 1000))
        try:
            ws = websocket.create_connection(ws_url, timeout=timeout_s, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
            resp = json.loads(ws.recv())
            if "result" not in resp:
                ws.close()
                return HealthResult(False, f"空操作异常: {resp}", int((time.monotonic() - t0) * 1000))
            ws.close()
            self._connected = True
            self.note_success()
            return HealthResult(True, "port+空操作 ok", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            self._connected = False
            return HealthResult(False, f"WS 失败: {exc}", int((time.monotonic() - t0) * 1000))

    def _connect(self) -> None:
        if self._ws is not None:
            return
        r = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=3)
        ws_url = r.json()["webSocketDebuggerUrl"]
        self._ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)

    def _send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self._connect()
        mid = int(time.monotonic() * 1000) % 1_000_000
        msg: dict = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._ws.send(json.dumps(msg))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            resp = json.loads(self._ws.recv())
            if resp.get("id") == mid:
                return resp
        raise TimeoutError(f"{method} 无响应")

    def _ensure_target(self) -> str:
        if self._target_id:
            return self._target_id
        resp = self._send("Target.createTarget", {"url": "about:blank"})
        self._target_id = resp["result"]["targetId"]
        attached = self._send("Target.attachToTarget", {"targetId": self._target_id, "flatten": True})
        self._session_id = attached["result"]["sessionId"]
        return self._target_id

    def do_open(self, url: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            self._ensure_target()
            self._send("Page.navigate", {"url": url}, session=self._session_id)
            self.note_success()
            return OpResult(True, {"url": url}, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_click(self, selector: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            js = f"document.querySelector({json.dumps(selector)})?.click(); return !!document.querySelector({json.dumps(selector)})"
            resp = self._send("Runtime.evaluate", {"expression": js, "returnByValue": True}, session=self._session_id)
            hit = resp.get("result", {}).get("result", {}).get("value", False)
            self.note_success()
            return OpResult(bool(hit), {"hit": hit}, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_type(self, selector: str, text: str, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            js = (
                f"const el=document.querySelector({json.dumps(selector)}); if(!el) return false; "
                f"el.value={json.dumps(text)}; el.dispatchEvent(new Event('input',{{bubbles:true}})); return true"
            )
            resp = self._send("Runtime.evaluate", {"expression": js, "returnByValue": True}, session=self._session_id)
            ok = resp.get("result", {}).get("result", {}).get("value", False)
            self.note_success()
            return OpResult(bool(ok), None, "" if ok else "元素不存在", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_read(self, timeout_s: float = 10.0) -> OpResult:
        t0 = time.monotonic()
        try:
            resp = self._send("Runtime.evaluate", {"expression": "document.body.innerText.slice(0, 5000)", "returnByValue": True}, session=self._session_id)
            text = resp.get("result", {}).get("result", {}).get("value", "")
            self.note_success()
            return OpResult(True, text, "", int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def close_tabs(self) -> None:
        try:
            if self._target_id:
                self._send("Target.closeTarget", {"targetId": self._target_id})
        except Exception:
            pass
        finally:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass
            self._ws = None
            self._session_id = None
            self._target_id = None
