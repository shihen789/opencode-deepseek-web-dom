# 三通道区别与使用指南（2026-10-16 修订）

## 问题背景
多次混淆 P 通道（Playwright attach CDP）和 N 通道（Native Messaging + 扩展），导致：
- 调用错误的 API
- 浏览器启动参数错误（headless vs headful）
- 功能验证失败

## 三通道核心区别

### P 通道（Playwright attach CDP）
**代码位置**: `browser_channels/channels/playwright_ch.py`

**特征**:
- 使用 Playwright 的 `connect_over_cdp()` 附加到已有浏览器
- CDP 端口：9222
- Profile：`%LOCALAPPDATA%\Microsoft\Edge\User Data\Default`（用户真实 Default profile，带扩展）
- **默认有头模式**（headful，用户可见窗口）
- API：完整 Playwright API（`page.goto()`, `page.click()`, `page.fill()`, `page.inner_text()`）
- 用途：网页自动化、DOM 操作、网页 AI 问答

**启动命令**:
```powershell
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\Users\39841\AppData\Local\Microsoft\Edge\User Data" `
  --profile-directory=Default
```

**识别方法**:
```python
from browser_channels.channels.playwright_ch import PlaywrightAttachChannel
c = PlaywrightAttachChannel()
c._ensure()  # 自动启动 Edge（如有需要）并附加
c.do_open("https://...")  # 导航
c.do_click("selector")    # 点击
c.do_read()               # 读取页面文本
```

### N 通道（Native Messaging + ScriptCat 扩展）
**代码位置**: `browser_channels/native_host.py` + `browser_channels/channels/native_ch.py`

**特征**:
- 使用 WebSocket 直连 CDP，不经过 Playwright
- 依赖 ScriptCat 浏览器扩展注入桥接代码
- CDP 端口：9222
- Profile：用户真实 Default profile
- **默认有头模式**（需要用户可见窗口以显示扩展）
- API：有限 CDP Runtime.evaluate（`click`, `type`, `read`, `open`, `close_tabs`）
- 用途：简单网页操作、扩展桥接测试

**识别方法**:
```python
from browser_channels.native_host import cmd_open, cmd_click, cmd_type, cmd_read
cmd_open("https://...")
cmd_click("#button")
```

### T 通道（CDP 隧道，已废弃）
**代码位置**: `browser_channels/channels/cdp_tunnel.py`

**特征**:
- 裸 CDP WebSocket 连接
- 无 Playwright，无扩展
- 功能最弱，已不再使用

## 常见混淆点与解决方案

### 混淆 1：启动浏览器
- ❌ 错误：用 `native_host.ensure_edge()` 启动后，用 Playwright 附加
- ✅ 正确：Playwright 通道的 `_ensure()` 会自动调用 `native_host.ensure_edge()`，无需手动启动

### 混淆 2：headless vs headful
- ❌ 错误：P 通道默认用 `--headless=new`
- ✅ 正确：**P 通道默认有头**（headful），用户可见窗口。只有明确需要后台运行时才用 headless

### 混淆 3：API 混用
- ❌ 错误：用 `native_host.cmd_*()` 系列函数操作 P 通道
- ✅ 正确：P 通道用 `PlaywrightAttachChannel` 的 `do_*()` 方法

### 混淆 4：扩展依赖
- ❌ 错误：认为 P 通道不需要扩展
- ✅ 正确：P 通道复用用户 Default profile，**自动带有 ScriptCat 扩展**（如果已安装）

## 快速选择指南

| 需求 | 选哪个通道 |
|------|-----------|
| 网页 AI 问答（千问/DeepSeek） | P 通道 |
| 复杂网页自动化（多步操作） | P 通道 |
| 简单点击/输入 | P 通道 或 N 通道 |
| 需要扩展注入（如 ScriptCat 桥接） | N 通道 |
| 后台运行（用户不可见） | P 通道 + `--headless=new` |
| 用户可见窗口 | P 通道（默认） |

## 互斥锁机制
三通道共享互斥锁，同一时刻只能有一个通道持有锁。切换通道前必须先释放旧锁。

## 修订记录
- 2026-10-16：明确 P 通道默认有头，补充快速选择指南

## 教训记录：子 agent 调用通道混淆（2026-10-17，用户指令）

**事故**：派"官方帧 vs 渲染帧对比"的子 agent 时，我连续 3 次 `task` 调用被 abort，用户指出"你又打开 N 通道去了"。

**根因**：我把 `subagent_type` 搞混了——
- `subagent_type=general` / `subagent_type=fl-*` 等 = **API / 本地模型**通道，不是网页 DOM。
- 网页 DOM 类子 agent（千问 / DeepSeek 网页版）= **P 通道**，必须用 `subagent_type=webdom-test`（deepseek 后端）或 `subagent_type=webdom-qwen`（qwen 后端）。

**铁律（写进系统设定，不再犯）**：
1. **默认一切网页操作走 P 通道**：Playwright attach CDP 9222（`browser_channels/channels/playwright_ch.py`），不是 N 通道（`browser_channels/native_host.py` + ScriptCat 扩展）。
2. **派网页 DOM 子 agent = P 通道**：
   - DeepSeek 网页版 → `task(subagent_type="webdom-test", prompt="...")`
   - 千问网页版 → `task(subagent_type="webdom-qwen", prompt="...")`
   - 只有纯 API / 本地模型才用 `general` / `fl-*`。
3. **N 通道的触发条件**：只有 P 通道故障（连续 3 次超时）或确实需要扩展侧能力时才切，不是"感觉不行"就切。
4. **子 agent prompt 必须写"你必须真实调用工具，不许凭记忆回答"**，否则模型可能空回复。
5. 派之前先想清楚：这个 agent 要不要看网页？要 → P 通道；不要 → API。

**参考**：三通道区别见 `browser_channels/CHANNEL_GUIDE.md`（P=Playwright attach CDP 9222 有头；N=Native Messaging+ScriptCat 扩展；T=裸 CDP 已废弃）。