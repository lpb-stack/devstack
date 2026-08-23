#!/usr/bin/env node
/** Validate mermaid blocks in markdown files against the mermaid@11 parser
 *  (the same major version GitHub renders with). Parse-only — no browser.
 *
 *  Setup (one-time, scratch dir — never inside the repo):
 *    D=/tmp/lpb-doc-check; mkdir -p $D
 *    cp <this script> $D/
 *    cd $D && npm install --no-audit --no-fund mermaid@11.12.2 jsdom@24
 *
 *  Usage (run the copy from the scratch dir so bare imports resolve):
 *    node /tmp/lpb-doc-check/check-mermaid.mjs file1.md [file2.md ...]
 *
 *  Exit code 1 if any block fails to parse (or a fence is unterminated).
 */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
globalThis.window = dom.window;
globalThis.document = dom.window.document;
if (!globalThis.navigator) {
  Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator });
}
// Dynamic import ON PURPOSE: static imports are hoisted and would load
// mermaid before the DOM globals exist (DOMPurify.sanitize not a function).
const { default: mermaid } = await import("mermaid");
mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

let failures = 0;
for (const file of process.argv.slice(2)) {
  const text = readFileSync(file, "utf8");
  const lines = text.split("\n");
  let inFence = false, fence = "", block = [], start = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!inFence && /^```/.test(line)) {
      inFence = true;
      fence = line.slice(3).trim();
      block = [];
      start = i + 1;
      continue;
    }
    if (inFence && /^```/.test(line)) {
      inFence = false;
      if (fence === "mermaid") {
        const src = block.join("\n");
        try {
          await mermaid.parse(src);
          console.log(`OK   ${file}:${start} (${block.length} lines)`);
        } catch (e) {
          failures++;
          console.log(`FAIL ${file}:${start}: ${e.message.split("\n")[0]}`);
        }
      }
      fence = "";
      continue;
    }
    if (inFence) block.push(line);
  }
  if (inFence && fence === "mermaid") {
    failures++;
    console.log(`FAIL ${file}:${start}: unterminated mermaid fence`);
  }
}
process.exit(failures ? 1 : 0);
