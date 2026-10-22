import { defineConfig } from "vitest/config";

export default defineConfig({
  // GitHub Pages serves the site from /<repo>/; relative paths work for both
  base: "./",
  test: {
    environment: "node",
    testTimeout: 60_000,
  },
});
