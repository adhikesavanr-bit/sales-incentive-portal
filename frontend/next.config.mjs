/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The browser talks only to the backend API. No GCP credential, project id
  // or dataset name is ever exposed here.
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
    NEXT_PUBLIC_GOOGLE_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
    NEXT_PUBLIC_ALLOW_DEV_LOGIN: process.env.NEXT_PUBLIC_ALLOW_DEV_LOGIN,
  },
};
export default nextConfig;
