"""从 Supreme T-Shirts 集合下载每件商品的高分辨率产品图。

官方 Storefront JSON（如 ``/products/handle.js``）在部分环境下会返回 403，因此本脚本
在打开**商品详情页**后，从 ``og:image``、JSON-LD、``img``/``srcset`` 中收集
``cdn.shopify.com`` 地址，再为 URL 增加 ``width=`` 参数请求尽量大的图（默认 4096）。

请合理设置 ``--max-products`` 与间隔，并遵守 [Supreme 店铺](https://shop.supreme.com/collections/t-shirts) 使用条款与版权。

用法::

    python scripts/supreme_tshirts_download_hd_images.py -o supreme_tshirts_hd
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from playwright_helpers import CHROME_UA, launch_chromium, new_stealth_context

COLLECTION_DEFAULT = 'https://shop.supreme.com/collections/t-shirts'

# 在商品页执行，收集 Shopify CDN 产品图（去重、尽量含多图/最大 srcset）。
_EXTRACT_IMAGE_URLS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const add = (u) => {
    if (!u || typeof u !== 'string') return;
    if (!u.includes('cdn.shopify.com')) return;
    if (seen.has(u)) return;
    seen.add(u);
    out.push(u);
  };
  const og = document.querySelector('meta[property="og:image"]');
  if (og && og.content) add(og.content);
  try {
    const nodes = document.querySelectorAll('script[type="application/ld+json"]');
    for (const n of nodes) {
      const j = JSON.parse(n.textContent);
      const items = Array.isArray(j) ? j : [j];
      for (const item of items) {
        if (item && item['@type'] === 'Product' && item.image) {
          const im = item.image;
          if (typeof im === 'string') add(im);
          else if (Array.isArray(im)) im.forEach(add);
          else if (im && im.url) add(im.url);
        }
      }
    }
  } catch (e) {}
  document.querySelectorAll('img').forEach((img) => {
    add(img.currentSrc);
    add(img.src);
    const ss = img.getAttribute('srcset');
    if (ss) {
      let best = '';
      let bestW = 0;
      ss.split(',').forEach((part) => {
        const bits = part.trim().split(/\s+/);
        const u = bits[0];
        const wspec = bits[1];
        let w = 0;
        if (wspec && /^\d+w$/.test(wspec)) w = parseInt(wspec, 10);
        if (u && u.includes('cdn.shopify.com') && w >= bestW) {
          bestW = w;
          best = u;
        }
      });
      add(best);
    }
  });
  return out;
}
"""


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description='从 T-Shirts 集合逐页打开商品，提取 Shopify 产品图并下载高清版本。'
    )
    p.add_argument(
        '--url',
        default=COLLECTION_DEFAULT,
        help='集合页 URL（默认：t-shirts）',
    )
    p.add_argument(
        '-o',
        '--output-dir',
        type=Path,
        default=Path('supreme_tshirts_hd'),
        help='图片保存目录',
    )
    p.add_argument(
        '--image-width',
        type=int,
        default=4096,
        help='Shopify CDN 的 width 参数（默认 4096，过大可能仍被源图限制）',
    )
    p.add_argument(
        '--max-products',
        type=int,
        default=0,
        help='最多处理多少件商品，0 表示不限制',
    )
    p.add_argument(
        '--scroll-rounds',
        type=int,
        default=30,
        help='集合页向下滚动轮数，用于懒加载（每轮约半屏）',
    )
    p.add_argument(
        '--between-ms',
        type=int,
        default=500,
        help='两个商品页之间的间隔（毫秒）',
    )
    p.add_argument(
        '--product-wait-ms',
        type=int,
        default=2000,
        help='进入商品页后等待毫秒数，便于懒加载图片',
    )
    p.add_argument(
        '--width',
        type=int,
        default=1280,
        help='浏览器视口宽度',
    )
    p.add_argument(
        '--height',
        type=int,
        default=900,
        help='浏览器视口高度',
    )
    p.add_argument(
        '--headed',
        action='store_true',
        help='显示浏览器（调试用）',
    )
    p.add_argument(
        '--browser-channel',
        choices=('auto', 'chromium', 'chrome', 'msedge'),
        default='auto',
        help='同其他脚本',
    )
    return p.parse_args()


def _slug_from_product_url(href: str) -> str:
    path = urlparse(href).path.rstrip('/')
    return path.split('/')[-1] or 'product'


def _dismiss_cookie_banner(page) -> None:
    for sel in (
        'button:has-text("Accept")',
        'button:has-text("I Accept")',
        'button:has-text("Agree")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1200):
                loc.click()
                page.wait_for_timeout(400)
                return
        except PlaywrightError:
            continue


def _scroll_collection(page, rounds: int, height: int) -> None:
    for _ in range(max(0, rounds)):
        page.mouse.wheel(0, height // 2)
        page.wait_for_timeout(350)


def _collect_product_urls(page, max_count: int | None) -> list[str]:
    """从集合页收集商品详情绝对 URL，顺序稳定、去重。"""
    loc = page.locator('a[href*="/products/"]')
    try:
        loc.first.wait_for(state='attached', timeout=45_000)
    except PlaywrightError:
        return []

    seen: set[str] = set()
    out: list[str] = []
    n = loc.count()
    for i in range(n):
        if max_count is not None and len(out) >= max_count:
            break
        raw = loc.nth(i).get_attribute('href')
        if not raw or '/products/' not in raw:
            continue
        full = urljoin(page.url, raw)
        norm = full.split('?')[0].split('#')[0].rstrip('/')
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _shopify_hd_url(src: str, max_width: int) -> str:
    """为 Shopify CDN 图片 URL 设置 width 参数以请求大图。"""
    if not src.startswith('http'):
        src = 'https:' + src if src.startswith('//') else src
    p = urlparse(src)
    qs = parse_qs(p.query)
    qs['width'] = [str(max_width)]
    new_q = urlencode(qs, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


def _safe_filename(s: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9._-]+', '_', s)
    return (s[:120] or 'img').strip('_')


def _download_image(
    request,
    url: str,
    path: Path,
    *,
    referer: str,
) -> bool:
    """下载图片，带上 Referer 与浏览器 UA，降低 CDN 拒绝概率。"""
    try:
        r = request.get(
            url,
            timeout=120_000,
            headers={
                'User-Agent': CHROME_UA,
                'Referer': referer,
                'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            },
        )
        if r.status != 200:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.body())
        return True
    except PlaywrightError:
        return False


def run(args: argparse.Namespace) -> None:
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    max_products = None if args.max_products == 0 else args.max_products

    with sync_playwright() as p:
        browser = launch_chromium(
            p, headed=args.headed, browser_channel=args.browser_channel
        )
        context = new_stealth_context(
            browser, width=args.width, height=args.height
        )
        page = context.new_page()
        request = context.request

        page.goto(args.url, wait_until='domcontentloaded', timeout=90_000)
        _dismiss_cookie_banner(page)
        page.wait_for_timeout(1200)
        _scroll_collection(page, args.scroll_rounds, args.height)

        product_urls = _collect_product_urls(page, max_products)

        if not product_urls:
            print('未找到商品链接。请检查集合 URL 或增大 --scroll-rounds。')
            context.close()
            browser.close()
            return

        print(
            f'共 {len(product_urls)} 件商品，按详情页提取图片并下载 '
            f'（CDN width={args.image_width}）…'
        )

        ok = 0
        for idx, product_url in enumerate(product_urls, start=1):
            handle = _slug_from_product_url(product_url)
            page.goto(product_url, wait_until='domcontentloaded', timeout=90_000)
            page.wait_for_timeout(args.product_wait_ms)
            srcs: list[str] = page.evaluate(_EXTRACT_IMAGE_URLS_JS)
            page.wait_for_timeout(args.between_ms)

            if not srcs:
                print(f'  [{idx}/{len(product_urls)}] {handle}: 页面未解析到 cdn.shopify 图片')
                continue

            for j, src in enumerate(srcs, start=1):
                hd = _shopify_hd_url(src, args.image_width)
                ext = '.jpg'
                pl = urlparse(hd).path.lower()
                if '.png' in pl:
                    ext = '.png'
                elif '.webp' in pl:
                    ext = '.webp'

                fname = f'{idx:03d}_{_safe_filename(handle)}_{j}{ext}'
                target = out_dir / fname
                if _download_image(request, hd, target, referer=product_url):
                    ok += 1
                    print(f'  OK {target.name}')
                elif _download_image(
                    request, src, target.with_suffix(ext), referer=product_url
                ):
                    ok += 1
                    print(f'  OK (原尺寸) {target.name}')
                else:
                    print(f'  FAIL {handle} 图 {j}')

        context.close()
        browser.close()

    print(f'完成。成功下载约 {ok} 个文件，目录: {out_dir.resolve()}')


def main() -> None:
    run(parse_args())


if __name__ == '__main__':
    main()
