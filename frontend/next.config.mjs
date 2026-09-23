/** @type {import('next').NextConfig} */

// No rewrites here on purpose. `rewrites()` is evaluated during `next build`
// and baked into the routes manifest, so it cannot read a runtime environment
// variable. The proxy lives in src/app/api/[...path]/route.ts instead, which
// runs per request.
const nextConfig = {
  reactStrictMode: true,
};
export default nextConfig;
