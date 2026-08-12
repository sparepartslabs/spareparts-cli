# Implementation Plan: Issue Ingestion and Repository Routing

**Branch**: `004-issue-ingestion` | **Date**: 2026-08-11 | **Spec**: [spec.md](spec.md)

## Summary

Add a lazy-loaded `sp ingest issue` command which validates a GitHub Actions event,
resolves one existing optional model provider, obtains or creates a bounded organization
repository ontology through GitHub and Core, constrains structured model routing to that
catalog, gathers bounded approval-review evidence, and submits an idempotent result to Core.

## Technical Context

**Language/Version**: Python 3.11-3.13

**Primary Dependencies**: stdlib `argparse`, `urllib`, `json`; existing optional Anthropic/OpenAI/Gemini extras

**Storage**: Core HTTP service selected by `SPAREPARTS_INGEST_KEY`; no local durable state or secrets

**Testing**: pytest with injected deterministic HTTP/provider boundaries

**Target Platform**: Linux/macOS CLI and one-shot GitHub Actions containers

**Project Type**: CLI

**Performance Goals**: bounded API collection; no unbounded pagination; one model call per event

**Constraints**: no secret persistence/logging; catalog-constrained routing; deterministic idempotency; bare help without SDKs

**Scale/Scope**: one GitHub organization per invocation, at most 100 repositories and 100 recent pull requests per affected repository

## Constitution Check

*GATE: pass before and after design.*

- Typed CLI contract: pass; inputs and model/Core payloads are validated.
- Safe/idempotent mutation: pass; Core source identity is stable and no local files are written.
- Optional providers: pass; provider import remains lazy and SDKs remain extras.
- Compatibility/artifacts: pass; additive module and command only.
- Supported behavior tests: required parser, fixture, HTTP, provider, and full-suite tests.

Post-design re-check: pass. Standard-library clients keep dependencies thin, and all
network/provider seams accept injected callables for deterministic tests.

## Project Structure

### Documentation (this feature)

```text
specs/004-issue-ingestion/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code

```text
src/spareparts/
├── cli.py
├── providers/
└── modules/ingest/
    ├── __init__.py
    ├── clients.py
    ├── models.py
    └── service.py

tests/
└── test_ingest.py
```

**Structure Decision**: Follow the existing lazy module package convention. Keep data
validation, HTTP clients, and orchestration separated inside `modules/ingest` while
testing the public command and pure evidence selection in one focused test module.

## Complexity Tracking

No constitution violations.
