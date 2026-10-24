"""通道基类。"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class HealthResult:
    ok: bool
    detail: str
    elapsed_ms: int


@dataclass
class OpResult:
    ok: bool
    data: object = None
    error: str = ""
    elapsed_ms: int = 0


class BaseChannel(abc.ABC):
    code: str
    name: str

    def __init__(self) -> None:
        self._consecutive_timeouts = 0
        self._consecutive_rejects = 0
        self._connected = False

    @abc.abstractmethod
    def health_check(self, timeout_s: float) -> HealthResult: ...

    @abc.abstractmethod
    def do_open(self, url: str, timeout_s: float) -> OpResult: ...

    @abc.abstractmethod
    def do_click(self, selector: str, timeout_s: float) -> OpResult: ...

    @abc.abstractmethod
    def do_type(self, selector: str, text: str, timeout_s: float) -> OpResult: ...

    @abc.abstractmethod
    def do_read(self, timeout_s: float) -> OpResult: ...

    @abc.abstractmethod
    def close_tabs(self) -> None: ...

    def note_timeout(self) -> None:
        self._consecutive_timeouts += 1

    def note_reject(self) -> None:
        self._consecutive_rejects += 1

    def note_success(self) -> None:
        self._consecutive_timeouts = 0
        self._consecutive_rejects = 0

    @property
    def consecutive_timeouts(self) -> int:
        return self._consecutive_timeouts

    @property
    def consecutive_rejects(self) -> int:
        return self._consecutive_rejects
