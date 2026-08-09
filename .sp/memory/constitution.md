<!--
SYNC IMPACT REPORT
Version change: unfilled template -> 1.0.0
Rationale: First evidence-based CLI constitution.
Principles: Typed CLI Contracts; Safe Idempotent Mutation; Optional Providers;
Compatibility and Artifacts; Supported Behavior Tests.
Templates checked: OK .sp/templates/plan-template.md, spec-template.md,
tasks-template.md, and .claude/commands/constitution.md.
-->

# Spare Parts CLI Constitution

## Stack & Constraints

sp supports Python 3.11 through 3.13, uses argparse and a thin dependency set, installs
agent commands and .sp scaffolding, and releases from Git tags. Model SDKs are extras.

## Core Principles

### I. Typed and Predictable CLI Contracts

Functions MUST have useful annotations. Parser input, YAML, provider responses, and file
payloads MUST be validated. Commands MUST use stable exit codes, stdout for results, and
stderr for actionable errors. Help and README examples MUST match the parser.

### II. Safe, Idempotent Repository Mutation

Install, configure, sync, pull, and hook operations MUST be idempotent. User files MUST
survive unless an explicit force option authorizes replacement. Writes MUST stay within
the resolved target and be atomic where partial output is dangerous. Keys MUST never be
persisted. Legacy-layout migrations MUST preserve authored content and have tests.

### III. Optional Providers Stay Optional

Anthropic, OpenAI, and Gemini MUST remain isolated behind extras. A bare install MUST
import and show help without provider SDKs. Missing extras MUST produce an actionable
error without breaking unrelated providers. Base dependencies require evidence that all
users need them.

### IV. Compatibility and Artifact Integrity

The supported Python range, CLI syntax, installed layout, and configuration formats are
product contracts. Breaking changes require migration guidance and a breaking release.
Built distributions MUST include prompts and command assets. Versions MUST come from Git
tags, never a source constant.

### V. Tests Define Supported Behavior

Command and option changes MUST have parser and behavior tests. Filesystem changes MUST
use temporary directories; network boundaries MUST be deterministic. The Python matrix,
bare-install job, pytest suite, and distribution checks MUST pass before release.

## Review Process

Critical findings include secret persistence, destructive mutation, broken bare install,
missing package data, or an undocumented CLI break. Every finding MUST cite file and
line, state impact, and propose a focused fix. Shared parser, installer, config, and
provider edits require the full suite.

## Governance

Constitution Check exceptions MUST be justified. All commits MUST follow
Conventional Commits. fix and perf produce patches, feat produces minors, and ! or a
BREAKING CHANGE footer produces a breaking release; other types do not release alone.
No AI or tool attribution is allowed in commits.

Amendments require evidence and semantic constitution versions: MAJOR removes or
redefines a principle, MINOR adds or expands one, PATCH clarifies. CONTRIBUTING.md
remains authoritative for release mechanics.

**Version**: 1.0.0 | **Ratified**: 2026-08-09 | **Last Amended**: 2026-08-09
