import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8012",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : {},
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { ...devices["Pixel 5"] } },
  ],
  webServer: {
    command: `${process.env.CBIN_TEST_PYTHON || "python"} tests/e2e_server.py`,
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    url: "http://127.0.0.1:8012/health/ready",
    reuseExistingServer: false,
    timeout: 20000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
