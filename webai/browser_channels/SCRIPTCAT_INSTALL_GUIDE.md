# ScriptCat 脚本部署方式（教训记录 · 2026-10-17）

## 问题

把 `ds_bg_bridge.user.js` **直接粘贴进 ScriptCat 编辑器**（本地脚本型），脚本在页面里
**不生效 / 不持久 / 不自动更新**。表现：

- 页面重载后 `__BG_BRIDGE` 消失，需要 provider 每次 `ensure_bridge()` 手动 reload + 注入
- 改了本地文件，浏览器里的副本不会自动同步
- 多个条目重复安装（粘贴一次一条），容易出现双注入

## 正确做法：脚本链接型

ScriptCat 的脚本必须以 **URL 链接** 的形式安装，才能在网页里真正生效并持久化：

1. 用一个本地 HTTP 服务器提供脚本文件（例如端口 `8899` 或 `8766`，`--directory scripts/`）
2. 在 ScriptCat 选项页用 URL 安装（或「导入脚本」→ URL）
3. 脚本头里的 `@updateURL` / `@downloadURL` 指向这个本地服务器
4. ScriptCat 会定期检查更新，文件改了自动拉取、自动生效

```bash
# 最小必须：起一个本地静态服务，把脚本以链接形式提供给 ScriptCat
python -m http.server 8899 --directory C:\Users\39841\PyCharmMiscProject\deepseek-agent\scripts
# 然后在 ScriptCat 里安装：http://localhost:8899/ds_bg_bridge.user.js
```

## 本地脚本 vs 链接型

| | 本地脚本（粘贴） | 脚本链接型（URL） |
|---|---|---|
| 页面生效 | ❌ 不稳定，重载易丢 | ✅ 持久、自动注入 |
| 更新 | ❌ 改文件不同步 | ✅ 改文件自动拉取 |
| 安装方式 | 编辑器粘贴 | URL 安装 / 导入 |
| 用途 | 开发调试 | **生产部署（唯一正确方式）** |

## 最小必须（缺一不可）

1. 本地 HTTP 服务（端口固定，如 8899），提供 `ds_bg_bridge.user.js`
2. ScriptCat 里该脚本以 **URL 链接型** 安装（`@updateURL`/`@downloadURL` 指向该服务）
3. 脚本文件本身（开发源码，放在 `deepseek-agent/scripts/`）

## 当前状态

- `deepseek-agent/scripts/ds_bg_bridge.user.js` = v1.26（开发源码，已含 CRLF 归一 + 归一后比较）
- **尚未以链接型部署**：目前是把代码写进 `chrome.storage.local`（等效于粘贴），需改为 URL 安装
- provider 的 `ensure_bridge()` 仍保留为兜底（自动 reload + 注入），但不应作为常态依赖

## 后续动作

- 起 `python -m http.server 8899 --directory scripts/`（或写进启动脚本）
- 在 ScriptCat 选项页用 URL 安装 `http://localhost:8899/ds_bg_bridge.user.js`（链接型）
- 删除 `chrome.storage.local` 里的粘贴副本，避免双注入
- 验证：新开 DeepSeek 页 → `__BG_BRIDGE.version` 应为 1.26 且自动出现