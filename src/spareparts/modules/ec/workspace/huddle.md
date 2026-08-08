---
description: Orchestrate a cross-repo feature across the Spec Kit-enabled repos in this workspace.
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Context

You are running at the **workspace root** — a folder that contains multiple git repos, each with its own `.sp/` working area and Spec Kit slash-commands (`__SPECKIT_COMMAND_SPECIFY__`, `__SPECKIT_COMMAND_PLAN__`, `__SPECKIT_COMMAND_TASKS__`, `__SPECKIT_COMMAND_IMPLEMENT__`, ...). Those commands are **repo-scoped**: they assume their working directory is a single repo root. Your job is the layer above them — split a workspace-level initiative into per-repo work, drive each repo's own Spec Kit lifecycle, and keep a single source of truth for the cross-repo effort.

**Golden rule**: never write specs, plans, or tasks into the workspace root's `.sp/` or a shared `specs/` directory. Repo-scoped artifacts live in their repo. The only workspace-level artifact is the huddle document described below.

## Step 1 — Discover the workspace

1. Find every Spec Kit-enabled repo: directories at or below the workspace root (max depth 4) that contain both `.git` and `.sp/`. Skip hidden dirs and `node_modules`, `__pycache__`, `.venv`, `venv`, `vendor`, `dist`, `build`. Do not descend inside a repo looking for nested repos. A single `find`/`ls` pass is fine, e.g.:
   ```bash
   find . -maxdepth 5 -name .sp -type d -not -path '*/node_modules/*' -not -path '*/.*/*' | sort
   ```
   and keep only those whose parent directory also has `.git`.
2. For each repo, gather a one-line profile:
   - repo path (relative to the workspace root)
   - what it is (read its README first paragraph or top-level layout if you don't already know)
   - active specification work: list `specs/*/` directories and, if present, `.sp/feature.json` (the repo's current feature directory)
3. If the user's input is empty or is `status`, stop after this step and report: a table of repos, their active specs, and each spec's lifecycle stage (spec only / plan present / tasks present / implementation in progress — infer from which files exist in the feature directory). Then suggest next actions per repo.

## Step 2 — Create or resume the huddle

Huddles live at the workspace root under `.sp/huddles/`.

1. If the user's input clearly refers to an existing huddle (by name or number), or exactly one huddle exists and the input reads as a continuation, **resume** it: read its `huddle.md`, reconcile the status board against reality (re-check each repo's spec/plan/tasks files), and continue from the first incomplete step.
2. Otherwise **create** one:
   - Generate a 2-4 word kebab-case short name from the initiative description.
   - Number it sequentially: next available `NNN` after scanning `.sp/huddles/`.
   - `mkdir -p .sp/huddles/NNN-<short-name>` and write `huddle.md` there:

   ```markdown
   # Huddle: [INITIATIVE NAME]

   **Created**: [DATE]
   **Status**: active
   **Initiative**: [one-paragraph statement of the cross-repo goal, from the user input]

   ## Repo Breakdown

   | Repo | Role in this initiative | Spec | Stage |
   |------|------------------------|------|-------|
   | path/to/repo-a | [what changes here and why] | specs/NNN-... (once created) | not-started |

   ## Interfaces & Contracts

   [Anything two repos must agree on: API shapes, event schemas, env vars,
   version constraints. Each contract names a producer repo and consumer repo(s).]

   ## Sequencing

   [Ordered phases. Which repo work can proceed in parallel, which is blocked
   on a contract or another repo's implementation, and why.]

   ## Decision Log

   - [DATE] [decision + rationale]
   ```

   The `**Status**` line MUST begin with one of the canonical status tokens so it renders consistently everywhere the huddle is surfaced (the ontology graph, the locker-room board, the iOS app):
   - `active` — work in progress
   - `blocked` — stalled on a dependency or decision
   - `complete` — shipped / merged / done
   Trailing prose after the token is allowed and ignored by the parser (e.g. `**Status**: complete (merged 2026-07-18, PR #23)`). Any other leading token, or a missing Status line, is treated as `unknown`. Do not invent new status words (`done`, `merged`, `shipped`, `paused`, etc.) — use `complete` or `blocked`.

## Step 3 — Split the initiative into per-repo specs

1. Decide which repos are actually touched. Be conservative: prefer fewer repos with clear responsibilities. Record repos considered-but-excluded (and why) in the Decision Log.
2. Draft the Interfaces & Contracts section **before** writing any per-repo spec — contracts are the thing repo-scoped specs can't see, and they are the main reason this command exists.
3. For each affected repo, produce a self-contained per-repo feature description: what this repo must deliver, the contracts it produces/consumes (quoted inline, not referenced by path — the repo-scoped agent may not read outside its repo), and its acceptance criteria.
4. Run each repo's own spec flow with that description. For each repo, from **inside that repo** (either `cd` there, or spawn a subagent/Task whose working directory is that repo — one per repo, parallel where sequencing allows):
   - Follow the repo's `.claude/commands/specify.md` (or the equivalent for the agent tool in use) with the per-repo description as the argument. Running the command file's instructions with cwd set to the repo root is equivalent to the user invoking `__SPECKIT_COMMAND_SPECIFY__` there.
   - Record the resulting feature directory back in the huddle's Repo Breakdown table and set its Stage to `specified`.
5. Update `huddle.md` after **every** repo-level state change. The huddle document must always reflect reality; it is the resumption point if this session dies.

## Step 4 — Drive the lifecycle

For each repo, in the order the Sequencing section dictates, advance its feature through the repo's own Spec Kit commands the same way (execute the repo's command file with cwd inside that repo):

1. `__SPECKIT_COMMAND_CLARIFY__` — only if the spec has open questions; bubble questions the user must answer up to this session rather than answering them yourself when they affect cross-repo contracts.
2. `__SPECKIT_COMMAND_PLAN__` — pass the relevant contracts as part of the planning input.
3. `__SPECKIT_COMMAND_TASKS__`, then `__SPECKIT_COMMAND_IMPLEMENT__` — only when the user has asked for implementation, not just specification/planning.

Rules:

- **Contract changes propagate.** If any repo's plan forces a contract change, stop, update Interfaces & Contracts and the Decision Log, then revisit every other repo whose spec consumed that contract before proceeding.
- **Stage gates.** Do not start a repo's plan while a contract it consumes is still unsettled. Parallelize freely otherwise.
- **Checkpoint with the user** between lifecycle stages (after all specs, after all plans) unless they explicitly asked you to run further.

## Completion Report

Report to the user:

- Huddle directory path and current Status
- The Repo Breakdown table (repo, spec path, stage)
- Contracts agreed and any still open
- The next action per repo, and what — if anything — you are blocked on
