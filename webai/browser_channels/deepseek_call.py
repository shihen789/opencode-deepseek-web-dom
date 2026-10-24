# -*- coding: utf-8 -*-
"""Shim：DeepSeek 调用本体已移至 `网页AI/deepseek/deepseek_call.py`（2026-10-15 网页AI 结构化隔离）。

保持既有引用不变：
    from browser_channels.deepseek_call import ask, ask_files, ensure_bridge, ...

本 shim 通过 importlib 文件加载转发全部公共/私有接口，
两个后端文件在 `网页AI/{deepseek,qwen}/` 下各自独立隔离，
通用根目录 `网页AI/` 统一保存；DeepSeek 恢复后本体直接可用。
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_TARGET = Path(__file__).resolve().parent.parent / "网页AI" / "deepseek" / "deepseek_call.py"

_spec = spec_from_file_location("webai_deepseek_call", _TARGET)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载 DeepSeek 本体：{_TARGET}（文件缺失？）")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

globals().update({k: v for k, v in vars(_module).items() if not k.startswith("__")})
__all__ = [k for k in dir(_module) if not k.startswith("__")]
