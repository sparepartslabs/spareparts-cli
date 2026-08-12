# Tasks: Issue Ingestion and Repository Routing

## Phase 1: Setup

- [x] T001 Register the lazy ingest module in `src/spareparts/cli.py`
- [x] T002 Create the ingest package command parser in `src/spareparts/modules/ingest/__init__.py`

## Phase 2: Foundational

- [x] T003 Define and validate issue, ontology, routing, and reviewer models in `src/spareparts/modules/ingest/models.py`
- [x] T004 Implement bounded GitHub and authenticated Core HTTP clients in `src/spareparts/modules/ingest/clients.py`

## Phase 3: User Story 1 - Configure ingestion provider

**Goal**: Select exactly one optional provider/model with runtime credentials.

**Independent test**: Parse provider forms and run with an injected provider without exposing credentials.

- [x] T005 [P] [US1] Add parser, credential, provider-label, and bare-help tests in `tests/test_ingest.py`
- [x] T006 [US1] Resolve the existing optional provider and safe command outcomes in `src/spareparts/modules/ingest/__init__.py`

## Phase 4: User Story 2 - Link affected repositories

**Goal**: Reuse or create an ontology and produce catalog-constrained repository links.

**Independent test**: Route a fixture and reject unknown repository IDs while accepting an empty result.

- [x] T007 [P] [US2] Add ontology, source identity, routing validation, and Core request tests in `tests/test_ingest.py`
- [x] T008 [US2] Implement ontology refresh, model routing, validation, and idempotent Core submission in `src/spareparts/modules/ingest/service.py`

## Phase 5: User Story 3 - Recommend an experienced approver

**Goal**: Attach deterministic bounded APPROVED-review evidence without failing links.

**Independent test**: Verify state filtering, bot exclusion, deduplication, ties, and unavailable evidence.

- [x] T009 [P] [US3] Add reviewer selection and partial-enrichment tests in `tests/test_ingest.py`
- [x] T010 [US3] Implement bounded review gathering and deterministic approver selection in `src/spareparts/modules/ingest/service.py`

## Phase 6: Polish and Cross-Cutting Concerns

- [x] T011 Document the public command and runtime-only secret behavior in `README.md`
- [x] T012 Run focused, full-suite, compile, and bare-help checks and mark every task complete in `specs/004-issue-ingestion/tasks.md`

## Dependencies

Setup precedes foundational work. US1 precedes US2 because routing requires a selected model. US3 depends on US2 links. Documentation and full validation follow all stories.

## Parallel Opportunities

Test fixture work marked `[P]` can be prepared independently from implementation files after foundational contracts exist.

## Implementation Strategy

Deliver US1 as the provider-selection MVP, then add catalog-constrained routing, then optional approval evidence. Each phase is independently fixture-testable.
