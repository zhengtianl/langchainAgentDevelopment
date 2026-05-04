"""在 Supreme 店铺集合页点击商品进入详情并逐张截图。



针对 https://shop.supreme.com 等 Shopify 店铺：在列表页收集 `/products/` 链接，

依次打开并保存 PNG。请合理设置 `--max` 与请求间隔，避免对站点造成压力；遵守

robots 与网站条款。



用法（在项目根目录）::



    python scripts/supreme_collection_click_screenshot.py --max 5 -o out_png

"""



from __future__ import annotations



import argparse

import re

from pathlib import Path

from urllib.parse import urljoin, urlparse



from playwright.sync_api import Error as PlaywrightError

from playwright.sync_api import sync_playwright



from playwright_helpers import launch_chromium, new_stealth_context





def parse_args() -> argparse.Namespace:

    """解析命令行参数。"""

    p = argparse.ArgumentParser(

        description='打开 Supreme 集合页，点击商品（按链接）并保存详情页截图。'

    )

    p.add_argument(

        '--url',

        default='https://shop.supreme.com/collections/t-shirts',

        help='集合页 URL（默认：All 分类）',

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

        help='最多截几张商品详情（默认 8，避免一次请求过多）',

    )

    p.add_argument(

        '--wait-ms',

        type=int,

        default=2500,

        help='进入详情页后额外等待毫秒数，便于图片与价格渲染',

    )

    p.add_argument(

        '--between-ms',

        type=int,

        default=800,

        help='两次打开商品之间的间隔（毫秒），略作节流',

    )

    p.add_argument(

        '--scroll-steps',

        type=int,

        default=4,

        help='列表页向下滚动次数，用于懒加载更多卡片（每次约半屏）',

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

        '--full-page',

        action='store_true',

        help='详情页使用整页长截图',

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





def _slug_from_product_url(href: str) -> str:

    """从商品 URL 生成文件名片段。"""

    path = urlparse(href).path.rstrip('/')

    seg = path.split('/')[-1] or 'product'

    safe = re.sub(r'[^a-zA-Z0-9._-]+', '_', seg)[:120]

    return safe or 'product'





def _dismiss_cookie_banner(page) -> None:

    """尝试关闭常见 Cookie 条，失败则忽略。"""

    candidates = (

        'button:has-text("Accept")',

        'button:has-text("I Accept")',

        'button:has-text("Agree")',

        '[id*="cookie"] button',

        'button[aria-label*="Accept"]',

    )

    for sel in candidates:

        try:

            loc = page.locator(sel).first

            if loc.is_visible(timeout=1500):

                loc.click()

                page.wait_for_timeout(400)

                return

        except PlaywrightError:

            continue





def _scroll_collection_page(page, steps: int, width: int, height: int) -> None:

    """在列表页多次滚动，尽量触发懒加载。"""

    for _ in range(max(0, steps)):

        page.mouse.wheel(0, height // 2)

        page.wait_for_timeout(500)





def _collect_product_hrefs(page, limit: int) -> list[str]:

    """在集合页收集去重后的商品绝对链接。"""

    base_url = page.url

    loc = page.locator('a[href*="/products/"]')

    try:

        loc.first.wait_for(state='attached', timeout=45_000)

    except PlaywrightError:

        return []



    seen: set[str] = set()

    out: list[str] = []

    n = loc.count()

    for i in range(n):

        if len(out) >= limit:

            break

        raw = loc.nth(i).get_attribute('href')

        if not raw or '/products/' not in raw:

            continue

        full = urljoin(base_url, raw)

        # 去掉 query/hash，同一商品只保留一条

        norm = full.split('?')[0].split('#')[0].rstrip('/')

        if norm in seen:

            continue

        seen.add(norm)

        out.append(norm)

    return out





def _wait_product_detail(page, wait_ms: int) -> None:

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





def run(args: argparse.Namespace) -> None:

    """打开集合页，收集链接，依次截图。"""

    out_dir: Path = args.output_dir

    out_dir.mkdir(parents=True, exist_ok=True)



    with sync_playwright() as p:

        browser = launch_chromium(

            p, headed=args.headed, browser_channel=args.browser_channel

        )

        context = new_stealth_context(

            browser, width=args.width, height=args.height

        )

        page = context.new_page()



        page.goto(args.url, wait_until='domcontentloaded', timeout=90_000)

        _dismiss_cookie_banner(page)

        page.wait_for_timeout(1500)

        _scroll_collection_page(

            page, args.scroll_steps, args.width, args.height

        )



        hrefs = _collect_product_hrefs(page, args.max)

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



        print(f'共收集 {len(hrefs)} 个商品链接，开始逐页截图…')

        for idx, href in enumerate(hrefs, start=1):

            slug = _slug_from_product_url(href)

            target = out_dir / f'{idx:02d}_{slug}.png'

            page.goto(href, wait_until='domcontentloaded', timeout=90_000)

            _wait_product_detail(page, args.wait_ms)

            page.screenshot(path=str(target), full_page=args.full_page)

            print(f'  [{idx}/{len(hrefs)}] {target.name}')

            page.wait_for_timeout(args.between_ms)



        context.close()

        browser.close()



    print(f'完成。输出目录: {out_dir.resolve()}')





def main() -> None:

    """CLI 入口。"""

    run(parse_args())





if __name__ == '__main__':

    main()


