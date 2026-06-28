/** @type {import('next').NextConfig} */

// Backend (FastAPI/uvicorn) base URL. Override via env for non-local deploys.
const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000'

const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  // Same-origin proxy: the browser talks to /api/* on the Next dev server,
  // which forwards to the FastAPI backend. Keeps requests same-origin so no
  // CORS changes are needed on the backend.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_URL}/:path*`,
      },
    ]
  },
}

export default nextConfig
