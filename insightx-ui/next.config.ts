import type { NextConfig } from "next";

// The browser only talks to this app. Calls to /api/... are forwarded to the
// InsightX FastAPI backend, so the backend needs no CORS change at all.
const backend = (process.env.INSIGHTX_BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    // Connecting a big database or running an investigation can take minutes.
    // The default proxy limit (30 s) would cut those calls off with a 500.
    proxyTimeout: 10 * 60 * 1000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
