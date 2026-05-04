"""调用 MiniMax ``image_generation``，按参考 T 恤商品图为每张图生成工厂线稿风打版技术图。

环境变量:

- ``MINIMAX_API_KEY``：必填才会执行；未设置则跳过本步骤。
- ``MINIMAX_API_BASE``：默认 ``https://api.minimaxi.com``。
- ``MINIMAX_TECH_PROMPT``：覆盖默认英文 prompt（须 ≤1500 字符，否则自动截断并记入日志）。
- ``MINIMAX_MAX_IMAGES``：最多对多少张参考图调用 API，``0`` 表示全部（慎用额度）。
- ``MINIMAX_SLEEP_SEC``：两次请求间隔秒数，默认 ``1.5``（略回避限流）。

参考图通过 ``subject_reference[].image_file`` 传入，官方支持公网 URL 或 Data URL
(``data:image/jpeg;base64,...``)。详见 MiniMax 图生图文档。
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path

import requests

DEFAULT_API_BASE = os.environ.get('MINIMAX_API_BASE', 'https://api.minimaxi.com')
MAX_PROMPT_LEN = 1500


def normalize_api_key(raw: str) -> str:
    """HTTP 头须为 latin-1；密钥应为 ASCII，去掉复制时混入的全角/零宽等字符。"""
    s = raw.strip()
    return ''.join(c for c in s if ord(c) < 128)

# 接口限制 prompt ≤1500；以下为与用户给定规格对齐的压缩版（front/back/side + 尺寸 + Pantone）。
DEFAULT_TECH_SHEET_PROMPT = (
    'You are a professional fashion technical designer. Analyze the reference '
    'T-shirt image and create a factory-ready technical specification sheet with '
    '**front, back, and side views** in clean vector-style line art (black '
    'technical lines on pure white background; no shading or gradients; scalable).'
    '\n\n### Garment & Base Specs\n'
    '- Fabric: 220gsm heavyweight cotton jersey\n'
    '- Fit: Relaxed boxy American fit\n'
    '- Neck: 1x1 rib crew neck\n'
    '- Stitching: Double-stitched hems, cuffs, shoulder seams\n\n'
    '### Front (cm, dimension lines + labels)\n'
    '- Body length (HPS to hem): 72; chest half (1cm below armhole): 56; '
    'shoulder: 49; sleeve (shoulder to cuff): 22; neck width: 22; rib height: 2; '
    'cuff/bottom hem: 2\n'
    '- Print: centered on chest, **18cm below HPS**, 12×8 cm; label '
    '"PRINT CENTERED, 18CM BELOW HPS". Recreate print from reference as vector, '
    'colors/fonts unchanged.\n\n'
    '### Back (cm)\n'
    '- Body 72; chest half 56; shoulder 49; back neck drop 2; back neck width 20; '
    'back print per reference if any (placement, Pantone).\n\n'
    '### Side (cm)\n'
    '- Body 72; armhole depth 22; side seam underarm to hem 48; sleeve cap 5; '
    'cuff opening 18.\n\n'
    '### Pantone\n'
    'Label fabric, print background, text, graphics, rib (vs body) with '
    'closest Pantone Solid Coated / TCX.\n\n'
    '### Notes\n'
    'No brand logos; crisp dimension lines; proportional scale across views; '
    'vector for pattern cutting and sampling.'
)


def image_path_to_data_url(path: Path) -> str:
    """本地图片转 Data URL（MiniMax 文档支持）。"""
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0]
    if not mime:
        suf = path.suffix.lower()
        mime = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp',
        }.get(suf, 'image/jpeg')
    b64 = base64.standard_b64encode(data).decode('ascii')
    return f'data:{mime};base64,{b64}'


def call_image_generation(
    *,
    reference_image: Path,
    api_key: str,
    prompt: str,
    api_base: str,
    aspect_ratio: str = '16:9',
    model: str = 'image-01',
    timeout: int = 300,
) -> bytes:
    """调用 ``POST /v1/image_generation``，返回首张图的二进制（JPEG/PNG 视模型输出）。"""
    api_key = normalize_api_key(api_key)
    if not api_key:
        raise ValueError('MINIMAX_API_KEY 为空或含非 ASCII 字符，请检查环境变量')
    url = f'{api_base.rstrip("/")}/v1/image_generation'
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model,
        'prompt': prompt,
        'aspect_ratio': aspect_ratio,
        'subject_reference': [
            {
                'type': 'character',
                'image_file': image_path_to_data_url(reference_image),
            }
        ],
        'response_format': 'base64',
        'n': 1,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    base_resp = body.get('base_resp') or {}
    code = base_resp.get('status_code')
    if code != 0:
        msg = base_resp.get('status_msg', str(body))[:800]
        raise RuntimeError(f'MiniMax status_code={code}: {msg}')
    data = body.get('data') or {}
    images_b64 = data.get('image_base64') or []
    if not images_b64:
        raise RuntimeError(f'无 image_base64: {str(body)[:600]}')
    return base64.b64decode(images_b64[0])


def _list_product_images(out_dir: Path) -> list[Path]:
    """仅遍历下载目录顶层的商品图，忽略 ``tech_sheets`` 子目录内文件。"""
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    files = [
        p
        for p in out_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts
    ]
    return sorted(files, key=lambda x: x.name.lower())


def run_tech_sheets_for_job(out_dir: Path, log_lines: list[str]) -> None:
    """在 ``out_dir`` 下创建 ``tech_sheets/``，为每张顶层商品图生成一张打版图。"""
    api_key = normalize_api_key(os.environ.get('MINIMAX_API_KEY', ''))
    if not api_key:
        log_lines.append('--- MiniMax 打版图: 未设置 MINIMAX_API_KEY，已跳过 ---')
        return

    api_base = os.environ.get('MINIMAX_API_BASE', DEFAULT_API_BASE).strip()
    max_n = int(os.environ.get('MINIMAX_MAX_IMAGES', '0'))
    sleep_sec = float(os.environ.get('MINIMAX_SLEEP_SEC', '1.5'))
    custom_prompt = os.environ.get('MINIMAX_TECH_PROMPT', '').strip()
    raw_prompt = custom_prompt if custom_prompt else DEFAULT_TECH_SHEET_PROMPT
    if len(raw_prompt) > MAX_PROMPT_LEN:
        prompt = raw_prompt[:MAX_PROMPT_LEN]
        log_lines.append(
            f'--- MiniMax: prompt 已截断至 {MAX_PROMPT_LEN} 字符（接口上限）---'
        )
    else:
        prompt = raw_prompt

    images = _list_product_images(out_dir)
    if not images:
        log_lines.append('--- MiniMax 打版图: 目录中无商品图片文件，已跳过 ---')
        return

    if max_n > 0:
        images = images[:max_n]

    tech_root = out_dir / 'tech_sheets'
    tech_root.mkdir(parents=True, exist_ok=True)

    log_lines.append(
        f'--- MiniMax 打版图: 共 {len(images)} 张参考图，输出目录 {tech_root.name}/ ---'
    )
    ok = 0
    for idx, src in enumerate(images, start=1):
        out_name = f'{src.stem}_tech.jpeg'
        dest = tech_root / out_name
        try:
            raw = call_image_generation(
                reference_image=src,
                api_key=api_key,
                prompt=prompt,
                api_base=api_base,
            )
            dest.write_bytes(raw)
            ok += 1
            log_lines.append(f'  [{idx}/{len(images)}] OK {dest.name}')
        except Exception as e:
            log_lines.append(f'  [{idx}/{len(images)}] FAIL {src.name}: {e}')
        if idx < len(images) and sleep_sec > 0:
            time.sleep(sleep_sec)

    log_lines.append(f'--- MiniMax 打版图结束: 成功 {ok}/{len(images)} ---')
