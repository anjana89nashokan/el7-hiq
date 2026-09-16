import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiProxyTarget = (env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000").replace(/\/$/, "");

  return {
    plugins: [react()],
    define: {
      "process.env.NODE_ENV": JSON.stringify(mode),
      "process.env.REACT_APP_STREAMING_ANOMALY": JSON.stringify(
        env.VITE_STREAMING_ANOMALY ?? "true",
      ),
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/sttm-api": {
          target: apiProxyTarget,
          changeOrigin: true,
          rewrite: (requestPath) => requestPath.replace(/^\/sttm-api/, ""),
        },
      },
    },
  };
});
