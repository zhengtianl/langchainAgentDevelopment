"""从 Supreme T-Shirts 集合下载每件商品的高分辨率产品图。

官方 Storefront JSON（如 ``/products/handle.js``）在部分环境下会返回 403，因此本脚本
在打开**商品详情页**后，从 ``og:image``、JSON-LD、``img``/``srcset`` 中收集
``cdn.shopify.com`` 地址。默认**每件商品只下载一张**：在候选里选**源图宽度最大**的一张
（从 ``width=`` 或路径里 ``NxM`` 推断；相同则取页面顺序中的第一张），再为 URL 增加
``width=`` 参数请求尽量大的图（默认 4096）。需要全量图时使用 ``--all-images``。

请合理设置 ``--max-products`` 与间隔，并遵守 [Supreme 店铺](https://shop.supreme.com/collections/t-shirts) 使用条款与版权。

用法::

    python scripts/supreme_tshirts_download_hd_images.py -o supreme_tshirts_hd
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError

from lib.playwright import CHROME_UA
from lib.session import open_stealth_session
from lib.supreme_shop import (
    COLLECTION_DEFAULT_TSHIRTS,
    collect_product_urls,
    dismiss_cookie_banner,
    safe_filename,
    scroll_collection_page,
    slug_from_product_url,
)

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
        default=COLLECTION_DEFAULT_TSHIRTS,
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
    p.add_argument(
        '--all-images',
        action='store_true',
        help='下载该商品页解析到的全部 Shopify 图；默认每件只下 1 张（源宽度最大）',
    )
    return p.parse_args()


def _normalize_http_url(src: str) -> str:
    if src.startswith('http'):
        return src
    return 'https:' + src if src.startswith('//') else src


def _inferred_source_width(url: str) -> int:
    """从 query ``width=`` 或路径中 ``宽x高`` 片段推断可比较的源宽度（用于选最大图）。"""
    u = _normalize_http_url(url)
    p = urlparse(u)
    qs = parse_qs(p.query)
    for key in ('width', 'w'):
        raw = (qs.get(key) or [None])[0]
        if raw and str(raw).isdigit():
            return int(raw)
    m = re.search(r'(\d{2,5})[xX](\d{2,5})', p.path)
    if m:
        return max(int(m.group(1)), int(m.group(2)))
    return 0


def _pick_largest_image_url(candidates: list[str]) -> str | None:
    """在候选列表中选源宽度最大者；宽度相同或均未知时取列表顺序中的第一张。"""
    if not candidates:
        return None
    best_i = 0
    best_w = _inferred_source_width(candidates[0])
    for i, u in enumerate(candidates[1:], start=1):
        w = _inferred_source_width(u)
        if w > best_w:
            best_w = w
            best_i = i
    return candidates[best_i]


def _shopify_hd_url(src: str, max_width: int) -> str:
    """为 Shopify CDN 图片 URL 设置 width 参数以请求大图。"""
    src = _normalize_http_url(src)
    p = urlparse(src)
    qs = parse_qs(p.query)
    qs['width'] = [str(max_width)]
    new_q = urlencode(qs, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


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

    with open_stealth_session(
        headed=args.headed,
        browser_channel=args.browser_channel,
        width=args.width,
        height=args.height,
    ) as s:
        page = s.page
        request = s.request

        page.goto(args.url, wait_until='domcontentloaded', timeout=90_000)
        dismiss_cookie_banner(page)
        page.wait_for_timeout(1200)
        scroll_collection_page(page, args.scroll_rounds, args.height)

        product_urls = collect_product_urls(page, max_products)

        if not product_urls:
            print('未找到商品链接。请检查集合 URL 或增大 --scroll-rounds。')
            return

        mode = '全部 Shopify 图' if args.all_images else '每商品 1 张（最大源宽优先）'
        print(
            f'共 {len(product_urls)} 件商品，{mode}，下载请求 width={args.image_width} …'
        )

        ok = 0
        for idx, product_url in enumerate(product_urls, start=1):
            handle = slug_from_product_url(product_url)
            page.goto(product_url, wait_until='domcontentloaded', timeout=90_000)
            page.wait_for_timeout(args.product_wait_ms)
            srcs: list[str] = page.evaluate(_EXTRACT_IMAGE_URLS_JS)
            page.wait_for_timeout(args.between_ms)

            if not srcs:
                print(f'  [{idx}/{len(product_urls)}] {handle}: 页面未解析到 cdn.shopify 图片')
                continue

            if args.all_images:
                to_fetch = list(enumerate(srcs, start=1))
            else:
                pick = _pick_largest_image_url(srcs)
                if not pick:
                    continue
                to_fetch = [(1, pick)]

            for j, src in to_fetch:
                hd = _shopify_hd_url(src, args.image_width)
                ext = '.jpg'
                pl = urlparse(hd).path.lower()
                if '.png' in pl:
                    ext = '.png'
                elif '.webp' in pl:
                    ext = '.webp'

                fname = f'{idx:03d}_{safe_filename(handle)}_{j}{ext}'
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

    print(f'完成。成功下载约 {ok} 个文件，目录: {out_dir.resolve()}')


def main() -> None:
    run(parse_args())


if __name__ == '__main__':
    main()
