/** 生产环境可设 VITE_API_BASE=http://127.0.0.1:8765；开发环境走 Vite proxy，留空即可 */
export const apiBase = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export function apiUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${apiBase}${p}`;
}

export interface MetaResponse {
  tshirts_url: string;
  all_url: string;
  supreme_shop_common: string;
  /** 后端是否设置了 MINIMAX_API_KEY（完成后 ZIP 可含 tech_sheets/） */
  minimax_tech_sheets?: boolean;
  /** 是否配置了百炼 DASHSCOPE / QWEN API Key */
  dashscope_tech_sheets?: boolean;
  /** auto | dashscope | minimax | none */
  tech_sheet_provider?: string;
}

export async function fetchMeta(): Promise<MetaResponse> {
  const r = await fetch(apiUrl('/api/meta'));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface StartJobResponse {
  job_id: string;
  collection_url: string;
  /** 后端解析后的打版图开关，应与请求一致 */
  generate_tech_sheets?: boolean;
  /** downloads_work 下的一级目录名（时间戳） */
  work_folder?: string;
}

export async function startJob(body: {
  mode: 'tshirts_hd' | 'all_hd';
  max_products: number;
  browser_channel: string;
  /** 为 false 时不生成打板图，后端不调用通义/MiniMax */
  generate_tech_sheets?: boolean;
}): Promise<StartJobResponse> {
  const payload = {
    ...body,
    // 与 snake_case 一并发送，避免代理/中间层只认 camelCase 时丢失字段
    generateTechSheets: body.generate_tech_sheets,
  };
  const r = await fetch(apiUrl('/api/jobs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface JobStatus {
  status: 'running' | 'done' | 'error';
  mode?: string;
  collection_url?: string;
  /** 该任务是否请求生成打板图 */
  generate_tech_sheets?: boolean;
  /** 产物目录：downloads_work/<work_folder>/ */
  work_folder?: string;
  log?: string;
  error?: string | null;
  zip_name?: string | null;
}

export async function fetchJob(jobId: string): Promise<JobStatus> {
  const r = await fetch(apiUrl(`/api/jobs/${jobId}`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function downloadJobUrl(jobId: string): string {
  return apiUrl(`/api/jobs/${jobId}/download`);
}
