/**
 * Base URL for FastAPI. Empty string = same-origin `/api/...` (recommended in dev: proxied by `next.config.mjs`
 * rewrites, so the browser never hits CORS). Set `NEXT_PUBLIC_API_BASE` only when the UI and API are on
 * different deploy hosts and CORS is configured.
 */
export const apiBaseUrl = () => {
  const raw = process.env.NEXT_PUBLIC_API_BASE?.trim();
  if (!raw) return "";
  return raw.replace(/\/$/, "");
};
