import { defineConfig } from "vite";
import { resolve } from "path";

// Library build: produce a single self-contained IIFE bundle at dist/widget.js
// so the host page only needs one <script src="…/widget.js"> tag with no
// runtime dependency on a module loader, React global, or CSS file.
export default defineConfig({
  build: {
    target: "es2019",
    cssCodeSplit: false,
    minify: "esbuild",
    sourcemap: false,
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, "src/main.tsx"),
      name: "ConciergeWidget",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    rollupOptions: {
      output: { inlineDynamicImports: true, extend: true },
    },
  },
});
