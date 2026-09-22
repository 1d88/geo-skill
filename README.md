# GEO Skill

A Codex Agent Skill for repeatable Google SEO and GEO audits. It creates an evidence-based baseline, prioritizes remediation, and defines verifiable retests without confusing observable technical signals with Search Console performance.

## Install in a project

Copy the skill directory into the target repository:

```text
.agents/skills/google-seo-audit/
```

Codex discovers repository skills automatically when it runs from that repository.

## Run with Codex CLI

```bash
codex exec 'Use $google-seo-audit to audit https://example.com and save the report in reports/seo/'
```

For a machine-readable event stream:

```bash
codex exec --json 'Use $google-seo-audit to audit https://example.com'
```

## Call from Node.js

Use `child_process.spawn()` with an argument array and send the prompt over stdin. Do not construct a shell command from user input.

```ts
import { spawn } from "node:child_process";

const child = spawn("codex", ["exec", "--json", "-"], {
  cwd: process.cwd(),
  shell: false,
  env: process.env,
});

child.stdin.end(
  "Use $google-seo-audit to audit https://example.com and return an evidence-based report.",
);

child.stdout.on("data", chunk => {
  for (const line of chunk.toString().split("\n")) {
    if (line.trim()) console.log(JSON.parse(line));
  }
});
```

For a production web service, run audits as background jobs, validate submitted URLs, limit concurrency, apply per-request timeouts (not an arbitrary overall audit deadline), and store results outside process memory.

## Repeatable task contract

The versioned skill owns the audit method and evidence rules. A host can pass a task JSON file instead of repeating SEO instructions in every prompt. See [.agents/skills/google-seo-audit/references/task-replay.md](.agents/skills/google-seo-audit/references/task-replay.md) for inputs and saved artifacts.

- `audit`: collect a new baseline and assess available read-only GSC evidence.
- `replay`: reassess saved evidence without fetching live data; record any skill-version change.
- `retest`: recollect with comparable scope and compare against the baseline.

Reports distinguish verified facts, supported but inconclusive claims, and unknowns. Technical fixes, Google's indexed records, and search performance changes are assessed separately. Each conclusion references evidence and its time/scope. These are skill execution instructions, not an implemented web scheduler or a guarantee of identical model output.

## Contents

- `SKILL.md`: orchestration workflow and evidence boundaries
- `scripts/seo_audit.py`: optional zero-dependency baseline and comparison helper
- `references/reporting.md`: conclusion levels, evidence references and prioritization rules
- `references/task-replay.md`: task inputs, replay modes and persistent artifacts
- `agents/openai.yaml`: user-facing skill metadata


## Read-only Google Search Console evidence

The skill now includes `scripts/gsc_collect.py` and `references/search-console.md` for the official Search Console API. An external collector obtains only explicitly selected datasets using `webmasters.readonly`; Codex receives a checksummed evidence snapshot, not Google credentials. Supported reads cover site/page performance, exact-page queries, URL Inspection, and sitemaps. No GSC write operations are implemented.

See [.agents/skills/google-seo-audit/references/search-console.md](.agents/skills/google-seo-audit/references/search-console.md) for service-account and OAuth-token setup, commands, output contracts, and evidence boundaries. Account authorization and credential isolation are performed by the host application; this repository does not implement a web OAuth login screen.

Run offline validation with `python3 -m unittest discover -s tests -v`.
