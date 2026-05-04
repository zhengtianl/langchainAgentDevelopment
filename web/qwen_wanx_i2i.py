"""阿里云百炼 DashScope：通义万相图生图（wan2.5-i2i 等），用于打版图等场景。

与 MiniMax 互斥或按 :envvar:`TECH_SHEET_PROVIDER` 选择。鉴权使用环境变量：

- ``DASHSCOPE_API_KEY`` 或 ``QWEN_API_KEY``（二选一，内容均为百炼 API-Key）
- ``DASHSCOPE_BASE_URL``：默认 ``https://dashscope.aliyuncs.com``（北京）；
  国际域名为 ``https://dashscope-intl.aliyuncs.com``（需与 Key 地域一致）
- ``WANX_I2I_MODEL``：默认 ``wan2.5-i2i-preview``
- ``DASHSCOPE_POLL_INTERVAL``：轮询秒数，默认 ``10``
- ``DASHSCOPE_POLL_TIMEOUT``：最长等待秒数，默认 ``600``
- ``DASHSCOPE_IMAGE_MAX_SIDE``：参考图最长边像素上限，默认 ``2048``（避免超大高清图 Base64 撑爆请求体触发 HTTP 400）
- ``DASHSCOPE_IMAGE_MAX_BYTES``：压缩后 JPEG 目标大小（字节），默认 ``4194304``（约 4MB）
- ``DASHSCOPE_MIN_EDGE``：宽高均须 ≥384（接口硬约束）；默认 ``384``，不足时用白底居中补齐（横图窄边易 <384 会直接导致 HTTP 400）
- ``DASHSCOPE_PROMPT_EXTEND``：``true`` / ``false``，对应 ``parameters.prompt_extend``，默认 ``false``（部分账号对 true 不兼容时可改）

HTTP 流程：创建异步任务 → 轮询 ``GET /api/v1/tasks/{task_id}`` → 下载结果图 URL。

参考：万相通用图像编辑 API（image2image image-synthesis）。
"""

from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

import requests

from minimax_tech_sheet import (  # same package when run from app
    DEFAULT_TECH_SHEET_PROMPT,
    image_path_to_data_url,
    normalize_api_key,
)

DEFAULT_BASE = os.environ.get('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com').rstrip(
    '/'
)
DEFAULT_MODEL = os.environ.get('WANX_I2I_MODEL', 'wan2.5-i2i-preview')

# 百炼文档：prompt 最长约 2000 字符（与 MiniMax 的 1500 不同）
DASHSCOPE_MAX_PROMPT_CHARS = 2000


def _http_error_body(r: requests.Response) -> str:
    try:
        j = r.json()
        if isinstance(j, dict):
            return str(
                j.get('message')
                or j.get('Message')
                or j.get('error', {}).get('message')
                or j.get('code')
                or j.get('Code')
                or j
            )[:1500]
    except Exception:
        pass
    return (r.text or '')[:1500]


def _raise_if_dashscope_json_error(data: dict[str, Any]) -> None:
    """百炼部分错误以 HTTP 200 + JSON 内 code/base_resp 返回，而非 HTTP 4xx。"""
    br = data.get('base_resp')
    if isinstance(br, dict):
        sc = br.get('status_code')
        if sc not in (None, 0, '0'):
            raise RuntimeError(
                f'DashScope base_resp {sc}: {br.get("status_msg", br)}'
            )
    root_code = data.get('code')
    allowed = (None, '', 0, '0', 200, '200', 'Success', 'success', 'OK', 'ok')
    if root_code not in allowed:
        if str(root_code).lower() in ('success', 'ok'):
            return
        raise RuntimeError(
            f'DashScope 响应 code={root_code}: {data.get("message", data)[:1200]}'
        )


def _dashscope_fit_image(img: Any, *, max_side: int, min_edge: int) -> Any:
    """满足接口：宽高均在 [min_edge, 5000]，且长边不超过 max_side。"""
    from PIL import Image

    for _ in range(8):
        w, h = img.size
        if max(w, h) > max_side:
            s = max_side / max(w, h)
            img = img.resize(
                (max(1, int(w * s)), max(1, int(h * s))),
                Image.Resampling.LANCZOS,
            )
            continue
        if min(w, h) < min_edge:
            cw = max(w, min_edge)
            ch = max(h, min_edge)
            canvas = Image.new('RGB', (cw, ch), (255, 255, 255))
            canvas.paste(img, ((cw - w) // 2, (ch - h) // 2))
            img = canvas
            continue
        break

    w, h = img.size
    if max(w, h) > 5000:
        s = 5000 / max(w, h)
        img = img.resize(
            (max(min_edge, int(w * s)), max(min_edge, int(h * s))),
            Image.Resampling.LANCZOS,
        )
    return img


def reference_to_dashscope_data_url(path: Path) -> str:
    """将本地参考图转为 Data URL：缩放、白底补齐短边（≥384）、JPEG 压缩，避免 400。"""
    raw = path.read_bytes()
    max_side = int(os.environ.get('DASHSCOPE_IMAGE_MAX_SIDE', '2048'))
    min_edge = int(os.environ.get('DASHSCOPE_MIN_EDGE', '384'))
    target_bytes = int(os.environ.get('DASHSCOPE_IMAGE_MAX_BYTES', str(4 * 1024 * 1024)))

    try:
        from PIL import Image
    except ImportError:
        return image_path_to_data_url(path)

    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert('RGB')
    except Exception:
        return image_path_to_data_url(path)

    img = _dashscope_fit_image(img, max_side=max_side, min_edge=min_edge)

    quality = 88
    jpeg_bytes = b''
    while quality >= 52:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        jpeg_bytes = buf.getvalue()
        if len(jpeg_bytes) <= target_bytes:
            break
        quality -= 7

    b64 = base64.standard_b64encode(jpeg_bytes).decode('ascii')
    return f'data:image/jpeg;base64,{b64}'


def _default_i2i_parameters() -> dict[str, Any]:
    pe = os.environ.get('DASHSCOPE_PROMPT_EXTEND', 'false').strip().lower() in (
        '1',
        'true',
        'yes',
    )
    return {'n': 1, 'prompt_extend': pe}


def _headers(api_key: str) -> dict[str, str]:
    k = normalize_api_key(api_key)
    if not k:
        raise ValueError('DASHSCOPE_API_KEY / QWEN_API_KEY 未设置或无效')
    return {
        'Authorization': f'Bearer {k}',
        'Content-Type': 'application/json',
        'X-DashScope-Async': 'enable',
    }


def _create_task(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    image_data_urls: list[str],
    parameters: dict[str, Any] | None = None,
    timeout: int = 60,
) -> str:
    url = f'{base_url.rstrip("/")}/api/v1/services/aigc/image2image/image-synthesis'
    prompt_use = (
        prompt[:DASHSCOPE_MAX_PROMPT_CHARS]
        if len(prompt) > DASHSCOPE_MAX_PROMPT_CHARS
        else prompt
    )
    body: dict[str, Any] = {
        'model': model,
        'input': {
            'prompt': prompt_use,
            'images': image_data_urls,
        },
        'parameters': parameters
        if parameters is not None
        else _default_i2i_parameters(),
    }
    try:
        r = requests.post(url, headers=_headers(api_key), json=body, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f'DashScope 网络请求失败: {e!s}') from e

    if not r.ok:
        raise RuntimeError(
            f'DashScope 创建任务 HTTP {r.status_code}: {_http_error_body(r)}'
        )
    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(
            f'DashScope 创建任务返回非 JSON（HTTP {r.status_code}）: {(r.text or "")[:600]}'
        ) from e

    _raise_if_dashscope_json_error(data)
    out = data.get('output') or {}
    task_id = out.get('task_id')
    if not task_id:
        raise RuntimeError(f'创建任务无 task_id，完整响应: {str(data)[:1200]}')
    return str(task_id)


def _poll_until_done(
    *,
    api_key: str,
    base_url: str,
    task_id: str,
    interval: float,
    timeout_sec: float,
) -> dict[str, Any]:
    """返回任务完成的 ``output`` 对象（含 ``results``）。"""
    deadline = time.monotonic() + timeout_sec
    root = base_url.rstrip('/')
    task_url = f'{root}/api/v1/tasks/{task_id}'
    poll_headers = {'Authorization': f'Bearer {normalize_api_key(api_key)}'}
    while time.monotonic() < deadline:
        r = requests.get(task_url, headers=poll_headers, timeout=60)
        if not r.ok:
            raise RuntimeError(
                f'DashScope 查询任务 HTTP {r.status_code}: {_http_error_body(r)}'
            )
        data = r.json()
        out = data.get('output') or {}
        status = (out.get('task_status') or '').upper()
        if status == 'SUCCEEDED':
            return out
        if status == 'FAILED':
            code = out.get('code', data.get('code'))
            msg = out.get('message', data.get('message', str(out)[:500]))
            raise RuntimeError(f'DashScope 任务失败: {code} {msg}')
        if status in ('CANCELED', 'UNKNOWN'):
            raise RuntimeError(f'DashScope 任务状态异常: {status} {out!s}'[:800])
        time.sleep(interval)
    raise TimeoutError(f'轮询超时（{timeout_sec}s）: task_id={task_id}')


def _result_url(output: dict[str, Any]) -> str:
    results = output.get('results') or []
    if not results:
        raise RuntimeError(f'无 results: {output!s}'[:600])
    u = results[0].get('url')
    if not u:
        raise RuntimeError(f'无结果 URL: {output!s}'[:600])
    return str(u)


def call_wanx_image2image(
    *,
    reference_image: Path,
    api_key: str,
    prompt: str,
    base_url: str = DEFAULT_BASE,
    model: str = DEFAULT_MODEL,
    poll_interval: float | None = None,
    poll_timeout: float | None = None,
) -> bytes:
    """单张参考图 → 一张生成图（二进制）。"""
    data_url = reference_to_dashscope_data_url(reference_image)
    interval = float(
        poll_interval if poll_interval is not None else os.environ.get('DASHSCOPE_POLL_INTERVAL', '10')
    )
    tout = float(
        poll_timeout if poll_timeout is not None else os.environ.get('DASHSCOPE_POLL_TIMEOUT', '600')
    )

    task_id = _create_task(
        api_key=api_key,
        base_url=base_url,
        model=model,
        prompt=prompt,
        image_data_urls=[data_url],
    )
    out = _poll_until_done(
        api_key=api_key,
        base_url=base_url,
        task_id=task_id,
        interval=interval,
        timeout_sec=tout,
    )
    url = _result_url(out)
    r = requests.get(url, timeout=120)
    if not r.ok:
        raise RuntimeError(
            f'下载结果图 HTTP {r.status_code}: {_http_error_body(r)[:500]}'
        )
    return r.content


def _list_product_images(out_dir: Path) -> list[Path]:
    exts = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    return sorted(
        (p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in exts),
        key=lambda x: x.name.lower(),
    )


def run_wanx_tech_sheets_for_job(out_dir: Path, log_lines: list[str]) -> None:
    """在 ``out_dir/tech_sheets/`` 下写入 ``*_tech.png``（万相默认 PNG）。"""
    api_key = normalize_api_key(
        os.environ.get('DASHSCOPE_API_KEY', '') or os.environ.get('QWEN_API_KEY', '')
    )
    if not api_key:
        log_lines.append(
            '--- 通义万相图生图: 未设置 DASHSCOPE_API_KEY / QWEN_API_KEY，已跳过 ---'
        )
        return

    base_url = os.environ.get('DASHSCOPE_BASE_URL', DEFAULT_BASE).strip().rstrip('/')
    model = os.environ.get('WANX_I2I_MODEL', DEFAULT_MODEL).strip()
    max_n = int(os.environ.get('MINIMAX_MAX_IMAGES', '0'))  # 与 MiniMax 共用上限变量名
    sleep_sec = float(os.environ.get('QWEN_SLEEP_SEC', os.environ.get('MINIMAX_SLEEP_SEC', '1.5')))

    custom_prompt = os.environ.get('QWEN_TECH_PROMPT', '').strip() or os.environ.get(
        'MINIMAX_TECH_PROMPT', ''
    ).strip()
    raw_prompt = custom_prompt if custom_prompt else DEFAULT_TECH_SHEET_PROMPT
    if len(raw_prompt) > DASHSCOPE_MAX_PROMPT_CHARS:
        prompt = raw_prompt[:DASHSCOPE_MAX_PROMPT_CHARS]
        log_lines.append(
            f'--- 通义万相: prompt 已截断至 {DASHSCOPE_MAX_PROMPT_CHARS} 字符 ---'
        )
    else:
        prompt = raw_prompt

    images = _list_product_images(out_dir)
    if not images:
        log_lines.append('--- 通义万相图生图: 目录中无商品图片，已跳过 ---')
        return

    if max_n > 0:
        images = images[:max_n]

    tech_root = out_dir / 'tech_sheets'
    tech_root.mkdir(parents=True, exist_ok=True)

    log_lines.append(
        f'--- 通义万相图生图 ({model}): 共 {len(images)} 张，输出 {tech_root.name}/ ---'
    )
    ok = 0
    for idx, src in enumerate(images, start=1):
        dest = tech_root / f'{src.stem}_tech.png'
        try:
            raw = call_wanx_image2image(
                reference_image=src,
                api_key=api_key,
                prompt=prompt,
                base_url=base_url,
                model=model,
            )
            dest.write_bytes(raw)
            ok += 1
            log_lines.append(f'  [{idx}/{len(images)}] OK {dest.name}')
        except Exception as e:
            detail = str(e)
            resp = getattr(e, 'response', None)
            if resp is not None and hasattr(resp, 'status_code'):
                detail = (
                    f'HTTP {resp.status_code}: {_http_error_body(resp)}'
                    if resp.status_code
                    else detail
                )
            log_lines.append(f'  [{idx}/{len(images)}] FAIL {src.name}: {detail}')
        if idx < len(images) and sleep_sec > 0:
            time.sleep(sleep_sec)

    log_lines.append(f'--- 通义万相图生图结束: 成功 {ok}/{len(images)} ---')
