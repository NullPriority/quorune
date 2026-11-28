import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const apiPort = Number(process.env.MTG_E2E_SERVER_PORT ?? "8000");
const webPort = Number(process.env.MTG_E2E_WEB_PORT ?? "5173");
function sourceBuild(): string {
  try {
    const stamp = process.env.QUORUNE_BUILD_ID
      ?? readFileSync(new URL("../.quorune-build", import.meta.url), "ascii").trim();
    return /^[a-f0-9]{40}$/i.test(stamp) ? stamp.toLowerCase() : "development";
  } catch {
    return "development";
  }
}

const manaFontAssets: Plugin = {
  name: "mana-font-assets",
  enforce: "pre",
  transform(source, id) {
    if (!id.split("?")[0].replaceAll("\\", "/").endsWith("/mana-font/css/mana.min.css")) return;
    return source.replace(/@font-face\s*\{\s*font-family:\s*["']?(Mana|MPlantin)["']?;[^}]*\}/g, (_face, family) =>
      family === "Mana"
        ? '@font-face{font-family:Mana;src:url("../fonts/mana.woff2") format("woff2");font-style:normal;font-weight:normal;font-display:swap}'
        : "");
  },
};

export default defineConfig({
  plugins: [manaFontAssets, react()],
  define: { __QUORUNE_CLIENT_BUILD__: JSON.stringify(sourceBuild()) },
  server: {
    port: webPort,
    strictPort: true,
    open: false,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: false,
        ws: true,
      },
    },
  },
});
