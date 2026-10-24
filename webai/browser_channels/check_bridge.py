"""用 P 通道在 DeepSeek 页面执行 JS 检查 bridge 状态。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_channels.scheduler import Scheduler, Channel
from browser_channels.channels.playwright_ch import PlaywrightAttachChannel


def main():
    p_ch = PlaywrightAttachChannel(port=9222)
    s = Scheduler(playwright=p_ch)
    s.switch_to(Channel.P, "检查 bridge 状态")

    # 确保在 DeepSeek 页面
    r = s.execute("open", "https://chat.deepseek.com")
    print(f"[P] open: ok={r.ok}")
    time.sleep(5)

    # 用 evaluate 执行 JS 检查 bridge
    p = s._current and s.channels[Channel.P]
    if p and p._page:
        result = p._page.evaluate("""() => {
            const bridge = typeof __BG_BRIDGE !== 'undefined' ? __BG_BRIDGE : null;
            const status = bridge ? bridge.status() : null;
            return JSON.stringify({
                url: location.href,
                title: document.title,
                hasBridge: !!bridge,
                status: status
            });
        }""")
        print(f"[P] JS result: {result}")
    else:
        print("[P] 无 page 对象")


if __name__ == "__main__":
    main()
