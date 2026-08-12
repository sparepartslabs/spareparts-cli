# CLI Contract

```text
sp ingest issue EVENT_FILE --core-url URL
  [--provider VENDOR[:MODEL]] [--model MODEL]
  [--delivery-id ID]
  [--refresh-ontology] [--max-repositories N]
```

The GitHub token is read only from `GITHUB_TOKEN`; `--delivery-id` defaults to
`GITHUB_DELIVERY_ID`. Core authentication is read from `SPAREPARTS_INGEST_KEY`.
The model key is read only by the selected existing provider. Credentials are
environment-only so they do not appear in process arguments.
Successful stdout is JSON containing `status`, `source_id`, `provider`,
`ontology_revision_id`, `affected_repository_count`, and Core's ingestion ID.
Configuration/input errors exit 2; retryable network/provider failures exit 3.

The command accepts only `issues` payloads. This release never mutates GitHub.
