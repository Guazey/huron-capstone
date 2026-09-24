import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI lives in ../extension/src and is shared with the Chrome extension.
// Those files import react from outside this folder, so dedupe makes them use
// this app's copy: two Reacts on one page would break hooks.
const shared = fileURLToPath(new URL("../extension/src", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom", "react-markdown"] },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    fs: { allow: [".", shared] },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
