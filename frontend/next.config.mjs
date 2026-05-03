/** @type {import('next').NextConfig} */
// Proxy API calls through Next so the browser never cross-origin fetches FastAPI (fixes CORS / NetworkError in dev).
// Override if the backend runs elsewhere: API_PROXY_TARGET=http://127.0.0.1:8001 next dev
const backendBase = (process.env.API_PROXY_TARGET || process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
