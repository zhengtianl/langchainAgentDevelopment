"""从单张本地参考图调用 MiniMax 生成一张打版图（与 web/minimax_tech_sheet.py 同一套接口）。

用法（仓库根目录）::

    pip install -r requirements-web.txt
    set MINIMAX_API_KEY=你的密钥
    python scripts/minimax_generate_one.py -i path/to/ref.jpg -o out_tech.jpeg

环境变量与 web 模块相同：MINIMAX_API_BASE、MINIMAX_TECH_PROMPT（可选）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from lib.env import ensure_web_on_path, load_dotenv_from_repo

load_dotenv_from_repo()
ensure_web_on_path()

from minimax_tech_sheet import (  # noqa: E402
    DEFAULT_TECH_SHEET_PROMPT,
    MAX_PROMPT_LEN,
    call_image_generation,
    normalize_api_key,
)


def main() -> None:
    p = argparse.ArgumentParser(description='单张参考图生成一张打版图（需 MINIMAX_API_KEY）')
    p.add_argument(
        '-i',
        '--input',
        type=Path,
        required=True,
        help='参考商品图（jpg/png/webp）',
    )
    p.add_argument(
        '-o',
        '--output',
        type=Path,
        default=Path('tech_sheet_out.jpeg'),
        help='输出文件路径（默认 tech_sheet_out.jpeg）',
    )
    args = p.parse_args()

    api_key = normalize_api_key(os.environ.get('MINIMAX_API_KEY', ''))
    if not api_key:
        print('错误: 请先设置环境变量 MINIMAX_API_KEY', file=sys.stderr)
        sys.exit(1)
    if not args.input.is_file():
        print(f'错误: 文件不存在: {args.input}', file=sys.stderr)
        sys.exit(1)

    api_base = os.environ.get('MINIMAX_API_BASE', 'https://api.minimaxi.com').strip()
    custom = os.environ.get('MINIMAX_TECH_PROMPT', '').strip()
    raw_prompt = custom if custom else DEFAULT_TECH_SHEET_PROMPT
    if len(raw_prompt) > MAX_PROMPT_LEN:
        print(
            f'提示: prompt 已截断至 {MAX_PROMPT_LEN} 字符',
            file=sys.stderr,
        )
        prompt = raw_prompt[:MAX_PROMPT_LEN]
    else:
        prompt = raw_prompt

    print(f'正在请求 MiniMax… 参考: {args.input.resolve()}', flush=True)
    out_bytes = call_image_generation(
        reference_image=args.input.resolve(),
        api_key=api_key,
        prompt=prompt,
        api_base=api_base,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out_bytes)
    print(f'已保存: {args.output.resolve()} ({len(out_bytes)} bytes)', flush=True)


if __name__ == '__main__':
    main()
