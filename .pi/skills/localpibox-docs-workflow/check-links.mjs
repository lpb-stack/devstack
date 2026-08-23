#!/usr/bin/env node
/** Check relative markdown links in one or more git repos. For every
 *  [text](target) whose target is a local .md path (no scheme, no leading
 *  /), verify the file exists relative to the linking file. Only TRACKED
 *  files are checked (git ls-files), so derived/gitignored trees (docs/,
 *  site/ on the docs branch) are excluded automatically. Anchors ignored.
 *  No dependencies.
 *
 *  Usage:
 *    node check-links.mjs /path/to/repo [/path/to/repo2 ...]
 *
 *  Exit code 1 if any link is broken.
 */
import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, resolve, dirname } from "node:path";

const LINK_RE = /\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;

function trackedMds(root) {
  try {
    const out = execFileSync(
      "git", ["-C", root, "ls-files", "*.md"],
      { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    );
    return out.split("\n").filter(Boolean).map((p) => resolve(root, p));
  } catch {
    return null; // not a git repo
  }
}

let failures = 0;
let checked = 0;
let filesChecked = 0;
for (const arg of process.argv.slice(2)) {
  const root = resolve(arg);
  const files = trackedMds(root);
  if (files === null) {
    console.log(`SKIP ${root}: not a git repo`);
    continue;
  }
  filesChecked += files.length;
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(LINK_RE)) {
      let target = m[1];
      if (/^[a-z+]+:/i.test(target) || target.startsWith("/") || target.startsWith("#")) continue;
      target = target.split("#")[0];
      if (!target || !target.toLowerCase().endsWith(".md")) continue;
      checked++;
      if (!existsSync(resolve(dirname(file), target))) {
        failures++;
        console.log(`BROKEN ${file}: -> ${m[1]}`);
      }
    }
  }
}
console.log(`${filesChecked} tracked .md files, ${checked} local .md links, ${failures} broken`);
process.exit(failures ? 1 : 0);
