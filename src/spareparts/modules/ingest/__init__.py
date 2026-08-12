"""`sp ingest issue`: one-shot GitHub issue enrichment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from spareparts.providers import ProviderError, resolve

from .clients import CoreClient, GitHubClient
from .models import IngestionError, IssueEvent
from .service import ingest_issue


def register(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue", help="ingest one GitHub issues event file")
    issue.add_argument("event_file", type=Path)
    issue.add_argument("--provider", help="anthropic, openai, gemini, or vendor:model")
    issue.add_argument("--model", help="explicit model; overrides vendor:model")
    issue.add_argument("--core-url", default=os.environ.get("SP_CORE_URL"))
    issue.add_argument("--delivery-id", default=os.environ.get("GITHUB_DELIVERY_ID"))
    issue.add_argument("--refresh-ontology", action="store_true")
    issue.add_argument("--max-repositories", type=int, default=100, choices=range(1, 101), metavar="1..100")


def run(args: argparse.Namespace) -> int:
    try:
        try:
            payload = json.loads(args.event_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise IngestionError(f"cannot read event file: {err}") from err
        event = IssueEvent.from_payload(payload, args.delivery_id)
        provider = resolve(args.provider, args.model)
        github = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
        core = CoreClient(args.core_url or "", os.environ.get("SPAREPARTS_INGEST_KEY", ""))
        result = ingest_issue(event, provider, github, core, refresh=args.refresh_ontology, max_repositories=args.max_repositories)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ProviderError as err:
        print(f"sp ingest: {err}", file=sys.stderr)
        return 2
    except IngestionError as err:
        print(f"sp ingest: {err}", file=sys.stderr)
        return 3 if str(err).startswith("network request failed") else 2
