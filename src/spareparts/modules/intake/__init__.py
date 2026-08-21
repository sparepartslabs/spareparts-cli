"""Private one-job intake worker command."""
from __future__ import annotations
import argparse
import json
import os
import sys
from .clients import IntakeClient
from .models import IntakeError
from .service import execute

def register(parser:argparse.ArgumentParser)->None:
    commands=parser.add_subparsers(dest="command",required=True)
    worker=commands.add_parser("worker",help="claim and process one private intake job")
    worker.add_argument("--job-id",default=os.environ.get("INTAKE_JOB_ID"),required=os.environ.get("INTAKE_JOB_ID") is None)
    worker.add_argument("--core-url",default=os.environ.get("SP_CORE_URL"))

def run(args:argparse.Namespace)->int:
    token=os.environ.get("INTAKE_BOOTSTRAP_TOKEN","")
    if not token:
        print("sp intake: INTAKE_BOOTSTRAP_TOKEN is required",file=sys.stderr)
        return 3
    try:
        result=execute(IntakeClient(args.core_url or ""),args.job_id,token)
        print(json.dumps(result,sort_keys=True,separators=(",",":")))
        return 0
    except IntakeError as error:
        print(json.dumps({"status":"failed","category":error.category,"retryable":error.retryable},sort_keys=True,separators=(",",":")))
        print(f"sp intake: {error}",file=sys.stderr)
        return 2 if error.retryable else 3
