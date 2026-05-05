"""使用浏览器截取网页截图并保存到本地文件。

适用于对已授权访问或公开的页面做存档式截图。自动化访问第三方站点（如 Instagram）
仍须遵守该平台服务条款与当地法律；若页面要求登录，截图可能是登录墙而非完整主页。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from lib.playwright import wait_until_meaningful_paint
from lib.session import open_stealth_session


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description='截取指定 URL 的浏览器截图（默认视口；可选用整页）。'
    )
    parser.add_argument(
        'url',
        nargs='?',
        default='https://shop.supreme.com/collections/t-shirts',
        help='要打开的网页地址',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=None,
        help='保存路径（默认：当前目录下带时间戳的 PNG）',
    )
    parser.add_argument(
        '--full-page',
        action='store_true',
        help='截取完整可滚动页面（否则仅当前视口）',
    )
    parser.add_argument(
        '--width',
        type=int,
        default=1280,
        help='浏览器视口宽度（像素）',
    )
    parser.add_argument(
        '--height',
        type=int,
        default=720,
        help='浏览器视口高度（像素）',
    )
    parser.add_argument(
        '--wait-ms',
        type=int,
        default=6000,
        help='在“有内容”判定之后额外等待的毫秒数（SPA 可适当增大，默认 6000）',
    )
    parser.add_argument(
        '--headed',
        action='store_true',
        help='显示浏览器窗口（调试时使用）',
    )
    parser.add_argument(
        '--browser-channel',
        choices=('auto', 'chromium', 'chrome', 'msedge'),
        default='auto',
        help=(
            'Chromium 来源：auto=先试 Playwright 自带 Chromium，失败再用本机 Chrome/Edge；'
            'chromium=仅自带（需先执行 playwright install chromium）；'
            'chrome / msedge=使用已安装的 Chrome 或 Edge。'
        ),
    )
    return parser.parse_args()


def default_output_path(url: str) -> Path:
    """根据 URL 与时间戳生成默认输出文件名。"""
    slug = url.rstrip('/').split('/')[-1] or 'page'
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in slug)[:80]
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return Path(f'{safe}_{stamp}.png')


def capture_screenshot(
    url: str,
    output: Path,
    *,
    full_page: bool,
    width: int,
    height: int,
    wait_ms: int,
    headed: bool,
    browser_channel: str,
) -> None:
    """启动 Chromium，打开 URL，保存截图。"""
    with open_stealth_session(
        headed=headed,
        browser_channel=browser_channel,
        width=width,
        height=height,
    ) as s:
        page = s.page
        page.goto(url, wait_until='domcontentloaded', timeout=90_000)
        wait_until_meaningful_paint(page, page.url, wait_ms)
        page.screenshot(path=str(output), full_page=full_page)


def main() -> None:
    """入口：解析参数并执行截图。"""
    args = parse_args()
    out = args.output or default_output_path(args.url)
    out.parent.mkdir(parents=True, exist_ok=True)
    capture_screenshot(
        args.url,
        out,
        full_page=args.full_page,
        width=args.width,
        height=args.height,
        wait_ms=args.wait_ms,
        headed=args.headed,
        browser_channel=args.browser_channel,
    )
    print(f'Saved: {out.resolve()}')


if __name__ == '__main__':
    main()
