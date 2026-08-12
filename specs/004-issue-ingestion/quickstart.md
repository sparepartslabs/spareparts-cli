# Quickstart Validation

## Prerequisites

- Python 3.11+
- One provider extra and its runtime key
- GitHub token with organization repository and pull-request review read access
- Core URL and `SPAREPARTS_INGEST_KEY` (the key resolves the workspace)
- An `issues` webhook payload at `event.json`

## Run

```sh
export OPENAI_API_KEY=...
export GITHUB_TOKEN=...
export SPAREPARTS_INGEST_KEY=...
sp ingest issue event.json \
  --provider openai:gpt-5.5 \
  --core-url https://core.example \
  --delivery-id "$GITHUB_DELIVERY_ID"
```

Expect a one-line JSON result with `accepted` or `partial`. Re-run with
the same delivery ID and verify the Core ingestion ID is unchanged. Inspect stdout,
stderr, and Core records and verify none of the three tokens appears.

## Tests

```sh
python -m pytest tests/test_ingest.py
python -m pytest
python -m spareparts --help
```
