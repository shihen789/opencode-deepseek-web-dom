# -*- coding: utf-8 -*-
"""网页AI 统一网关（2026-10-15，通用多后端询问入口）。

用法：
    from 网页AI import ai_call            # 或把项目根加进 sys.path 后: import ai_call
    r = ai_call.ask("问题")                # 默认 backend="qwen"
    r = ai_call.ask("问题", backend="deepseek")   # DeepSeek 恢复后随时可用
    r = ai_call.ask_files(["a.py"], "分析这个文件")

结构约定（临时/恢复双态）：
    网页AI/                     通用根目录
      ai_call.py               本网关（后端路由）
      qwen/qwen_call.py        千问后端（P 通道 DOM 收发；DeepSeek 故障期主用）
      deepseek/deepseek_call.py  DeepSeek 本体（C 端故障期隔离于此，恢复即用；
                                 browser_channels/deepseek_call.py 是它的转发 shim，
                                 所有既有引用经 shim 汇入同一模块实例，单例不分裂）
      archive/                 一次性 ask_ds_*.py 归档

同构接口：ask(text) → {ok, text, elapsed_ms}；ask_files(paths, text) 同。
约定：同标签页连续追问（复用会话不清空）；发源码/日志必须整文件直发。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent  # 网页AI/
_PROJ = _ROOT.parent  # 项目根
for _p in (str(_PROJ), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DEFAULT_BACKEND = "deepseek"  # 优先 DeepSeek（千问频繁限流）；故障时可回退 "qwen"


def _load_qwen():
    from qwen import qwen_call

    return qwen_call


def _load_deepseek():
    # 必经 shim：与 40+ 既有引用同一模块实例（避免 _SCHED 等单例分裂）
    from browser_channels import deepseek_call

    return deepseek_call


_BACKENDS = {"qwen": _load_qwen, "deepseek": _load_deepseek}


def _pick(backend: str | None):
    name = backend or DEFAULT_BACKEND
    loader = _BACKENDS.get(name)
    if loader is None:
        raise ValueError(f"unknown backend: {name!r}（可选: {sorted(_BACKENDS)}）")
    return name, loader()


def ask(text: str, backend: str | None = None, timeout_s: float = 90.0) -> dict:
    try:
        name, mod = _pick(backend)
    except Exception as exc:
        return {"ok": False, "text": "", "error": str(exc)}
    try:
        return mod.ask(text, timeout_s=timeout_s)
    except Exception as exc:
        return {"ok": False, "text": "", "error": f"{name} ask failed: {exc}"}


def ask_files(paths, text: str = "", backend: str | None = None, timeout_s: float = 120.0) -> dict:
    try:
        name, mod = _pick(backend)
    except Exception as exc:
        return {"ok": False, "text": "", "error": str(exc)}
    try:
        return mod.ask_files(paths, text, timeout_s=timeout_s)
    except Exception as exc:
        return {"ok": False, "text": "", "error": f"{name} ask_files failed: {exc}"}


def available() -> dict:
    """后端注册表与默认值（可用性实测见各后端 dbg/ensure_page）。"""
    return {"default": DEFAULT_BACKEND, "backends": sorted(_BACKENDS)}


if __name__ == "__main__":
    import json

    print(json.dumps(available(), ensure_ascii=False))
    print(json.dumps(ask("回复两个字：收到"), ensure_ascii=False))
