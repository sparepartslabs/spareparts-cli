# sp

The Spare Parts command line.

```
pip install -e .
sp
```

## Modules

| Module | What it does |
|---|---|
| `sp lgtm` | Prove you read a diff before you merge it |

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

Anthropic, OpenAI and Gemini. A bare install ships Anthropic; the other two are
extras:

```sh
pip install -e ".[openai]"     # or [gemini], or [all]
```

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
sp lgtm install                       # pre-commit, advisory
sp lgtm install --hook pre-push       # the closer analogue — see below
sp lgtm install --blocking            # wrong answers stop the commit
sp lgtm uninstall
```

The hook quizzes what is **staged** (`git diff --cached`), since a pre-commit
hook has no revision range to name.

**Advisory by default.** Generation is three model calls and about a minute. A
hook that costs a minute *and* can stop your commit is a hook you delete within
a week; one that just tells you is one you keep. `--blocking` is there when you
want it, and even then only a wrong answer (exit 1) blocks — "couldn't ask"
(exit 2, no API key, vendor outage, nothing quizzable) never costs you a commit.

Escape hatches, in the order you'll want them:

```sh
SP_LGTM_SKIP=1 git commit ...    # skip this once
git commit --no-verify ...       # skip every hook
```

It skips itself silently when there's no terminal — a rebase, a GUI client, CI.
Git runs hooks with stdin closed, so the hook attaches `/dev/tty`; where there
isn't one, nobody can answer and nobody failed.

`sp lgtm install` refuses to overwrite a hook it didn't write (`--force`
overrides), and honours `core.hooksPath` — writing to an assumed `.git/hooks`
when that is set installs a hook that never runs, which looks exactly like
success.

#### Which hook

`pre-commit` is the default because it's what people ask for, but it's worth
knowing what it is. LGTM's premise is that the person answering *didn't write
the code* — that's the point of quizzing a reviewer. At pre-commit time the
author is you, seconds after typing it. That's a proofreading pass, and a fair
use of the tool: it catches the change you made without noticing what else it
touched. It isn't the same thing the Action does.

`pre-push` is the closer analogue — the moment you hand work to someone else,
and the moment a merge brings in code you didn't write. It also costs a minute
per *push* rather than per *commit*, which is usually the trade you want.

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
