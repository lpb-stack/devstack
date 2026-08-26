# Subagent Spawning Pattern — Browser Testing

## Overview

When spawning subagents for browser testing, use the following pattern to ensure
isolated sessions and structured JSON output.

## Steps

### 1. Generate a Unique Session ID

```bash
SESSION_ID=$(session-uuid "${PI_WORKTREE_ID}-<test-name>")
```

Example output: `pi-main-abc123-login-flow-7f3a9b2c`

### 2. Load the System Prompt Template

```bash
PROMPT=$(cat /opt/pi-support/config/subagent-browser-prompt.txt)
```

The template at `/opt/pi-support/config/subagent-browser-prompt.txt` includes:
- The JSON schema the subagent must produce
- Instructions for using agent-browser with bundled Chrome
- Metrics collection steps (vitals, a11y, snapshot)

### 3. Spawn the Subagent

Use the Pi `Agent` tool with `subagent_type: "browser-automation"`:

```javascript
Agent(
  description: "Browser test on https://example.com",
  subagent_type: "browser-automation",
  prompt: `Run browser test on https://example.com:
    1. Navigate to the URL
    2. Verify the page loads correctly
    3. Check for accessibility issues
    4. Measure performance metrics
    
    Session ID: ${SESSION_ID}`,
  thinking: "low",
  isolation: "worktree"
)
```

### 4. Collect and Validate the Result

The subagent returns structured output directly via its completion. Close the
browser session when done:

```bash
agent-browser --session "$SESSION_ID" close
```

### 5. Retry on Failure (Max 3 Attempts)

If the output needs validation, use the support utility:

```bash
echo "${SUBAGENT_OUTPUT}" | tsx /opt/pi-support/validate-subagent-output.ts
agent-browser --session "$SESSION_ID" close
```

## Key Points

- **Always use bundled Chrome** (not CDP) for subagents
- **Always set `AGENT_BROWSER_SESSION`** to a unique value
- **Always validate** the output on the parent side
- **Always retry** with a repair prompt if validation fails
- **Cap at 3 attempts** to avoid infinite loops
- **Close browser sessions** — without it, Chrome processes become zombies

## File Reference

| Path | Purpose |
|---|---|
| `session-uuid` (installed at `/opt/pi-support/`) | Generate unique session IDs |
| `/opt/pi-support/schemas/browser-validation-schema.json` | Unified JSON schema (all fields) |
| `/opt/pi-support/config/subagent-browser-prompt.txt` | System prompt template |
| `/opt/pi-support/validate-subagent-output.ts` | Parent-side validation utility |
| `/opt/pi-support/browser-validate.ts` | Browser validation entry point |
