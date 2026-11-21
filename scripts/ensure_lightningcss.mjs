// Tailwind v4 needs the platform binary of `lightningcss`. npm sometimes skips optional deps
// (known npm bug), which breaks `vite`. Check, and install the missing binary with --no-save.
// Run: npm run setup (from frontend/). Called by scripts/start_local.sh.
import { execSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../frontend");
const require = createRequire(path.join(frontend, "package.json"));

try {
  require("lightningcss");
  console.log("lightningcss: ok");
  process.exit(0);
} catch {
  /* fall through to install */
}

const { platform, arch } = process;
let suffix = "";
if (platform === "linux") {
  const musl = existsSync("/lib/ld-musl-x86_64.so.1") || existsSync("/lib/ld-musl-aarch64.so.1");
  suffix = musl ? "-musl" : "-gnu";
} else if (platform === "win32") suffix = "-msvc";
const pkg = `lightningcss-${platform}-${arch}${suffix}`;
const version = JSON.parse(readFileSync(path.join(frontend, "node_modules/lightningcss/package.json"), "utf8")).version;
console.log(`lightningcss: binary missing, installing ${pkg}@${version} (--no-save)`);
execSync(`npm install --no-save --no-package-lock ${pkg}@${version}`, { cwd: frontend, stdio: "inherit" });
try {
  require("lightningcss");
  console.log("lightningcss: ok");
} catch (e) {
  console.error("lightningcss still fails to load:", e.message);
  process.exit(1);
}
