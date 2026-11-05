// Phase 0: does a throwaway-profile Chrome with web security off bypass CORS,
// and can an agent attach over CDP? Needs playwright-core on NODE_PATH.
// Usage: CHROME="/path/to/chrome" HOST=http://localhost:4421 node cors-devmode.js
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright-core");

const CHROME = process.env.CHROME;
const HOST = process.env.HOST || "http://localhost:4421";

async function run(label, extraArgs, cdpPort) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "pb-profile-"));
  const args = [
    "--headless=new", `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", ...extraArgs, "about:blank",
  ];
  const proc = spawn(CHROME, args, { stdio: "ignore" });
  let result = "no-result";
  try {
    let browser;
    for (let i = 0; i < 40 && !browser; i++) {
      try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`); }
      catch { await new Promise((r) => setTimeout(r, 250)); }
    }
    if (!browser) throw new Error("CDP attach failed");
    const ctx = browser.contexts()[0];
    const page = await ctx.newPage();
    await page.goto(HOST);
    await page.waitForFunction(() => document.getElementById("out").textContent !== "pending", null, { timeout: 8000 });
    result = await page.textContent("#out");
    await browser.close();
  } catch (e) { result = "ERROR " + e.message.split("\n")[0]; }
  proc.kill();
  fs.rmSync(profile, { recursive: true, force: true });
  console.log(`${label.padEnd(34)} page says: ${result}`);
}

(async () => {
  await run("normal (throwaway profile)", [], 9331);
  await run("dev-mode (web security off)", ["--disable-web-security", "--disable-site-isolation-trials"], 9332);
  await run("dev-mode, web-security flag only", ["--disable-web-security"], 9333);
})();
