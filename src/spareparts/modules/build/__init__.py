"""Public `sp build issue` command."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from .clients import Commands, CoreClient, GitHub
from .models import BuildError, Policy, repository_name
from .service import run_build


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="build one durably ingested issue")
    issue.add_argument("--source-repository", required=True)
    issue.add_argument("--issue-number", required=True, type=int)
    issue.add_argument("--trigger-delivery-id", required=True)
    issue.add_argument("--core-url", default=os.environ.get("SP_CORE_URL"))
    issue.add_argument("--agent", required=True, choices=("codex", "claude"))
    issue.add_argument("--model")
    issue.add_argument("--allowed-org", action="append", default=[])
    issue.add_argument("--allowed-repository", action="append", default=[])
    issue.add_argument("--max-fanout", type=int, choices=range(1, 11), default=3, metavar="1..10")
    issue.add_argument("--validation-command", action="append", default=[])
    issue.add_argument("--workspace", type=Path, default=Path(".sp/builds"))
    issue.add_argument("--dry-run", action="store_true")


def run(args: argparse.Namespace) -> int:
    try:
        source = repository_name(args.source_repository, "--source-repository")
        if args.issue_number < 1: raise BuildError("--issue-number must be positive")
        if not args.trigger_delivery_id.strip(): raise BuildError("--trigger-delivery-id must not be empty")
        validations = tuple(tuple(shlex.split(value)) for value in args.validation_command)
        if any(not value for value in validations): raise BuildError("--validation-command must not be empty")
        policy = Policy(frozenset(value.lower() for value in args.allowed_org), frozenset(repository_name(value, "--allowed-repository").lower() for value in args.allowed_repository), args.max_fanout, validations)
        commands = Commands()
        result = run_build(source_repository=source, issue_number=args.issue_number, trigger_id=args.trigger_delivery_id, agent=args.agent, model=args.model, workspace=args.workspace.resolve(), policy=policy, core=CoreClient(args.core_url or "", os.environ.get("SPAREPARTS_API_KEY", "") or os.environ.get("SPAREPARTS_INGEST_KEY", "")), github=GitHub(commands, os.environ.get("GITHUB_TOKEN", "")), commands=commands, dry_run=args.dry_run)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "success" else 1
    except (BuildError, ValueError) as error:
        print(f"sp build: {error}", file=sys.stderr)
        return 3 if isinstance(error, BuildError) and error.category == "retryable_failure" else 2
