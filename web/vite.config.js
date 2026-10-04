import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // VITE_API_URL comes from .env.local, which is generated from Terraform output
  // by scripts/write-web-env.sh. Nothing here hard-codes an API URL, because the
  // API id changes whenever the estate is destroyed and recreated.
  const env = loadEnv(mode, process.cwd(), "");

  return {
    // Relative paths, because the built site is served from S3 behind CloudFront
    // and an absolute "/assets/..." would break if it ever moved to a sub-path.
    base: "./",

    server: {
      // strictPort matters more than it looks. Cognito matches the callback URL
      // exactly, and this port is registered in the user pool. Without it, Vite
      // silently moves to 3001 when 3000 is busy and every sign-in then fails
      // with redirect_mismatch - an error that names the redirect rather than
      // the port.
      port: 3000,
      strictPort: true,

      // The API is proxied rather than called directly, so the browser only ever
      // talks to localhost:3000 and CORS never enters the picture during
      // development. The proxy runs server-side, where CORS does not apply.
      //
      // In production the same relative /api path is served by CloudFront from
      // the API Gateway origin, so the client code is identical in both and
      // there is no origin to configure in either.
      proxy: {
        "/api": {
          target: env.VITE_API_URL,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
  };
});
