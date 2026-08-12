# Core HTTP Contract Consumed by the CLI

Machine requests use `x-api-key: <SPAREPARTS_INGEST_KEY>`. Credentials and full
webhook payloads are never submitted.

## Current ontology context

`GET /ingestion/v1/ontology-context?organization_login={login}&q=&limit={n}&stale_after_hours=24`

No revision is represented by a null revision and empty repositories. A usable
response includes a current revision and bounded deterministic repository entries.

## Create ontology revision

`POST /ingestion/v1/ontology-revisions`

Body contains complete revision and organization provenance plus stable repository
IDs, canonical metadata, relationships, and observation times. Creation is atomic
and idempotent and returns the current immutable revision.

## Submit issue ingestion

`POST /ingestion/v1/issues`

Body contains stable source event identity, safe issue/actor metadata,
provider/model provenance, ontology revision ID, processing status, and affected
repositories. Each link contains the ontology repository ID, rationale, confidence,
and reviewer evidence or an unavailable reason. Replay returns the original event.
