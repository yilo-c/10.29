/**
 * sync-dist.mjs — Copy desktop build output to 思忆集 web_dist/.
 *
 * Usage:  node scripts/sync-dist.mjs
 * Hook:   npm run build && npm run sync
 */
import { cp, readdir, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const SOURCE = join(ROOT, "packages", "desktop", "dist");
const TARGET = join(ROOT, "..", "思忆集app", "web_dist");

const LABEL = "[sync-dist]";

async function main() {
  if (!existsSync(SOURCE)) {
    console.error(`${LABEL} Source not found: ${SOURCE}`);
    console.error(`${LABEL} Run 'npm run build' first.`);
    process.exit(1);
  }

  await mkdir(TARGET, { recursive: true });

  const entries = await readdir(SOURCE, { withFileTypes: true });
  for (const entry of entries) {
    const src = join(SOURCE, entry.name);
    const dst = join(TARGET, entry.name);
    await cp(src, dst, { recursive: true, force: true });
  }

  const count = entries.length;
  console.log(`${LABEL} Copied ${count} entries from ${SOURCE}`);
  console.log(`${LABEL} => ${TARGET}`);
}

main().catch((err) => {
  console.error(`${LABEL} Failed:`, err);
  process.exit(1);
});
