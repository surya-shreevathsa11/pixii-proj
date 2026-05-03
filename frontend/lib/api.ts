import { apiBaseUrl } from "@/lib/config";
import type { BootstrapResponse, JobDetailResponse } from "@/lib/types";

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text || resp.statusText);
  }
  return resp.json() as Promise<T>;
}

export async function postMarketJob(bestsellersUrl: string): Promise<{ job_id: string }> {
  const resp = await fetch(`${apiBaseUrl()}/api/jobs/market`, {
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
  const resp = await fetch(`${apiBaseUrl()}/api/jobs/competitive`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return handle(resp);
}

export async function fetchJob(jobId: string): Promise<JobDetailResponse> {
  const resp = await fetch(`${apiBaseUrl()}/api/jobs/${jobId}`, { cache: "no-store" });
  return handle(resp);
}

export async function fetchBootstrap(): Promise<BootstrapResponse> {
  const resp = await fetch(`${apiBaseUrl()}/api/bootstrap`, { cache: "no-store" });
  return handle(resp);
}
