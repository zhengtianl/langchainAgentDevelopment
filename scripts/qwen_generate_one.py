"""单张图调用阿里云百炼「通义万相」图生图（wan2.5-i2i-preview），输出一张生成图。

需配置::

    set DASHSCOPE_API_KEY=sk-xxxx
    :: 或 QWEN_API_KEY（与上百炼控制台获得的 Key 相同）

可选::

    set DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com
    set WANX_I2I_MODEL=wan2.5-i2i-preview

用法::

    python scripts/qwen_generate_one.py -i ref.jpg -o out.png
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
)
from qwen_wanx_i2i import call_wanx_image2image  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description='通义万相图生图（单张）')
    p.add_argument('-i', '--input', type=Path, required=True, help='参考图')
    p.add_argument('-o', '--output', type=Path, default=Path('qwen_i2i_out.png'))
    args = p.parse_args()

    key = (
        os.environ.get('DASHSCOPE_API_KEY', '').strip()
        or os.environ.get('QWEN_API_KEY', '').strip()
    )
    if not key:
        print('错误: 请设置 DASHSCOPE_API_KEY 或 QWEN_API_KEY', file=sys.stderr)
        sys.exit(1)
    if not args.input.is_file():
        print(f'错误: 文件不存在: {args.input}', file=sys.stderr)
        sys.exit(1)

    custom = os.environ.get('QWEN_TECH_PROMPT', '').strip() or os.environ.get(
        'MINIMAX_TECH_PROMPT', ''
    ).strip()
    raw = custom if custom else DEFAULT_TECH_SHEET_PROMPT
    if len(raw) > MAX_PROMPT_LEN:
        print(
            f'提示: prompt 已截断至 {MAX_PROMPT_LEN} 字符',
            file=sys.stderr,
        )
        prompt = raw[:MAX_PROMPT_LEN]
    else:
        prompt = raw

    base = os.environ.get('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com').strip()
    model = os.environ.get('WANX_I2I_MODEL', 'wan2.5-i2i-preview').strip()

    print(f'创建异步任务并轮询… model={model}', flush=True)
    out_bytes = call_wanx_image2image(
        reference_image=args.input.resolve(),
        api_key=key,
        prompt=prompt,
        base_url=base,
        model=model,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out_bytes)
    print(f'已保存: {args.output.resolve()} ({len(out_bytes)} bytes)', flush=True)


if __name__ == '__main__':
    main()
