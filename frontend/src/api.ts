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
}

export async function startJob(body: {
  mode: 'tshirts_hd' | 'all_hd';
  max_products: number;
  browser_channel: string;
}): Promise<StartJobResponse> {
  const r = await fetch(apiUrl('/api/jobs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface JobStatus {
  status: 'running' | 'done' | 'error';
  mode?: string;
  collection_url?: string;
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
