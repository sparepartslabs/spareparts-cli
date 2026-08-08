# sp

The Spare Parts command line.

```
pip install ".[anthropic]"     # or [openai], or [gemini], or [all]
sp
```

## Modules

| Module | What it does |
|---|---|
| `sp ec` | Install Spec Kit-derived engineering-context commands |
| `sp lgtm` | Prove you read a diff before you merge it |

---

## `sp ec`

Installs the Spec Kit slash commands and their shared `.sp/` working area into
a repo. Existing `.blitz/` working areas are migrated automatically, including
renaming `memory/playbook.md` to `memory/constitution.md`.

```sh
sp ec install                    # auto-detect the coding agent
sp ec install --agent claude
sp ec install --all
sp ec install --dir path/to/repo
```

When `--dir` is a folder of git repos, each repo gets its own commands and
`.sp/` area. The workspace root gets the cross-repo `/huddle` command and a
workspace constitution, but no pooled repo scaffold.

The installer appends ignore rules for generated `.sp` state and templates,
while keeping `.sp/memory/constitution.md` trackable. Blanket `.sp/` ignores
are left untouched and reported as warnings.

### GitHub Projects huddle store

Configure a workspace-level GitHub Project, then check access or synchronize a
huddle manually:

```sh
sp ec project configure https://github.com/orgs/example/projects/1 --dir ..
sp ec project status --dir ..
sp ec project sync ../.sp/huddles/001-example/huddle.md --dry-run
sp ec project sync ../.sp/huddles/001-example/huddle.md
```

`/huddle` prefers installed GitHub MCP tools capable of managing Projects. It
falls back to `gh project` through `sp ec project sync`. Each draft item carries
a stable huddle-path marker, so later syncs update it instead of creating a
duplicate. Markdown remains authoritative when remote synchronization fails.

---

## `sp lgtm`

Generates a few multiple-choice questions about a range of commits and asks
them, in your terminal. The questions are about what the change *does* — what a
new guard prevents, what the error path now returns, which edit can touch
existing rows — never about statistics, naming, or formatting.

```sh
sp lgtm                       # what this branch adds since it left main
sp lgtm main...feature/x      # someone else's branch, before you merge it
sp lgtm -n 3 -d hard
sp lgtm --dry-run             # what it would ask about, no model call
```

Generation is three model calls, so expect it to take a moment — this is a thing
you run before a merge, not on every commit.

### Providers

Anthropic, OpenAI and Gemini, and no vendor is the assumed one. Install the SDK
for whichever you use:

```sh
pip install ".[anthropic]"     # or [openai], or [gemini], or [all]
```

Name nobody and `sp` uses whichever key you have set. With more than one set it
picks in the order below, which is a tie-break rather than a ranking; name
`provider:` in `.github/lgtm.yml` to decide it yourself.

| Vendor | Key | Default model | Typical run |
|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5` | ~55s |
| `openai` | `OPENAI_API_KEY` | `gpt-5.5` | ~65s |
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-pro-latest` | ~115s |

```sh
sp lgtm -p openai
sp lgtm -p gemini --model gemini-3.6-flash
sp lgtm -p anthropic --verifier openai     # see below
```

All three are exercised against live APIs, on a 32KB diff, and the timings above
are from those runs.

The defaults were chosen by listing each vendor's models with a live key. Gemini
uses the tracking alias because the vendor currently publishes no plain
`gemini-3.x-pro` — only `-image` variants — so pinning a pro model would mean
pinning to 2.5 indefinitely. The other two pin, because a default that changes
underneath a quiz changes what the quiz asks.

### Two vendors are better than one

`sp lgtm` writes a question with one call and then asks a second call to refute
it. A candidate that can't be refuted survives; everything else is dropped.

Both calls going to the same model is the weak version of that check — a model
asked to find fault with its own reasoning mostly doesn't. If you have keys for
two vendors, split them:

```sh
sp lgtm --provider anthropic --verifier openai
```

That is the strongest arrangement available, and it is why the provider layer
exists rather than a bare `--model` flag. It costs one extra vendor's tokens and
nothing else — the verifier sees the question and the diff, never the
proposer's reasoning.

It also costs no extra wall-clock in practice: proposing and verifying are
sequential either way, and the cross-vendor run above came in at 68s against
55s for Anthropic alone.

### It is a self-check, not a gate

`sp lgtm` has no way to stop you doing anything. The answers live in the same
process as the questions, on your machine, and you can skip the whole thing.
That is on purpose: the version that actually gates is the
[GitHub Action](https://github.com/sparepartslabs/spareparts-lgtm), which asks
the *reviewer* after they approve and holds a check run open until they answer.

What local gets in exchange is the thing the Action can't have: the code is
checked out. Press `?` on any question to print the hunk it came from. Wrong
answers are never a failure — you get told which files to look at again, and it
re-asks, as many times as you like.

### Configuration

Reads `.github/lgtm.yml` from the repo you're in, the same file the Action uses,
so a repo is configured once:

```yaml
questions: 2          # 1-5
difficulty: medium    # easy | medium | hard
provider: anthropic   # anthropic | openai | gemini, or vendor:model
verifier: openai      # optional; defaults to the proposer
exemptPaths:
  - "docs/**"
```

`provider` and `verifier` are read here but validated by the provider layer, so
a typo is reported with the list of known vendors rather than silently ignored.

Keys that only mean something to the Action (`enforce`, `webConcepts`,
`surfaceReading`, `answerQuestions`, `exemptReviewers`) are accepted and
ignored. `-n` and `-d` override the file.

Lockfiles, `dist/`, `vendor/`, `*.pbxproj` and friends are never quizzed.

### As a git hook

```sh
sp lgtm install                       # pre-push, advisory
sp lgtm install --hook pre-commit     # earlier, and once per commit
sp lgtm install --blocking            # wrong answers stop the push
sp lgtm uninstall
```

**`pre-push` is the default.** The old framing for this tool was "the person
answering didn't write the code" — that's why it quizzes a *reviewer*. That
framing is dated: when a model wrote the diff, nobody in the loop wrote it, and
the author is as much a reader as anyone. The useful question isn't who typed
it, it's where the last cheap moment to catch it is — and that's before the code
leaves your machine, which is left of anything the Action can do.

It also matches how the cost lands. Generation is three model calls and about a
minute; per *push* that's fine, per *commit* it taxes every checkpoint you save.
`--hook pre-commit` is there if you want it, and quizzes the staged diff.

**Advisory by default.** A hook that costs a minute *and* can stop you is one
you delete within a week. `--blocking` is opt-in, and even then only a wrong
answer (exit 1) blocks — "couldn't ask" (exit 2: no API key, vendor outage,
nothing quizzable) never costs you a push.

Escape hatches, in the order you'll want them:

```sh
SP_LGTM_SKIP=1 git push ...    # skip this once
git push --no-verify ...       # skip every hook
```

#### What it reads

`pre-push` quizzes exactly what you're about to push. Git names the refs on
stdin, so the hook takes `<remote sha>..<local sha>` — the commits the remote
doesn't have yet. A branch the remote has never seen has no such range, so it
falls back to what the branch adds since it left the default branch. A push that
only *deletes* a remote branch reads nothing at all.

That ref list arrives on the same stdin the quiz needs for answers, so the hook
consumes it before attaching `/dev/tty`.

It skips itself silently when there's no terminal — a rebase, a GUI client, CI.
Git runs hooks with stdin closed; where there's no tty, nobody can answer and
nobody failed.

`sp lgtm install` refuses to overwrite a hook it didn't write (`--force`
overrides), and honours `core.hooksPath` — writing to an assumed `.git/hooks`
when that's set installs a hook that never runs, which looks exactly like
success.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Confirmed |
| 1 | Not confirmed — wrong answers, or you quit |
| 2 | Couldn't ask — no API key, git failed, nothing quizzable |

If you wire this into a git hook, treat only `1` as a failure. A tool that
cannot run must not block a commit.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

`lgtm`'s diff parsing, config, screening and generator are a port of the
TypeScript in
[`spareparts-lgtm`](https://github.com/sparepartslabs/spareparts-lgtm). The
prompts in `generator.py` are the part worth keeping identical — changing the
wording here without changing it there produces two tools that disagree about
the same diff.
