import type { NextConfig } from "next";
import path from "path";

const stub = path.join(process.cwd(), "src/stubs/empty.ts");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "@x402/evm/upto/client": stub,
      "@x402/evm/exact/client": stub,
      "@x402/core/client": stub,
      "@x402/svm/exact/client": stub,
    };
    return config;
  },
};

export default nextConfig;
