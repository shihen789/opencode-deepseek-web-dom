# opencode-deepseek-web-dom

本项目利用浏览器自动化（Playwright CDP 通道）抓取网页版大模型（如 DeepSeek、千问）的对话 DOM，将其包装为 opencode 的 Provider。

## 包含内容

- 'index.mjs' / 'provider/'：opencode 的 Provider 接入代码。
- 'webai/'：网页 AI 底层通信与通道管理（包含 CDP 调度器、网络桥接与前后端接口）。
- 'uuid_manager/'：基于 Electron 的本地会话管理软件，用于同步和锁定网页端 UUID。
- 'bridge/'：相关的浏览器 UserScript 桥接脚本。

## 更新说明 (v2.0)

- 修复了会话游标（cursor）不存在时的异常崩溃问题。
- 移除了原本在文本过长时自动生成 .txt 附件的逻辑，改为直接在内存中截断。
- 取消了发生错误时向模型强制注入提示语的行为，保持对话上下文干净。
- 补充了图片发送处理和 todowrite/todoread 工具的支持。
- 规范了 uuid_manager 的路径管理。

## 使用前置要求

1. 本机安装 Python 3.9+、Node.js 18+，以及相关依赖（'pip install -r requirements.txt'）。
2. 启动浏览器并开启 CDP 调试端口（'--remote-debugging-port=9222'）。
3. 在 opencode.jsonc 中配置本 Provider，并指向对应的启动脚本。

## 注意事项

请确保网页端的界面结构未发生大幅变动，否则需更新 DOM 选择器。本项目仅供学习与本地环境自动化测试使用。
