"""N 通道：扩展 + Native Messaging。主通道。"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import time

from .base import BaseChannel, HealthResult, OpResult

NATIVE_HOST_NAME = "com.deepseek_agent.bridge"


def _manifest_path() -> str:
    return os.path.expandvars(
        rf"%LOCALAPPDATA%\Microsoft\Edge\User Data\NativeMessagingHosts\{NATIVE_HOST_NAME}.json"
    )


class NativeChannel(BaseChannel):
    code = "N"
    name = "扩展 + Native Messaging"

    def __init__(self, host_cmd: list[str] | None = None) -> None:
        super().__init__()
        self.host_cmd = host_cmd

    def health_check(self, timeout_s: float = 3.0) -> HealthResult:
        t0 = time.monotonic()
        manifest = _manifest_path()
        if not os.path.exists(manifest):
            return HealthResult(False, f"Native Host 清单不存在: {manifest}", int((time.monotonic() - t0) * 1000))
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cmd = cfg.get("path")
            if isinstance(cmd, str):
                cmd = [cmd]
            if not cmd:
                return HealthResult(False, "清单里没有 path", int((time.monotonic() - t0) * 1000))
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                payload = json.dumps({"cmd": "ping"}).encode("utf-8")
                proc.stdin.write(struct.pack("<I", len(payload)) + payload)
                proc.stdin.flush()
                header = proc.stdout.read(4)
                if len(header) < 4:
                    raise ConnectionError("Native Host 无应答")
                size = struct.unpack("<I", header)[0]
                body = proc.stdout.read(size)
                resp = json.loads(body)
                if resp.get("ok") is not True:
                    raise ConnectionError(f"应答异常: {resp}")
                self._connected = True
                self.note_success()
                return HealthResult(True, "ping ok", int((time.monotonic() - t0) * 1000))
            finally:
                proc.terminate()
        except Exception as exc:
            self._connected = False
            return HealthResult(False, f"ping 失败: {exc}", int((time.monotonic() - t0) * 1000))

    def _rpc(self, msg: dict, timeout_s: float) -> OpResult:
        t0 = time.monotonic()
        manifest = _manifest_path()
        if not os.path.exists(manifest):
            self.note_reject()
            return OpResult(False, error="Native Host 未注册", elapsed_ms=0)
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cmd = cfg.get("path")
            if isinstance(cmd, str):
                cmd = [cmd]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            payload = json.dumps(msg).encode("utf-8")
            proc.stdin.write(struct.pack("<I", len(payload)) + payload)
            proc.stdin.flush()
            header = proc.stdout.read(4)
            if len(header) < 4:
                raise ConnectionError("no response")
            size = struct.unpack("<I", header)[0]
            body = proc.stdout.read(size)
            proc.terminate()
            resp = json.loads(body)
            self.note_success()
            return OpResult(bool(resp.get("ok")), resp, "", int((time.monotonic() - t0) * 1000))
        except subprocess.TimeoutExpired:
            self.note_timeout()
            return OpResult(False, error="timeout", elapsed_ms=int((time.monotonic() - t0) * 1000))
        except ConnectionRefusedError as exc:
            self.note_reject()
            return OpResult(False, error=f"rejected: {exc}", elapsed_ms=int((time.monotonic() - t0) * 1000))
        except Exception as exc:
            return OpResult(False, error=str(exc), elapsed_ms=int((time.monotonic() - t0) * 1000))

    def do_open(self, url: str, timeout_s: float = 5.0) -> OpResult:
        return self._rpc({"cmd": "open", "url": url}, timeout_s)

    def do_click(self, selector: str, timeout_s: float = 5.0) -> OpResult:
        return self._rpc({"cmd": "click", "selector": selector}, timeout_s)

    def do_type(self, selector: str, text: str, timeout_s: float = 5.0) -> OpResult:
        return self._rpc({"cmd": "type", "selector": selector, "text": text}, timeout_s)

    def do_read(self, timeout_s: float = 5.0) -> OpResult:
        return self._rpc({"cmd": "read"}, timeout_s)

    def close_tabs(self) -> None:
        self._rpc({"cmd": "close_tabs"}, 3.0)
