import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    // The designed-slide CSS is read as text (`?raw`); keep its text in tests instead of emptying it like other style sheets.
    css: { include: [/\.css\?raw$/] },
    clearMocks: true,
    restoreMocks: true,
  },
});
