#!/usr/bin/env tsx

/**
 * browser-validate.ts — Structured validation utility with Zod + vision model
 *
 * Pipeline:
 *   1. Navigate to URL
 *   2. Pre-compute: vitals JSON, a11y JSON, snapshot text, annotated screenshot
 *   3. Encode screenshot to base64
 *   4. Construct vision model prompt with schema + pre-computed data
 *   5. Call Lemonade vision model API
 *   6. Clean JSON output (remove markdown fences, preamble)
 *   7. Validate against Zod schema
 *   8. Retry with repair prompt on failure (max 3 attempts)
 *   9. Save validated report to /browser-states/<session-id>/validated.json
 *
 * Usage: npx tsx browser-validate.ts <url> [--session <id>] [--attempts <n>]
 */

import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "zod";

// ─── Zod Schema ──────────────────────────────────────────────────────────────

const ValidationReportSchema = z.object({
  url: z.string(),
  status: z.enum(["PASS", "WARN", "FAIL"]),
  metrics: z.object({
    lcp_ms: z.number(),
    cls: z.number(),
    ttfb_ms: z.number(),
    inp_ms: z.number().optional(),
    a11y_violations: z.number(),
    a11y_passes: z.number(),
  }),
  checks: z.array(
    z.object({
      check: z.string(),
      description: z.string(),
      pass: z.boolean(),
      evidence: z.string().optional(),
    }),
  ),
  screenshot_path: z.string().optional(),
});

type ValidationReport = z.infer<typeof ValidationReportSchema>;

// ─── Agent-Browser Helpers ───────────────────────────────────────────────────

function agentBrowser(...args: string[]): string {
  const result = spawnSync("agent-browser", args, {
    encoding: "utf-8",
    timeout: 30_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `agent-browser ${args.join(" ")} failed: ${result.stderr?.toString().trim()}`,
    );
  }
  return result.stdout?.toString().trim() ?? "";
}

// ─── Task 2.4: Pre-computation ──────────────────────────────────────────────

async function precomputeData(
  url: string,
  sessionId: string,
): Promise<{ vitals: string; a11y: string; snapshot: string; screenshotPath: string }> {
  const screenshotPath = `/browser-states/${sessionId}/screenshot.png`;

  // Ensure session directory exists
  mkdirSync(`/browser-states/${sessionId}`, { recursive: true });

  // Navigate
  agentBrowser("--session", sessionId, "open", url);
  // Small delay for page to load
  await sleep(1000);

  // Collect vitals JSON
  const vitals = agentBrowser("--session", sessionId, "vitals", "--json");

  // Collect a11y JSON
  const a11y = agentBrowser("--session", sessionId, "a11y", "--json");

  // Collect snapshot text
  const snapshot = agentBrowser("--session", sessionId, "snapshot", "-i");

  // Capture annotated screenshot
  agentBrowser(
    "--session",
    sessionId,
    "screenshot",
    "--annotate",
    screenshotPath,
  );

  return { vitals, a11y, snapshot, screenshotPath };
}

// ─── Task 2.5: Image → base64 encoding ──────────────────────────────────────

function imageToBase64(path: string): string {
  return readFileSync(path).toString("base64");
}

// ─── Task 2.6: Vision model prompt construction ─────────────────────────────

const VISION_PROMPT_TEMPLATE = `You are a web application validation assistant. Analyze the provided data and produce a structured JSON validation report.

## Pre-computed Data

### Web Vitals (JSON)
{{VITALS}}

### Accessibility Audit (JSON)
{{A11Y}}

### Page Snapshot (Interactive Elements)
{{SNAPSHOT}}

## JSON Schema (you must follow this exactly)
{
  "url": "<the URL being tested>",
  "status": "PASS" | "WARN" | "FAIL",
  "metrics": {
    "lcp_ms": <number>,
    "cls": <number>,
    "ttfb_ms": <number>,
    "inp_ms": <number (optional)>,
    "a11y_violations": <number>,
    "a11y_passes": <number>
  },
  "checks": [
    {
      "check": "<check name>",
      "description": "<what was checked>",
      "pass": <boolean>,
      "evidence": "<supporting details (optional)>"
    }
  ],
  "screenshot_path": "<path to screenshot>"
}

## Instructions
1. Extract metric values from the vitals JSON above
2. Count a11y violations and passes from the a11y JSON
3. Fill in the checks array based on your analysis of the page (structure, visual, performance, accessibility)
4. Set overall status: PASS if no critical issues, WARN if minor issues, FAIL if critical failures
5. Return ONLY valid JSON. No preamble. No markdown formatting. No code fences.`;

function buildPrompt(
  _url: string,
  vitals: string,
  a11y: string,
  snapshot: string,
  screenshotPath: string,
): string {
  return VISION_PROMPT_TEMPLATE.replace("{{VITALS}}", vitals)
    .replace("{{A11Y}}", a11y)
    .replace("{{SNAPSHOT}}", snapshot)
    .replace("{{SCREENSHOT_PATH}}", screenshotPath);
}

// ─── Task 2.7: Lemonade API call ────────────────────────────────────────────

/**
 * Resolve the Lemonade base URL.
 * Priority (matches the lemonade plugin / start.sh):
 *   1. ~/.pi/agent/auth.json — persisted, verified source (same candidate
 *      order as the plugin's readStoredPayload; this is what `lpb-config
 *      show` prints)
 *   2. LEMONADE_BASE_URL env — startup default; in non-host-networked
 *      containers it may still be the 127.0.0.1 baked placeholder
 *   3. 127.0.0.1 placeholder (in-container / host-networked default)
 */
function resolveLemonadeBase(): string {
  const authPath = join(homedir(), ".pi", "agent", "auth.json");
  try {
    const auth = JSON.parse(readFileSync(authPath, "utf-8"));
    const entry =
      auth?.lemonade ?? auth?.providers?.lemonade ?? auth?.oauth?.lemonade;
    // The plugin persists `refresh` as a double-encoded JSON string; accept
    // a plain object too (defensive against future format changes).
    let refresh: unknown = entry?.refresh;
    if (typeof refresh === "string") {
      try {
        refresh = JSON.parse(refresh);
      } catch {
        refresh = undefined;
      }
    }
    const baseUrl: unknown =
      (refresh as Record<string, unknown> | undefined)?.baseUrl;
    if (typeof baseUrl === "string" && baseUrl) {
      const trimmed = baseUrl.replace(/\/$/, "");
      return /\/v\d+$/.test(trimmed) ? trimmed : `${trimmed}/v1`;
    }
  } catch {
    // no readable auth.json — fall through to env
  }
  return process.env.LEMONADE_BASE_URL || "http://127.0.0.1:13305/v1";
}

/**
 * Resolve the vision model.
 * Priority: VISION_MODEL env → agent's default model (settings.json —
 * what `lpb-config show` reports) → legacy hardcoded fallback.
 */
function resolveVisionModel(): string {
  if (process.env.VISION_MODEL) return process.env.VISION_MODEL;
  const settingsPath = join(homedir(), ".pi", "agent", "settings.json");
  try {
    const settings = JSON.parse(readFileSync(settingsPath, "utf-8"));
    if (typeof settings?.defaultModel === "string" && settings.defaultModel) {
      return settings.defaultModel;
    }
  } catch {
    // no readable settings.json — fall through
  }
  return "Qwen3.6-35B-A3B-MTP-GGUF";
}

const LEMONADE_BASE = resolveLemonadeBase();
const VISION_MODEL = resolveVisionModel();

async function callVisionModel(
  prompt: string,
  screenshotBase64: string,
  _attempt: number,
): Promise<string> {
  const payload: Record<string, unknown> = {
    model: VISION_MODEL,
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: prompt },
          {
            type: "image_url",
            image_url: { url: `data:image/png;base64,${screenshotBase64}` },
          },
        ],
      },
    ],
    max_tokens: 2048,
    stream: false,
  };
  // Qwen models think by default; on a small token budget the reasoning can
  // eat the whole budget and return empty content (flaky JSON output). This
  // task is structured extraction, not reasoning — disable thinking via the
  // vendor wire parameter (the same control the pi plugin sends).
  if (/qwen/i.test(VISION_MODEL)) {
    payload.enable_thinking = false;
  }

  const response = await fetch(`${LEMONADE_BASE}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Lemonade API error ${response.status}: ${text}`);
  }

  const raw = await response.json();
  const choices = (raw as Record<string, unknown>).choices as
    | unknown[]
    | undefined;
  const choice = choices?.[0] as Record<string, unknown> | undefined;
  const msg = choice?.message as Record<string, unknown> | undefined;

  // Extract reasoning_content first, then content fallback
  const content = (msg?.reasoning_content || msg?.content || "") as string;
  return content.trim();
}

// ─── Task 2.10: JSON cleaning ───────────────────────────────────────────────

function cleanJsonOutput(raw: string): string {
  // Remove markdown code fences (```json ... ```)
  let cleaned = raw.replace(/```(?:json)?\s*/g, "").replace(/```/g, "");
  // Remove preamble text (everything before the first {)
  const firstBrace = cleaned.indexOf("{");
  if (firstBrace > 0) {
    cleaned = cleaned.slice(firstBrace);
  }
  // Remove trailing text after the last }
  const lastBrace = cleaned.lastIndexOf("}");
  if (lastBrace < cleaned.length - 1) {
    cleaned = cleaned.slice(0, lastBrace + 1);
  }
  return cleaned.trim();
}

// ─── Task 2.8: Zod validation ───────────────────────────────────────────────

function validateReport(raw: string): {
  success: boolean;
  data?: ValidationReport;
  error?: string;
} {
  try {
    const parsed = JSON.parse(raw);
    const result = ValidationReportSchema.safeParse(parsed);
    if (!result.success) {
      return {
        success: false,
        error: result.error.errors
          .map(e => `${e.path.join(".")}: ${e.message}`)
          .join("; "),
      };
    }
    return { success: true, data: result.data };
  } catch (e) {
    return {
      success: false,
      error: `JSON parse error: ${e instanceof Error ? e.message : String(e)}`,
    };
  }
}

// ─── Task 2.9: Repair loop ──────────────────────────────────────────────────

async function validateWithRetry(
  prompt: string,
  screenshotBase64: string,
  maxAttempts: number,
): Promise<ValidationReport> {
  let lastError = "";

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    console.log(`  Attempt ${attempt}/${maxAttempts}...`);

    let currentPrompt = prompt;
    if (attempt > 1 && lastError) {
      currentPrompt += `\n\n## Previous Validation Error\n${lastError}\nPlease fix the JSON and return valid output.`;
    }

    const raw = await callVisionModel(currentPrompt, screenshotBase64, attempt);
    const cleaned = cleanJsonOutput(raw);

    const result = validateReport(cleaned);
    if (result.success) {
      console.log(`  ✓ Validation passed on attempt ${attempt}`);
      return result.data!;
    }

    lastError = result.error || "Unknown validation error";
    console.log(`  ✗ Validation failed: ${lastError}`);
  }

  throw new Error(
    `Validation failed after ${maxAttempts} attempts. Last error: ${lastError}`,
  );
}

// ─── Task 2.11: Save result ─────────────────────────────────────────────────

function saveReport(sessionId: string, report: ValidationReport): string {
  const outputDir = `/browser-states/${sessionId}`;
  mkdirSync(outputDir, { recursive: true });
  const outputPath = `${outputDir}/validated.json`;
  writeFileSync(outputPath, JSON.stringify(report, null, 2));
  console.log(`  Report saved to ${outputPath}`);
  return outputPath;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const url = args.find(a => !a.startsWith("--"));

  // Flag values: an absent flag must fall back, not pick up args[0] (the URL)
  // via the old `args[indexOf(flag) + 1]` pattern (indexOf === -1 → args[0]).
  const flagValue = (flag: string): string | undefined => {
    const i = args.indexOf(flag);
    return i >= 0 && i + 1 < args.length ? args[i + 1] : undefined;
  };

  const sessionId =
    flagValue("--session") ??
    `${process.env.PI_WORKTREE_ID ?? "default"}-validate-${randomUUID().slice(0, 8)}`;
  const parsedAttempts = parseInt(flagValue("--attempts") ?? "3", 10);
  const maxAttempts =
    Number.isFinite(parsedAttempts) && parsedAttempts > 0 ? parsedAttempts : 3;

  if (!url) {
    console.error(
      "Usage: npx tsx browser-validate.ts <url> [--session <id>] [--attempts <n>]",
    );
    process.exit(1);
  }

  console.log(`\n=== Browser Validation ===`);
  console.log(`URL: ${url}`);
  console.log(`Session: ${sessionId}`);
  console.log(`Max attempts: ${maxAttempts}`);
  console.log(`Vision: ${VISION_MODEL} @ ${LEMONADE_BASE}\n`);

  try {
    // Step 1: Pre-compute structured data
    console.log("→ Pre-computing structured data...");
    const { vitals, a11y, snapshot, screenshotPath } = await precomputeData(
      url,
      sessionId,
    );
    console.log("  ✓ Data collected");

    // Step 2: Encode screenshot
    console.log("→ Encoding screenshot...");
    const screenshotBase64 = imageToBase64(screenshotPath);
    console.log("  ✓ Encoded");

    // Step 3: Build prompt
    console.log("→ Building vision model prompt...");
    const prompt = buildPrompt(url, vitals, a11y, snapshot, screenshotPath);

    // Step 4: Call vision model with validation + repair loop
    console.log("→ Calling vision model...");
    const report = await validateWithRetry(
      prompt,
      screenshotBase64,
      maxAttempts,
    );

    // Step 5: Save report
    console.log("→ Saving report...");
    const savedPath = saveReport(sessionId, report);

    console.log(`\n=== Validation Complete ===`);
    console.log(`Status: ${report.status}`);
    console.log(`Checks: ${report.checks.length}`);
    console.log(`Saved: ${savedPath}\n`);
  } catch (error) {
    console.error(
      `\n✗ Validation failed: ${error instanceof Error ? error.message : String(error)}`,
    );
    process.exit(1);
  } finally {
    // Always close the browser session to prevent zombie Chrome processes
    console.log("→ Closing browser session...");
    try {
      agentBrowser("--session", sessionId, "close");
      console.log("  ✓ Session closed");
    } catch {
      console.log("  [WARN] Failed to close session (may already be closed)");
    }
  }
}

main();
