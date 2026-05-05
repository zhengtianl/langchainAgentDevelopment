"""向后兼容：请使用 ``from lib.playwright import ...``。"""

from __future__ import annotations

from lib.playwright import (  # noqa: F401
    CHROME_UA,
    INIT_PATCH,
    LAUNCH_ARGS,
    launch_chromium,
    new_stealth_context,
    wait_until_meaningful_paint,
)
