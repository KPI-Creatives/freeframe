/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    // Type checking is performed in CI and locally via `tsc --noEmit`; skipping
    // it during the production build keeps the Docker build under memory limits
    // on smaller Coolify hosts (the type-check phase spikes RAM hard).
    ignoreBuildErrors: true,
  },
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '9000',
      },
    ],
  },
  async rewrites() {
    // Proxy /stream/* (HLS playback) to the FastAPI backend.
    //
    // Why: the FastAPI hls_proxy router is mounted at /stream/*. With a default
    // ingress that only routes /api/* to the backend, browser requests to
    // /stream/hls/master.m3u8 hit Next.js (no route) and 404. We rewrite them
    // server-side to the api container.
    //
    // The hls_proxy router validates its own short-lived JWT (?token=...) on
    // every segment fetch, so exposing it without /api prefix is safe — that's
    // also why /stream/ is in middleware.ts's PUBLIC_PREFIXES list.
    const apiInternalUrl = process.env.API_INTERNAL_URL || 'http://api:8000'
    return [
      {
        source: '/stream/:path*',
        destination: `${apiInternalUrl}/stream/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
