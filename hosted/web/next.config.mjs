/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Nothing is exported to a CDN and no page is statically committed: every page
  // is rendered per request, so it reflects the plane's own records rather than a
  // build-time snapshot.
};

export default nextConfig;