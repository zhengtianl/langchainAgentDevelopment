"""Supreme / Shopify 集合页共用工具：链接收集、文件名规则与滚动。

与 ``supreme_tshirts_download_hd_images`` 使用相同的 handle / 序号规则，便于输出目录对照。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

COLLECTION_DEFAULT_TSHIRTS = 'https://shop.supreme.com/collections/t-shirts'

# 官方「全部分类」列表页（商品量很大；自动化抓取请自行控制频率与条款）。
COLLECTION_DEFAULT_ALL = 'https://shop.supreme.com/collections/all'


def slug_from_product_url(href: str) -> str:
    """商品 URL 最后一段 handle（未做安全过滤）。"""
    path = urlparse(href).path.rstrip('/')
    return path.split('/')[-1] or 'product'


def safe_filename(s: str) -> str:
    """与 HD 下载脚本一致的安全文件名段。"""
    s = re.sub(r'[^a-zA-Z0-9._-]+', '_', s)
    return (s[:120] or 'img').strip('_')


def dismiss_cookie_banner(page: Page) -> None:
    """关闭常见 Cookie 条。"""
    for sel in (
        'button:has-text("Accept")',
        'button:has-text("I Accept")',
        'button:has-text("Agree")',
        '[id*="cookie"] button',
        'button[aria-label*="Accept"]',
    ):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1500):
                loc.click()
                page.wait_for_timeout(400)
                return
        except PlaywrightError:
            continue


def scroll_collection_page(page: Page, rounds: int, height: int) -> None:
    """列表页向下滚动，触发懒加载。"""
    for _ in range(max(0, rounds)):
        page.mouse.wheel(0, height // 2)
        page.wait_for_timeout(350)


def collect_product_urls(page: Page, max_count: int | None) -> list[str]:
    """收集 ``/products/`` 绝对 URL，顺序稳定、去重。"""
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
