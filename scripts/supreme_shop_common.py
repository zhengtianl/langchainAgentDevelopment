"""向后兼容：请使用 ``from lib.supreme_shop import ...``。"""

from __future__ import annotations

from lib.supreme_shop import (  # noqa: F401
    COLLECTION_DEFAULT_ALL,
    COLLECTION_DEFAULT_TSHIRTS,
    collect_product_urls,
    dismiss_cookie_banner,
    safe_filename,
    scroll_collection_page,
    slug_from_product_url,
)
