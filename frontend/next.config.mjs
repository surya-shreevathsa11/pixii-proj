/** @type {import('next').NextConfig} */
// Proxy API calls through Next so the browser never cross-origin fetches FastAPI (fixes CORS / NetworkError in dev).
// Override if the backend runs elsewhere: API_PROXY_TARGET=http://127.0.0.1:8001 next dev
const directApiBase = (process.env.NEXT_PUBLIC_API_BASE || "").trim();
const explicitProxyTarget = (process.env.API_PROXY_TARGET || process.env.BACKEND_URL || "").trim();
const isVercel = process.env.VERCEL === "1" || process.env.VERCEL === "true";
const useProxyRewrite = !directApiBase;
const backendBase = (
  explicitProxyTarget || (!isVercel ? "http://127.0.0.1:8000" : "")
).replace(/\/$/, "");

if (isVercel && useProxyRewrite && !backendBase) {
  throw new Error(
    "Frontend deploy misconfiguration: set BACKEND_URL (or API_PROXY_TARGET) on Vercel, "
      + "or set NEXT_PUBLIC_API_BASE and configure backend CORS.",
  );
}

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    if (!useProxyRewrite) {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: `${backendBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
