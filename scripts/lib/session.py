"""基于 Playwright 的「一次性」隐蔽浏览器会话，统一资源释放。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import sync_playwright

from .playwright import launch_chromium, new_stealth_context


@dataclass(frozen=True, slots=True)
class StealthSession:
    """``with`` 块内使用的页面对象与底层句柄。"""

    page: Page
    context: BrowserContext
    browser: Browser

    @property
    def request(self):
        return self.context.request


@contextmanager
def open_stealth_session(
    *,
    headed: bool,
    browser_channel: str,
    width: int,
    height: int,
    device_scale_factor: float = 1.0,
) -> Generator[StealthSession, None, None]:
    """启动 Chromium、创建上下文与单页；退出时关闭 context 与 browser。"""
    with sync_playwright() as p:
        browser = launch_chromium(p, headed=headed, browser_channel=browser_channel)
        context = new_stealth_context(
            browser,
            width=width,
            height=height,
            device_scale_factor=device_scale_factor,
        )
        page = context.new_page()
        try:
            yield StealthSession(page=page, context=context, browser=browser)
        finally:
            context.close()
            browser.close()
