# Data Model: Issue Ingestion

## IssueEvent

Fields: `source_id`, `event`, `action`, organization login, repository full name and node ID, issue number/title/body/URL, actor login, and received payload. Event must be `issues`; required nested objects are validated. The payload is transient and is never submitted wholesale to Core.

## RepositoryEntity

Fields: stable GitHub node ID, full/name, description, URL, visibility/private, archived, disabled, fork, default branch, topics, language, observed timestamp, and GitHub provenance. Repository IDs are the routing allowlist.

## OntologyRevision

Fields: revision ID, organization, observed time, completeness, and repository entities. Only complete revisions are used. The Core response supplies the canonical revision ID.

## RepositoryLink

Fields: repository ID/full name, rationale, confidence in `[0,1]`, and optional ReviewerEvidence. Links with IDs outside the selected ontology are rejected. Zero links is valid.

## ReviewerEvidence

Available form: login, submitted APPROVED count, observed time. Unavailable form: reason and observed time. Bots, non-APPROVED states, and duplicate review IDs are excluded; ties sort by login.

## IngestionResult

Fields: stable source ID, source summary, provider/model label, ontology revision ID, status (`accepted`, `ignored`, `retryable`, `permanent_failure`, `partial`), links, and safe diagnostics. Repeated source IDs return Core's existing record.
