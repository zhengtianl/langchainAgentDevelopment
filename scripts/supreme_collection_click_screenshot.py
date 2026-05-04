"""在 Supreme 店铺集合页依次打开商品并截图。

输出文件名与 ``supreme_tshirts_download_hd_images.py`` 对齐：``NNN_handle_1.png``。

默认模式：**单张、非整页**——在详情页选取面积最大的 ``cdn.shopify.com`` 商品图（主图
画廊里通常最大的一张），对该 ``<img>`` 做一次 ``screenshot``，不是视口长截图。

- ``--viewport-only``：改为截取**浏览器视口**单帧（仍非整页滚动，除非再加 ``--full-page``）。
- ``--viewport-only --full-page``：才是沿页面滚动的**完整长图**。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from playwright_helpers import launch_chromium, new_stealth_context
from supreme_shop_common import (
    COLLECTION_DEFAULT_TSHIRTS,
    collect_product_urls,
    dismiss_cookie_banner,
    safe_filename,
    scroll_collection_page,
    slug_from_product_url,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description='Supreme 集合页依次打开商品并截图（文件名与 HD 下载脚本一致）。'
    )
    p.add_argument(
        '--url',
        default=COLLECTION_DEFAULT_TSHIRTS,
        help='集合页 URL（默认：t-shirts）',
    )
    p.add_argument(
        '-o',
        '--output-dir',
        type=Path,
        default=Path('supreme_product_screens'),
        help='截图输出目录',
    )
    p.add_argument(
        '--max',
        type=int,
        default=8,
        help='最多处理几件商品；0 表示不限制（默认 8）',
    )
    p.add_argument(
        '--wait-ms',
        type=int,
        default=2500,
        help='进入详情页后额外等待毫秒数',
    )
    p.add_argument(
        '--between-ms',
        type=int,
        default=800,
        help='两件商品之间的间隔（毫秒）',
    )
    p.add_argument(
        '--scroll-rounds',
        type=int,
        default=30,
        help='列表页向下滚动轮数（与 HD 下载脚本一致，默认 30）',
    )
    p.add_argument(
        '--viewport-only',
        action='store_true',
        help='截取当前视口（单帧），不要最大的商品图元素',
    )
    p.add_argument(
        '--full-page',
        action='store_true',
        help='仅在与 --viewport-only 同时使用时生效：滚动拼接整页长图（默认关闭）',
    )
    p.add_argument(
        '--width',
        type=int,
        default=1280,
        help='视口宽度',
    )
    p.add_argument(
        '--height',
        type=int,
        default=900,
        help='视口高度',
    )
    p.add_argument(
        '--headed',
        action='store_true',
        help='显示浏览器窗口（调试）',
    )
    p.add_argument(
        '--browser-channel',
        choices=('auto', 'chromium', 'chrome', 'msedge'),
        default='auto',
        help='同 save_page_screenshot.py',
    )
    return p.parse_args()


def _wait_product_detail(page: Page, wait_ms: int) -> None:
    """等待详情页出现标题或主图。"""
    try:
        page.wait_for_function(
            """() => {
              const h = document.querySelector('h1');
              const img = document.querySelector(
                'main img[src], [data-product] img, .product img'
              );
              return (h && h.textContent && h.textContent.trim().length > 0)
                || (img && img.naturalWidth > 0);
            }""",
            timeout=40_000,
        )
    except PlaywrightError:
        pass
    page.wait_for_timeout(wait_ms)


_LARGEST_SHOPIFY_IMG_INDEX_JS = r"""
() => {
  const nodes = [...document.querySelectorAll('img[src*="cdn.shopify.com"]')];
  let bestI = -1;
  let bestA = 0;
  nodes.forEach((img, i) => {
    if (!img.offsetParent) return;
    const r = img.getBoundingClientRect();
    if (r.width < 32 || r.height < 32) return;
    const w = img.naturalWidth || r.width;
    const h = img.naturalHeight || r.height;
    const a = w * h;
    if (a > bestA) {
      bestA = a;
      bestI = i;
    }
  });
  return bestI;
}
"""


def _screenshot_detail(
    page: Page,
    target: Path,
    *,
    viewport_only: bool,
    full_page: bool,
) -> None:
    """单张截图：默认最大商品图元素；视口模式则整帧或（可选）整页长图。"""
    if viewport_only:
        # 仅视口单帧，或显式要求时再整页长图
        page.screenshot(path=str(target), full_page=full_page)
        return

    idx = page.evaluate(_LARGEST_SHOPIFY_IMG_INDEX_JS)
    if idx >= 0:
        loc = page.locator('img[src*="cdn.shopify.com"]').nth(idx)
        try:
            loc.wait_for(state='visible', timeout=15_000)
            loc.screenshot(path=str(target))
            return
        except PlaywrightError:
            pass

    # 找不到可用商品图时：只截视口一帧，绝不默认长滚动整页
    page.screenshot(path=str(target), full_page=False)


def run(args: argparse.Namespace) -> None:
    """打开集合页，收集链接，依次截图。"""
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    max_products = None if args.max == 0 else args.max

    # 截主图时使用较高 device_scale_factor，PNG 更清晰（与 HD「看清细节」一致）
    dsf = 1.0 if args.viewport_only else 2.0

    with sync_playwright() as p:
        browser = launch_chromium(
            p, headed=args.headed, browser_channel=args.browser_channel
        )
        context = new_stealth_context(
            browser,
            width=args.width,
            height=args.height,
            device_scale_factor=dsf,
        )
        page = context.new_page()

        page.goto(args.url, wait_until='domcontentloaded', timeout=90_000)
        dismiss_cookie_banner(page)
        page.wait_for_timeout(1500)
        scroll_collection_page(page, args.scroll_rounds, args.height)

        hrefs = collect_product_urls(page, max_products)
        if not hrefs:
            shot = out_dir / '_collection_empty.png'
            page.screenshot(path=str(shot), full_page=False)
            context.close()
            browser.close()
            print(
                '未找到 a[href*="/products/"] 链接。可能选择器与主题不符，'
                f'已保存列表页截图: {shot.resolve()}'
            )
            return

        if args.full_page and not args.viewport_only:
            print(
                '提示: --full-page 仅在与 --viewport-only 同时使用时有效；'
                '当前仍按「单张最大商品图」输出。'
            )
        print(
            f'共收集 {len(hrefs)} 个商品链接，开始截图（单张最大商品图 / 非整页长图；'
            f'NNN_handle_1.png）…'
        )

        for idx, href in enumerate(hrefs, start=1):
            handle = slug_from_product_url(href)
            target = out_dir / f'{idx:03d}_{safe_filename(handle)}_1.png'

            page.goto(href, wait_until='domcontentloaded', timeout=90_000)
            _wait_product_detail(page, args.wait_ms)
            _screenshot_detail(
                page,
                target,
                viewport_only=args.viewport_only,
                full_page=args.full_page,
            )
            print(f'  [{idx}/{len(hrefs)}] {target.name}')
            page.wait_for_timeout(args.between_ms)

        context.close()
        browser.close()

    print(f'完成。输出目录: {out_dir.resolve()}')


def main() -> None:
    run(parse_args())


if __name__ == '__main__':
    main()
