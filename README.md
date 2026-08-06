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

| Vendor | Key | Default model |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5` |
| `openai` | `OPENAI_API_KEY` | `gpt-5` |
| `gemini` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `gemini-2.5-pro` |

```sh
sp lgtm -p openai
sp lgtm -p gemini --model gemini-2.5-flash
sp lgtm -p anthropic --verifier openai     # see below
```

Only the Anthropic default has been exercised against a live API by this repo.
The other two defaults are conservative picks; if a vendor has renamed or
retired one, the failure is loud — the vendor's own "model not found" reaches
the terminal — and the fix is `--model` or the `model:` key in the config.

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
