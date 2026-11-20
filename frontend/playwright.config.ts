import { defineConfig } from "@playwright/test";

const port = 8765;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "off" },
  webServer: {
    command: `cd ../backend && uv run python -m dp_agentic_collector.testing.e2e_server --port ${port} --web-dist ../frontend/dist`,
    url: `http://127.0.0.1:${port}/api/v1/health`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
