"""Smoke-test claude_code.api.streaming.StreamingClient from the command line.

Usage:
    python scripts/streaming_client_smoke_test.py --prompt "Hi" [--model haiku_current]
        [--max-tokens 256] [--dry-run] [--json-log]

--dry-run makes no API call (no key needed). --json-log prints one JSON summary
object to stdout; without it, the streamed text is printed instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_code.config.model_registry import cost_for_call, get_model  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="haiku_current")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true", help="skip the API call")
    parser.add_argument("--json-log", action="store_true", help="emit a JSON summary")
    args = parser.parse_args(argv)

    spec = get_model(args.model)

    if args.dry_run:
        summary = {
            "dry_run": True,
            "model": spec.id,
            "prompt": args.prompt,
            "chunks": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": cost_for_call(args.model, input_tokens=0, output_tokens=0),
        }
    else:
        from claude_code.api.streaming import StreamingClient

        chunks: list[str] = []
        with StreamingClient(model=args.model) as client:
            for chunk in client.stream(args.prompt, max_tokens=args.max_tokens):
                chunks.append(chunk)
                if not args.json_log:
                    print(chunk, end="", flush=True)
            in_tok, out_tok = client.last_call_tokens
            cost = client.last_call_usd
        summary = {
            "dry_run": False,
            "model": spec.id,
            "prompt": args.prompt,
            "chunks": len(chunks),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost,
        }

    if args.json_log:
        print(json.dumps(summary))
    elif args.dry_run:
        print(f"DRY RUN: would stream {args.prompt!r} to {spec.id}")
    else:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
