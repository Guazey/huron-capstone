import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Two entry points: the side panel page and the background service worker.
// The worker must land at a fixed path because manifest.json names it.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: "sidepanel.html",
        background: "src/background.ts",
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js",
      },
    },
  },
  test: {
    environment: "node",
    // config.ts refuses to load without these; tests never call AWS.
    env: {
      VITE_REGION: "us-west-2",
      VITE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-west-2:000000000000:runtime/test",
      VITE_CLIENT_ID: "test-client",
      VITE_LOGIN_HOST: "https://test.auth.us-west-2.amazoncognito.com",
    },
  },
});
