"""Submit a Message Batch from a JSON file, poll it to completion, write JSONL results.

Usage:
    python scripts/batches_submit_and_poll.py --input requests.json --output results.jsonl
        [--dry-run | --real] [--json-log] [--poll-interval 30] [--timeout 3600]

Input is a JSON list of {"custom_id", "prompt", "model"?, "max_tokens"?} objects.
Nothing is submitted unless --real is passed; --dry-run (the default) only
validates the input and reports what would be sent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ccdvf.api.batches import BatchRequest  # noqa: E402


def load_requests(path: Path) -> list[BatchRequest]:
    rows = json.loads(path.read_text())
    return [
        BatchRequest(
            custom_id=row["custom_id"],
            messages=[{"role": "user", "content": row["prompt"]}],
            model=row.get("model", "haiku_current"),
            max_tokens=row.get("max_tokens", 256),
        )
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate only (default)")
    mode.add_argument("--real", action="store_true", help="submit to the API (billable)")
    parser.add_argument("--json-log", action="store_true")
    parser.add_argument("--poll-interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)

    requests = load_requests(args.input)

    if not args.real:
        summary = {
            "dry_run": True,
            "requests": len(requests),
            "custom_ids": [r.custom_id for r in requests],
            "batch_id": "dry-run-batch-id",
        }
    else:
        from ccdvf.api.batches import BatchesClient

        client = BatchesClient()
        handle = client.submit(requests)
        handle = client.poll_until_complete(
            handle.id, timeout_seconds=args.timeout, poll_interval_seconds=args.poll_interval
        )
        with args.output.open("w") as out:
            for item in client.results(handle.id):
                row = {"custom_id": item["custom_id"], "type": item["result"].type}
                if item["result"].type == "succeeded":
                    row["text"] = "".join(
                        b.text for b in item["result"].message.content if b.type == "text"
                    )
                out.write(json.dumps(row) + "\n")
        summary = {
            "dry_run": False,
            "requests": len(requests),
            "batch_id": handle.id,
            "request_counts": handle.request_counts,
            "output": str(args.output),
        }

    print(json.dumps(summary) if args.json_log else summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
