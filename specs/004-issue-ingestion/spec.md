# Feature Specification: Issue Ingestion and Repository Routing

**Created**: 2026-08-11
**Status**: Ready

## User Scenarios & Testing

### User Story 1 - Configure ingestion provider (Priority: P1)

As a repository owner, I can select one ingestion provider and model while supplying its credential securely at runtime.

**Independent Test**: Invoke each supported provider/model with its matching secret and verify selection without credential persistence.

**Acceptance Scenarios**:

1. Given a supported provider, model, and matching runtime key, when ingestion runs, then that model is used and its identity is recorded.
2. Given a missing or mismatched key, when ingestion starts, then it fails with actionable guidance before analysis.
3. Given any run, when files, logs, and submitted results are inspected, then no API key is present.

### User Story 2 - Link affected repositories (Priority: P1)

As a workspace owner, I receive evidence-grounded links to organization repositories likely affected by an issue.

**Independent Test**: Ingest a fixture against a known repository catalog and verify links are catalog members with reasons and confidence.

**Acceptance Scenarios**:

1. Given an issue and current ontology, when ingestion completes, then zero or more affected repositories include identity, rationale, and confidence.
2. Given uncertainty, when no repository meets the threshold, then no affected repository is recorded.
3. Given a model proposal outside the catalog, when validated, then it is rejected.

### User Story 3 - Recommend an experienced approver (Priority: P2)

As a workspace owner, I see the strongest available approval-history signal for each affected repository.

**Independent Test**: Use review-history fixtures and verify the selected non-bot user has the highest APPROVED count, with deterministic ties.

**Acceptance Scenarios**:

1. Given accessible history, when a repository is linked, then the non-bot user with most submitted APPROVED reviews is selected with evidence count.
2. Given a tie, then lexicographically first login wins.
3. Given no history or insufficient permission, then recommendation is unavailable without failing repository linkage.
4. Dismissed, commented, requested-changes, duplicate, and bot reviews do not count.

### Edge Cases

- Every issue action is a distinct input and preserves its action.
- Replaying one source delivery does not duplicate durable records.
- Renamed, archived, private, empty, and temporarily inaccessible repositories remain explicit.
- Malformed or extra repository references are rejected.
- Large catalogs and histories use bounded resumable collection rather than silent truncation.

## Requirements

- **FR-001**: The CLI MUST expose issue ingestion for local and one-shot Actions use.
- **FR-002**: It MUST preserve event/action, source identity, repository, issue, and actor.
- **FR-003**: It MUST support Anthropic, OpenAI, and Gemini through vendor:model or separate provider/model selection consistent with existing provider behavior.
- **FR-004**: Exactly one ingestion provider configuration MUST be active.
- **FR-005**: Credentials MUST come only from the selected vendor environment variable and never be persisted, logged, or submitted to Core.
- **FR-006**: Missing support, credentials, unknown vendors, and invalid models MUST fail actionably.
- **FR-007**: It MUST obtain or refresh organization ontology when no usable revision exists.
- **FR-008**: Routing output MUST be constrained to ontology repositories with reason and confidence.
- **FR-009**: Empty affected-repository results MUST be valid.
- **FR-010**: Each linked repository MUST attempt to select the non-bot user with most submitted APPROVED reviews in available history.
- **FR-011**: Ties MUST be deterministic and unavailable history distinct from zero approvals.
- **FR-012**: Ingestion MUST be idempotent by stable source-event identity.
- **FR-013**: Output MUST distinguish accepted, ignored, retryable failure, permanent failure, and partial enrichment.
- **FR-014**: This release MUST NOT trigger builds, assign users, mutate issues, or update Project fields.
- **FR-015**: Bare CLI install and help MUST work without model SDKs.
- **FR-016**: Network and provider boundaries MUST support deterministic fixture testing.

## Key Entities

- **Ingestion Configuration**: Provider, model, organization, refresh policy, and non-secret service locations.
- **Issue Event**: Stable identity and GitHub issue context.
- **Repository Link**: Catalog-backed repository, rationale, confidence, and optional approver evidence.
- **Ingestion Result**: Provider/model provenance, ontology revision, links, status, and safe diagnostics.

## Success Criteria

- **SC-001**: Supported-provider fixtures select correctly and expose zero credentials.
- **SC-002**: Routing fixtures never return repositories outside the ontology.
- **SC-003**: Reviewer fixtures select correctly in 100% of tie, bot, and unavailable cases.
- **SC-004**: Ten replays yield exactly one durable ingestion identity.
- **SC-005**: An owner can configure ingestion from Actions without changing container code.
- **SC-006**: All missing-key, SDK, and permission cases provide actionable errors.
- **SC-007**: Existing CLI checks remain green.

## Assumptions

- Provider/model may be repository configuration; keys are environment or Actions secrets only.
- Top approver uses available all-history APPROVED reviews and is a routing signal, not ownership proof.
- Embeddings are deferred until structured and lexical retrieval misses a measured benchmark.
- Caller supplies GitHub delivery ID or deterministic event fingerprint.
- Core stores ontology/results; CLI gathers GitHub evidence.

## Dependencies

- Core ontology and ingestion contracts.
- GitHub credentials for accessible catalog and review history.
- Existing optional provider abstraction.
