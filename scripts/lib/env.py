"""仓库根目录 ``.env`` 加载，并把 ``web`` 目录加入 ``sys.path``（供图生图 CLI 导入 ``web`` 模块）。"""

from __future__ import annotations

import sys
from pathlib import Path


def repo_root() -> Path:
    """``scripts/lib`` → 仓库根。"""
    return Path(__file__).resolve().parent.parent.parent


def load_dotenv_from_repo() -> None:
    """尽量加载根目录与 ``web/.env``；未安装 ``python-dotenv`` 时静默跳过。"""
    root = repo_root()
    try:
        from dotenv import load_dotenv

        load_dotenv(root / '.env')
        load_dotenv(root / 'web' / '.env', override=True)
    except ImportError:
        pass


def ensure_web_on_path() -> Path:
    """将 ``web`` 加入 ``sys.path``，返回该目录 Path。"""
    web = repo_root() / 'web'
    s = str(web)
    if s not in sys.path:
        sys.path.insert(0, s)
    return web
