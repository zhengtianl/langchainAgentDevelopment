"""跨脚本统一的 URL slug 与安全文件名规则。"""

from __future__ import annotations

import re
from urllib.parse import urlparse


def slug_from_url(href: str) -> str:
    """商品/详情 URL 路径最后一段 handle（未做额外安全过滤）。"""
    path = urlparse(href).path.rstrip('/')
    return path.split('/')[-1] or 'product'


def safe_filename(s: str, *, max_len: int = 120) -> str:
    """文件名安全段（字母数字与少量标点），过长截断。"""
    s = re.sub(r'[^a-zA-Z0-9._-]+', '_', s)
    s = (s[:max_len] or 'x').strip('_')
    return s or 'img'
