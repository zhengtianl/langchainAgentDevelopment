"""Louis Vuitton 列表页：依次打开商品详情并保存单张主图截图。

默认列表 URL：HK 男装成衣「全部」分类。站点并非 Shopify，链接一般为 ``.../products/...``，
主图多为 Scene7 / Dynamic Media（``is/image`` 等）。默认截取详情页中**面积最大**的商品
相关 ``img``（非整页长图）；可选 ``--viewport-only`` 改为视口单帧。

使用前请阅读 louisvuitton.com 条款，控制 ``--max`` 与 ``--between-ms``，避免高频自动化访问。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from lib.filename import safe_filename, slug_from_url
from lib.session import open_stealth_session

DEFAULT_LIST_URL = (
    'https://hk.louisvuitton.com/eng-hk/men/ready-to-wear/'
    'all-ready-to-wear/_/N-tmfgzj3'
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='LV 列表页采集商品链接并对每件保存一张 PNG（默认最大主图）。'
    )
    p.add_argument('--url', default=DEFAULT_LIST_URL, help='分类/列表页 URL')
    p.add_argument(
        '-o',
        '--output-dir',
        type=Path,
        default=Path('lv_product_screens'),
        help='截图输出目录',
    )
    p.add_argument(
        '--href-substring',
        default='',
        help='可选：只保留 URL 中含该子串的链接（默认不过滤；例 nvprod）',
    )
    p.add_argument(
        '--max',
        type=int,
        default=24,
        help='最多处理几件商品；0 表示不限制（慎用）',
    )
    p.add_argument('--wait-ms', type=int, default=3500, help='详情页加载等待毫秒')
    p.add_argument('--between-ms', type=int, default=1200, help='两件商品间隔毫秒')
    p.add_argument('--scroll-rounds', type=int, default=45, help='列表页滚动次数')
    p.add_argument(
        '--viewport-only',
        action='store_true',
        help='截取浏览器视口单帧，而非最大商品图',
    )
    p.add_argument(
        '--full-page',
        action='store_true',
        help='仅与 --viewport-only 同时用：整页长图',
    )
    p.add_argument('--width', type=int, default=1440)
    p.add_argument('--height', type=int, default=900)
    p.add_argument('--headed', action='store_true')
    p.add_argument(
        '--browser-channel',
        choices=('auto', 'chromium', 'chrome', 'msedge'),
        default='auto',
    )
    return p.parse_args()


def _dismiss_cookies(page: Page) -> None:
    for sel in (
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept All")',
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("接受")',
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click()
                page.wait_for_timeout(600)
                return
        except PlaywrightError:
            continue


def _scroll_list(page: Page, rounds: int, view_h: int) -> None:
    for _ in range(max(0, rounds)):
        page.mouse.wheel(0, view_h // 2)
        page.keyboard.press('End')
        page.wait_for_timeout(400)


# 与示例一致：https://hk.louisvuitton.com/eng-hk/products/...-nvprod.../1AJRDJ
_LV_PDP_PATH_RE = re.compile(
    r'/[^/]+/products/[^/?#]+/[^/?#]+',
    re.I,
)
# 从 HTML / JSON 文本里捞 URL（列表常把 PDP 写在 script 里而非 a[href]）
_LV_PDP_URL_IN_HTML_RE = re.compile(
    r'https?://(?:www\.)?hk\.louisvuitton\.com/[^/]+/products/[^"\'\s\\<>?#]+/[^"\'\s\\<>?#/?]+',
    re.I,
)


_COLLECT_PDP_ANCHORS_JS = r"""
(listUrl) => {
  const listNorm = listUrl.split('#')[0].replace(/\/$/, '').toLowerCase();
  const pdpPath = /^\/[^/]+\/products\/[^/]+\/[^/]+/;
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    let raw = a.getAttribute('href');
    if (!raw || raw === '#' || raw.toLowerCase().startsWith('javascript:')) continue;
    let abs;
    try {
      abs = new URL(raw, location.href).href.split('#')[0];
    } catch (e) { continue; }
    if (!/louisvuitton\.com/i.test(abs)) continue;
    let path;
    try {
      path = new URL(abs).pathname.replace(/\/$/, '');
    } catch (e2) { continue; }
    if (!pdpPath.test(path)) continue;
    const norm = abs.replace(/\/$/, '').toLowerCase();
    if (norm === listNorm) continue;
    if (seen.has(norm)) continue;
    seen.add(norm);
    out.push(abs);
  }
  return out;
}
"""


def _extract_pdp_urls_from_html(page: Page, list_url: str) -> list[str]:
    """从整页 HTML（含内嵌 JSON）中提取 PDP 绝对 URL。"""
    list_norm = list_url.split('#')[0].rstrip('/').lower()
    seen: set[str] = set()
    out: list[str] = []
    try:
        html = page.content()
    except PlaywrightError:
        return []
    for m in _LV_PDP_URL_IN_HTML_RE.finditer(html):
        u = m.group(0)
        for suf in ('\\', '"', "'", ')', ',', ';'):
            u = u.rstrip(suf)
        u = u.split('#')[0]
        if not u.startswith('http'):
            continue
        norm = u.split('?')[0].rstrip('/').lower()
        if norm == list_norm or norm in seen:
            continue
        if not _LV_PDP_PATH_RE.search(urlparse(u).path):
            continue
        seen.add(norm)
        out.append(u)
    return out


def _gather_links_from_all_frames(page: Page, list_url: str) -> list[str]:
    """在所有 Frame 中用 pathname 识别 PDP（不要求 href 含某子串）。"""
    js = _COLLECT_PDP_ANCHORS_JS.strip()
    seen: set[str] = set()
    ordered: list[str] = []
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    for fr in frames:
        try:
            hrefs: list[str] = fr.evaluate(js, list_url)
        except PlaywrightError:
            continue
        for h in hrefs:
            norm = h.split('#')[0].split('?')[0].rstrip('/').lower()
            if norm in seen:
                continue
            path = urlparse(h).path
            if not _LV_PDP_PATH_RE.search(path):
                continue
            seen.add(norm)
            ordered.append(h.split('#')[0])
    return ordered


def _merge_unique_ordered(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for u in lst:
            n = u.split('#')[0].split('?')[0].rstrip('/').lower()
            if n in seen:
                continue
            seen.add(n)
            out.append(u.split('#')[0])
    return out


def _collect_product_links(
    page: Page,
    list_url: str,
    href_substring: str,
    max_count: int | None,
) -> list[str]:
    """收集 PDP：全 Frame 锚点 + 整页 HTML 正则；可选子串过滤。"""
    from_frames = _gather_links_from_all_frames(page, list_url)
    from_html = _extract_pdp_urls_from_html(page, list_url)
    out = _merge_unique_ordered(from_frames, from_html)

    if href_substring:
        sub = href_substring.lower()
        out = [u for u in out if sub in u.lower()]

    if not out:
        list_norm = list_url.split('#')[0].rstrip('/').lower()
        for pat in ('/products/', 'nvprod'):
            loc = page.locator(f'a[href*="{pat}"]')
            try:
                loc.first.wait_for(state='attached', timeout=5_000)
            except PlaywrightError:
                continue
            n = loc.count()
            for i in range(n):
                raw = loc.nth(i).get_attribute('href')
                if not raw:
                    continue
                full = urljoin(page.url, raw)
                norm = full.split('#')[0].split('?')[0].rstrip('/').lower()
                if norm == list_norm:
                    continue
                if not _LV_PDP_PATH_RE.search(urlparse(full).path):
                    continue
                out = _merge_unique_ordered(out, [full.split('#')[0]])
            if out:
                break

    if max_count is not None:
        out = out[: max_count]
    return out


_LARGEST_LV_PRODUCT_IMG_JS = r"""
() => {
  const nodes = [...document.querySelectorAll('img')].filter((img) => {
    if (!img.offsetParent) return false;
    const s = (img.currentSrc || img.src || '').toLowerCase();
    if (!s) return false;
    if (s.includes('logo') || s.includes('icon') && img.naturalWidth < 64) return false;
    if (!/louisvuitton|is\/image|scene7|cloudinary|akamai/.test(s)) return false;
    const r = img.getBoundingClientRect();
    if (r.width < 40 || r.height < 40) return false;
    if (r.top < 0 || r.top > window.innerHeight + 200) return false;
    return true;
  });
  let bestI = -1;
  let bestA = 0;
  const all = [...document.querySelectorAll('img')];
  nodes.forEach((img) => {
    const i = all.indexOf(img);
    if (i < 0) return;
    const w = img.naturalWidth || img.getBoundingClientRect().width;
    const h = img.naturalHeight || img.getBoundingClientRect().height;
    const a = w * h;
    if (a > bestA) {
      bestA = a;
      bestI = i;
    }
  });
  return bestI;
}
"""


def _wait_pdp(page: Page, wait_ms: int) -> None:
    try:
        page.wait_for_function(
            """() => {
              const h = document.querySelector('h1');
              const img = document.querySelector('main img, [role="main"] img, picture img');
              return (h && h.textContent && h.textContent.trim().length > 0)
                || (img && (img.naturalWidth > 0 || img.getBoundingClientRect().width > 50));
            }""",
            timeout=50_000,
        )
    except PlaywrightError:
        pass
    page.wait_for_timeout(wait_ms)


def _screenshot_pdp(
    page: Page,
    path: Path,
    *,
    viewport_only: bool,
    full_page: bool,
) -> None:
    if viewport_only:
        page.screenshot(path=str(path), full_page=full_page)
        return
    idx = page.evaluate(_LARGEST_LV_PRODUCT_IMG_JS)
    if idx >= 0:
        loc = page.locator('img').nth(idx)
        try:
            loc.wait_for(state='visible', timeout=12_000)
            loc.screenshot(path=str(path))
            return
        except PlaywrightError:
            pass
    page.screenshot(path=str(path), full_page=False)


def run(args: argparse.Namespace) -> None:
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    max_n = None if args.max == 0 else args.max
    dsf = 1.0 if args.viewport_only else 2.0

    with open_stealth_session(
        headed=args.headed,
        browser_channel=args.browser_channel,
        width=args.width,
        height=args.height,
        device_scale_factor=dsf,
    ) as s:
        page = s.page

        page.goto(args.url, wait_until='domcontentloaded', timeout=120_000)
        try:
            page.wait_for_load_state('load', timeout=90_000)
        except PlaywrightError:
            pass
        _dismiss_cookies(page)
        page.wait_for_timeout(2000)
        _scroll_list(page, args.scroll_rounds, args.height)

        hrefs = _collect_product_links(page, args.url, args.href_substring, max_n)
        if not hrefs:
            page.wait_for_timeout(2500)
            _scroll_list(page, min(25, args.scroll_rounds), args.height)
            hrefs = _collect_product_links(page, args.url, args.href_substring, max_n)
        if not hrefs:
            shot = out_dir / '_lv_list_empty.png'
            page.screenshot(path=str(shot), full_page=False)
            print(
                '未在页面中解析到 Louis Vuitton 商品详情 URL（路径形如 '
                '…/eng-hk/products/…/SKU）。'
                f'已保存列表页 {shot.resolve()}。可试 --headed 接受 Cookie，或加大 --scroll-rounds。'
            )
            return

        if args.full_page and not args.viewport_only:
            print(
                '提示: --full-page 仅在指定 --viewport-only 时生效；'
                '当前仍为「最大商品图」单张 PNG。'
            )
        print(f'共 {len(hrefs)} 个商品链接，开始截图…')

        for i, href in enumerate(hrefs, start=1):
            slug = slug_from_url(href)
            fname = out_dir / f'{i:03d}_{safe_filename(slug)}_1.png'
            page.goto(href, wait_until='domcontentloaded', timeout=120_000)
            page.wait_for_timeout(800)
            _dismiss_cookies(page)
            _wait_pdp(page, args.wait_ms)
            _screenshot_pdp(
                page,
                fname,
                viewport_only=args.viewport_only,
                full_page=args.full_page,
            )
            print(f'  [{i}/{len(hrefs)}] {fname.name}')
            page.wait_for_timeout(args.between_ms)

    print(f'完成。输出目录: {out_dir.resolve()}')


def main() -> None:
    run(parse_args())


if __name__ == '__main__':
    main()
