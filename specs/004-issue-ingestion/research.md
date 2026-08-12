# Research: Issue Ingestion

## Provider selection

- **Decision**: Reuse `spareparts.providers.resolve(provider, model)` and its existing vendor key variables.
- **Rationale**: It already implements vendor:model precedence, lazy optional SDKs, and actionable credential errors.
- **Alternatives considered**: A second ingestion-specific provider registry would drift and risk persisting configuration.

## GitHub evidence collection

- **Decision**: Use REST API calls through an injected stdlib client, capped at 100 repositories, 100 pull requests per affected repository, and 100 reviews per pull request.
- **Rationale**: The evidence is auditable and deterministic, while explicit caps avoid runaway Actions jobs and silent pagination assumptions.
- **Alternatives considered**: GraphQL reduces calls but introduces a larger query contract; unbounded pagination can exhaust rate limits.

## Ontology retrieval and refresh

- **Decision**: Ask Core for current bounded context; create a complete revision from the GitHub repository catalog only when Core returns no usable revision or `--refresh-ontology` is set.
- **Rationale**: Subsequent ingestion reuses durable context and interrupted refresh cannot replace a complete revision.
- **Alternatives considered**: Rebuild every run wastes calls; local cache is unsuitable in ephemeral containers.

## Repository routing

- **Decision**: One structured model call returns catalog repository IDs, rationales, and confidence; reject unknown IDs and invalid confidence instead of guessing.
- **Rationale**: The ontology remains the authority and empty output is valid.
- **Alternatives considered**: Embeddings are deferred until lexical/structured retrieval has a measured miss benchmark.

## Idempotency and failure output

- **Decision**: Prefer `--delivery-id`; otherwise hash canonical event name, action, repository, issue, and payload. Emit a small safe JSON summary and stable exit codes.
- **Rationale**: GitHub retries converge without treating distinct issue actions as the same event.
- **Alternatives considered**: Run IDs are unstable across retries; logging full payloads may expose issue content.
