"""Playwright 启动与“反白屏”等待的共享逻辑，供截图脚本复用。"""

from __future__ import annotations

from playwright.sync_api import Browser
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright

# 减轻自动化标记；与常见桌面 Chrome UA 对齐（下载 CDN 时也可作 User-Agent）。
CHROME_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)
LAUNCH_ARGS = ('--disable-blink-features=AutomationControlled',)

INIT_PATCH = """\
(() => {
  const w = navigator;
  try {
    Object.defineProperty(w, 'webdriver', { get: () => undefined });
  } catch (e) {}
})();
"""


def launch_chromium(
    p: Playwright,
    *,
    headed: bool,
    browser_channel: str,
) -> Browser:
    """启动 Chromium：自带包或本机 Chrome / Edge。"""
    headless = not headed
    base: dict = {'headless': headless, 'args': list(LAUNCH_ARGS)}
    if browser_channel == 'chromium':
        return p.chromium.launch(**base)
    if browser_channel == 'chrome':
        return p.chromium.launch(**base, channel='chrome')
    if browser_channel == 'msedge':
        return p.chromium.launch(**base, channel='msedge')

    attempts = ({}, {'channel': 'chrome'}, {'channel': 'msedge'})
    last_err: PlaywrightError | None = None
    for extra in attempts:
        try:
            return p.chromium.launch(**{**base, **extra})
        except PlaywrightError as e:
            last_err = e
            continue
    msg = (
        '无法启动浏览器。可选：python -m playwright install chromium，'
        '或安装 Chrome/Edge 后使用 --browser-channel chrome|msedge。\n'
        f'最后一次错误: {last_err}'
    )
    raise RuntimeError(msg) from last_err


def new_stealth_context(
    browser: Browser,
    *,
    width: int,
    height: int,
):
    """新建带 UA、语言的上下文并注入轻量 patch。"""
    context = browser.new_context(
        viewport={'width': width, 'height': height},
        user_agent=CHROME_UA,
        locale='en-US',
        timezone_id='America/New_York',
        color_scheme='light',
        extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
    )
    context.add_init_script(INIT_PATCH)
    return context


def wait_until_meaningful_paint(page, url: str, wait_ms: int) -> None:
    """等待 SPA 出现可见内容（用于 Instagram 等）。"""
    is_ig = 'instagram.com' in url.lower()
    fn_timeout = 55_000 if is_ig else 35_000
    try:
        page.wait_for_function(
            """() => {
              const body = document.body;
              if (!body) return false;
              const t = (body.innerText || '').trim();
              const imgs = document.querySelectorAll('img[src]');
              const articles = document.querySelectorAll('article');
              if (articles.length > 0) return true;
              if (imgs.length >= 2) return true;
              if (t.length > 120) return true;
              return false;
            }""",
            timeout=fn_timeout,
        )
    except PlaywrightError:
        pass
    page.wait_for_timeout(wait_ms)
