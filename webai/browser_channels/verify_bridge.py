"""用 P 通道打开 DeepSeek，验证 bridge v1.15 连上。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_channels.scheduler import Scheduler, Channel
from browser_channels.channels.playwright_ch import PlaywrightAttachChannel


def main():
    p_ch = PlaywrightAttachChannel(port=9222)
    s = Scheduler(playwright=p_ch)
    s.switch_to(Channel.P, "打开 DeepSeek 验证 bridge")

    r = s.execute("open", "https://chat.deepseek.com")
    print(f"[P] open: ok={r.ok}")
    time.sleep(8)

    p = s.channels[Channel.P]
    if p._page:
        result = p._page.evaluate("""() => {
            const bridge = typeof __BG_BRIDGE !== 'undefined' ? __BG_BRIDGE : null;
            return JSON.stringify({
                hasBridge: !!bridge,
                version: bridge ? bridge.version : null,
                status: bridge ? bridge.status() : null
            });
        }""")
        print(f"[P] bridge: {result}")

    # 检查日志
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs', 'executor.log')
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print("\n[日志最后5行]")
        for line in lines[-5:]:
            print(f"  {line.rstrip()}")


if __name__ == "__main__":
    main()
