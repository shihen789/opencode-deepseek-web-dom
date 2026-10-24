# -*- coding: utf-8 -*-
"""注册 N 通道 Native Messaging Host 清单（native.py 从该路径读取）。"""
import json
import os
import sys

HOST_NAME = "com.deepseek_agent.bridge"
HERE = os.path.dirname(os.path.abspath(__file__))
HOST_PY = os.path.join(HERE, "native_host.py")
MANIFEST_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\NativeMessagingHosts")
MANIFEST_PATH = os.path.join(MANIFEST_DIR, f"{HOST_NAME}.json")

manifest = {
    "name": HOST_NAME,
    "description": "DeepSeek agent browser bridge (N channel)",
    "type": "stdio",
    "path": [sys.executable, HOST_PY],
    "allowed_origins": ["extension://liilgpjgabokdklappibcjfablkpcekh"],
}

os.makedirs(MANIFEST_DIR, exist_ok=True)
with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print("registered:", MANIFEST_PATH)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
