"""关闭本通道打开的临时标签页（edge://extensions），保留用户 DeepSeek 页。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from browser_channels.scheduler import Scheduler, Channel
from browser_channels.channels.playwright_ch import PlaywrightAttachChannel

s = Scheduler(playwright=PlaywrightAttachChannel(port=9222))
s.switch_to(Channel.P, "清理临时页")
for t in s.list_tabs():
    url = t.get('url', '')
    if url.startswith('edge://extensions') or url.startswith('chrome://extensions'):
        ok = s.close_tab(t.get('id') or t.get('targetId', ''))
        print(f"closed {url} -> {ok}")
print("剩余:", [t.get('url', '')[:50] for t in s.list_tabs()])
