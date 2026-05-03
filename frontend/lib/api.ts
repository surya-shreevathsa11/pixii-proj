import { apiBaseUrl } from "@/lib/config";
import type { BootstrapResponse, JobDetailResponse } from "@/lib/types";

function apiUrl(path: string) {
  const base = apiBaseUrl();
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    const hint =
      typeof window !== "undefined" && !apiBaseUrl()
        ? " Is uvicorn running on port 8000? Restart `npm run dev` after changing API_PROXY_TARGET."
        : " Check NEXT_PUBLIC_API_BASE, backend CORS (CORS_ORIGINS), and that uvicorn is running.";
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`Cannot reach the API (${input}): ${message}.${hint}`);
  }
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || resp.statusText);
  }
  return resp.json() as Promise<T>;
}

export async function postMarketJob(bestsellersUrl: string): Promise<{ job_id: string }> {
  const resp = await apiFetch(apiUrl("/api/jobs/market"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ bestsellers_url: bestsellersUrl }),
  });
  return handle(resp);
}

export async function postCompetitiveJob(payload: {
  product_url: string;
  competitor_urls: string[];
  auto_discover_competitors: boolean;
}): Promise<{ job_id: string }> {
  const resp = await apiFetch(apiUrl("/api/jobs/competitive"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return handle(resp);
}

export async function fetchJob(jobId: string): Promise<JobDetailResponse> {
  const resp = await apiFetch(apiUrl(`/api/jobs/${jobId}`), { cache: "no-store" });
  return handle(resp);
}

export async function fetchBootstrap(): Promise<BootstrapResponse> {
  const resp = await apiFetch(apiUrl("/api/bootstrap"), { cache: "no-store" });
  return handle(resp);
}
