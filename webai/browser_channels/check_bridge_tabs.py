"""用 P 通道检查 bridge 状态 + 标签页管理。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_channels.scheduler import Scheduler, Channel
from browser_channels.channels.playwright_ch import PlaywrightAttachChannel


def main():
    p_ch = PlaywrightAttachChannel(port=9222)
    s = Scheduler(playwright=p_ch)
    s.switch_to(Channel.P, "检查 bridge + 标签页管理")

    # 标签页管理：枚举当前所有标签页
    import requests
    r = requests.get('http://127.0.0.1:9222/json/list', timeout=3)
    tabs = r.json()
    page_tabs = [t for t in tabs if t['type'] == 'page']
    print(f"[标签页管理] 当前页面标签页数: {len(page_tabs)}")
    for i, t in enumerate(page_tabs):
        print(f"  [{i}] {t['url'][:80]}")

    # 检查是否有 DeepSeek 标签页
    ds_tab = None
    for t in page_tabs:
        if 'chat.deepseek.com' in t['url']:
            ds_tab = t
            break

    if ds_tab:
        print(f"\n[标签页管理] 复用已有 DeepSeek 标签页: {ds_tab['url'][:60]}")
        # 连接到已有标签页
        p_ch._ensure()
        # 用已有标签页的 CDP target 连接
        import websocket
        ws = websocket.create_connection(ds_tab['webSocketDebuggerUrl'], timeout=5)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": "JSON.stringify({hasBridge: typeof __BG_BRIDGE !== 'undefined', version: typeof __BG_BRIDGE !== 'undefined' ? __BG_BRIDGE.version : null})",
            "returnByValue": True
        }}))
        resp = json.loads(ws.recv())
        bridge_info = resp.get('result', {}).get('result', {}).get('value', '{}')
        print(f"[P] bridge 状态: {bridge_info}")
        ws.close()
    else:
        print("\n[标签页管理] 无 DeepSeek 标签页，新建一个")
        s.execute("open", "https://chat.deepseek.com")
        time.sleep(5)

    # 检查日志
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs', 'executor.log')
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print("\n[日志最后5行]")
        for line in lines[-5:]:
            print(f"  {line.rstrip()}")

    # 回切 N
    print("\n[回切] 尝试切回 N...")
    result = s.revert_to_native()
    if result:
        print(f"[回切] 成功切回 N")
    else:
        print("[回切] N 不可用，保持 P")


if __name__ == "__main__":
    main()
