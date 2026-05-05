"""Supreme HD 图下载 API（与 React 前端分离）。

启动::

    pip install -r requirements-web.txt
    python web/app.py

默认监听 8765。若报 WinError 10048 / 端口占用，先结束旧进程，或换端口::

    set BACKEND_PORT=8766
    python web/app.py

同时在前端 ``frontend/.env.local`` 设 ``VITE_PROXY_TARGET=http://127.0.0.1:8766`` 并重启 ``npm run dev``。

打版图 API Key 建议写入仓库根目录 ``.env`` 或 ``web/.env``（见 ``web/env.example``），
启动 ``python web/app.py`` 时会自动加载（需 ``pip install -r requirements-web.txt``）。
仅依赖「系统环境变量」时，须在与启动后端**同一终端**里 ``set``，或**重启** Cursor / 终端后再起后端。

下载完成后，可在商品图目录下生成打版图 ``tech_sheets/`` 并打入同一 ZIP。

- **通义万相（推荐与 MiniMax 二选一）**：设置 ``DASHSCOPE_API_KEY`` 或 ``QWEN_API_KEY``，
  见 `web/qwen_wanx_i2i.py`（百炼图生图异步任务 + 轮询）。可用 ``TECH_SHEET_PROVIDER``:
  ``auto``（有 DashScope Key 则优先万相，否则 MiniMax）、``dashscope``、``minimax``、``none``。

- **MiniMax**：``MINIMAX_API_KEY``，见 `web/minimax_tech_sheet.py`。

共用：``MINIMAX_MAX_IMAGES`` 限制张数；``MINIMAX_TECH_PROMPT`` / ``QWEN_TECH_PROMPT`` 覆盖英文 prompt。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import AliasChoices, BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / '.env')
    load_dotenv(WEB_DIR / '.env', override=True)
except ImportError:
    pass
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

from lib.supreme_shop import (  # noqa: E402
    COLLECTION_DEFAULT_ALL,
    COLLECTION_DEFAULT_TSHIRTS,
)

from minimax_tech_sheet import normalize_api_key, run_tech_sheets_for_job  # noqa: E402
from qwen_wanx_i2i import run_wanx_tech_sheets_for_job  # noqa: E402

WORK_ROOT = ROOT / 'downloads_work'
WORK_ROOT.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}


def _new_work_folder_name() -> str:
    """``downloads_work`` 下的一级目录名：本地时间戳 + 微秒，避免同秒多任务冲突。"""
    base = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    name = base
    n = 0
    while (WORK_ROOT / name).exists():
        n += 1
        name = f'{base}_{n}'
    return name


app = FastAPI(title='Supreme 下载 API', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        'http://127.0.0.1:5173',
        'http://localhost:5173',
        'http://127.0.0.1:4173',
        'http://localhost:4173',
    ],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


class StartJobBody(BaseModel):
    """任务参数。"""

    mode: str = Field(..., description='tshirts_hd | all_hd')
    max_products: int = Field(0, ge=0, description='0 表示不限制件数（慎用）')
    browser_channel: str = Field(
        'auto',
        description='playwright：auto | chrome | msedge | chromium',
    )
    generate_tech_sheets: bool = Field(
        True,
        description='为 False 时不生成 tech_sheets/，不调用通义万相与 MiniMax',
        validation_alias=AliasChoices(
            'generate_tech_sheets',
            'generateTechSheets',
        ),
    )

    @field_validator('generate_tech_sheets', mode='before')
    @classmethod
    def _normalize_generate_tech_sheets(cls, v: Any) -> bool:
        """兼容缺失字段、字符串与数字，避免误用默认值 True 导致仍调图生图 API。"""
        if v is None:
            return True
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return int(v) != 0
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ('0', 'false', 'no', 'off', ''):
                return False
            if s in ('1', 'true', 'yes', 'on'):
                return True
            return bool(s)
        return bool(v)


def run_tech_sheets_dispatch(out_dir: Path, log_lines: list[str]) -> None:
    """按 ``TECH_SHEET_PROVIDER`` 调用通义万相或 MiniMax 生成 ``tech_sheets/``。"""
    provider = os.environ.get('TECH_SHEET_PROVIDER', 'auto').strip().lower()
    ds_key = normalize_api_key(
        os.environ.get('DASHSCOPE_API_KEY', '') or os.environ.get('QWEN_API_KEY', '')
    )
    mm_key = normalize_api_key(os.environ.get('MINIMAX_API_KEY', ''))

    if provider in ('none', 'off', 'disabled'):
        log_lines.append('--- 打版图: TECH_SHEET_PROVIDER=none，已跳过 ---')
        return
    if provider == 'dashscope':
        run_wanx_tech_sheets_for_job(out_dir, log_lines)
        return
    if provider == 'minimax':
        run_tech_sheets_for_job(out_dir, log_lines)
        return
    if ds_key:
        run_wanx_tech_sheets_for_job(out_dir, log_lines)
    elif mm_key:
        run_tech_sheets_for_job(out_dir, log_lines)
    else:
        log_lines.append(
            '--- 打版图: 未配置 DASHSCOPE_API_KEY/QWEN_API_KEY 与 MINIMAX_API_KEY，已跳过 ---'
        )


def _run_hd_download(
    job_id: str,
    collection_url: str,
    out_subdir: str,
    max_products: int,
    browser_channel: str,
) -> None:
    job = JOBS[job_id]
    work_folder = str(job.get('work_folder') or '')
    if not work_folder:
        job['status'] = 'error'
        job['error'] = '内部错误：缺少 work_folder'
        job['log'] = ''
        return
    parent = WORK_ROOT / work_folder
    out_dir = parent / out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    script = SCRIPTS / 'supreme_tshirts_download_hd_images.py'
    cmd = [
        sys.executable,
        str(script),
        '--url',
        collection_url,
        '-o',
        str(out_dir),
        '--browser-channel',
        browser_channel,
        '--scroll-rounds',
        '35',
    ]
    if max_products > 0:
        cmd.extend(['--max-products', str(max_products)])
    else:
        cmd.extend(['--max-products', '0'])

    log_lines: list[str] = []
    # 中文 Windows 下子进程 stdout 常为 GBK，与 encoding='utf-8' 解码不一致会乱码；强制子进程 UTF-8 输出
    child_env = os.environ.copy()
    child_env['PYTHONIOENCODING'] = 'utf-8'
    child_env['PYTHONUTF8'] = '1'
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=None,
            encoding='utf-8',
            errors='replace',
            env=child_env,
        )
        log_lines.append(f'exit_code={proc.returncode}')
        if proc.stdout:
            log_lines.append(proc.stdout)
        if proc.stderr:
            log_lines.append(proc.stderr)
        log_text = '\n'.join(log_lines)

        if proc.returncode != 0:
            job['status'] = 'error'
            job['log'] = log_text
            job['error'] = '脚本执行失败（非零退出码）'
            return

        try:
            # 仅以 JOBS 为准，避免线程参数与任务记录不一致
            want_tech = bool(JOBS[job_id].get('generate_tech_sheets', True))
            log_lines.append(
                f'--- 打版图开关(任务记录): generate_tech_sheets={want_tech} ---'
            )
            if want_tech:
                run_tech_sheets_dispatch(out_dir, log_lines)
            else:
                log_lines.append(
                    '--- 打版图: 已选择不生成打板文件，未调用通义万相 / MiniMax ---'
                )
        except Exception as e:
            log_lines.append(f'--- 打版图: 未预期错误 ---\n{e!s}')

        log_text = '\n'.join(log_lines)

        arc_base = str(parent / f'_export_{out_subdir}')
        shutil.make_archive(arc_base, 'zip', root_dir=str(parent), base_dir=out_subdir)
        zip_tmp = Path(arc_base + '.zip')
        zip_on_disk = parent / f'{out_subdir}.zip'
        if zip_on_disk.exists():
            zip_on_disk.unlink()
        zip_tmp.rename(zip_on_disk)
        job['status'] = 'done'
        job['log'] = log_text
        # 下载文件名带时间戳目录名，便于与磁盘一级目录对应
        job['zip_name'] = f'{work_folder}_{out_subdir}.zip'
        job['zip_path'] = str(zip_on_disk)
    except Exception as e:
        job['status'] = 'error'
        job['error'] = str(e)
        job['log'] = '\n'.join(log_lines)


@app.get('/')
async def root() -> dict:
    return {
        'ok': True,
        'message': 'Supreme download API',
        'docs': '/docs',
        'react_dev': 'http://127.0.0.1:5173',
    }


@app.post('/api/jobs')
async def create_job(body: StartJobBody) -> dict:
    if body.mode not in ('tshirts_hd', 'all_hd'):
        raise HTTPException(400, 'mode 须为 tshirts_hd 或 all_hd')

    if body.mode == 'tshirts_hd':
        url = COLLECTION_DEFAULT_TSHIRTS
        subdir = 'supreme_tshirts_hd'
    else:
        url = COLLECTION_DEFAULT_ALL
        subdir = 'supreme_all_hd'

    job_id = uuid.uuid4().hex
    work_folder = _new_work_folder_name()
    JOBS[job_id] = {
        'status': 'running',
        'mode': body.mode,
        'collection_url': url,
        'generate_tech_sheets': body.generate_tech_sheets,
        'work_folder': work_folder,
        'log': '',
        'zip_path': None,
        'zip_name': None,
        'error': None,
    }

    t = threading.Thread(
        target=_run_hd_download,
        args=(job_id, url, subdir, body.max_products, body.browser_channel),
        daemon=True,
    )
    t.start()
    return {
        'job_id': job_id,
        'collection_url': url,
        'generate_tech_sheets': body.generate_tech_sheets,
        'work_folder': work_folder,
    }


@app.get('/api/jobs/{job_id}')
async def job_status(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, 'job 不存在')
    j = JOBS[job_id].copy()
    j.pop('zip_path', None)
    return j


@app.get('/api/jobs/{job_id}/download')
async def download_zip(job_id: str) -> FileResponse:
    if job_id not in JOBS:
        raise HTTPException(404, 'job 不存在')
    j = JOBS[job_id]
    if j['status'] != 'done' or not j.get('zip_path'):
        raise HTTPException(400, '任务未完成或无可下载文件')
    path = Path(j['zip_path'])
    if not path.is_file():
        raise HTTPException(404, 'zip 已删除')
    return FileResponse(
        path,
        filename=j.get('zip_name') or path.name,
        media_type='application/zip',
    )


@app.get('/api/meta')
async def meta() -> dict:
    ds = bool(
        normalize_api_key(
            os.environ.get('DASHSCOPE_API_KEY', '')
            or os.environ.get('QWEN_API_KEY', '')
        )
    )
    return {
        'tshirts_url': COLLECTION_DEFAULT_TSHIRTS,
        'all_url': COLLECTION_DEFAULT_ALL,
        'supreme_shop_common': str(SCRIPTS / 'lib' / 'supreme_shop.py'),
        'minimax_tech_sheets': bool(normalize_api_key(os.environ.get('MINIMAX_API_KEY', ''))),
        'dashscope_tech_sheets': ds,
        'tech_sheet_provider': os.environ.get('TECH_SHEET_PROVIDER', 'auto'),
    }


def main() -> None:
    import uvicorn

    port = int(os.environ.get('BACKEND_PORT', '8765'))
    print(f'API 监听 http://127.0.0.1:{port}（环境变量 BACKEND_PORT 可改端口）', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=port)


if __name__ == '__main__':
    main()
