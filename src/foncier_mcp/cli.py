"""Command line: serve (stdio or HTTP), call one tool directly, manage API keys."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from . import __version__
from .config import Settings
from .errors import FoncierError
from .http import Fetcher
from .tiers import KeyStore, tiers_for
from .tools import TOOL_FUNCTIONS, run_tool


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _call(settings: Settings, tool: str, inputs: dict[str, Any]) -> dict[str, Any]:
    fetcher = Fetcher(settings)
    try:
        return await run_tool(fetcher, tool, inputs)
    finally:
        await fetcher.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foncier-mcp",
        description="MCP server for French public land data (cadastre, DVF, zoning, DPE, risks).",
    )
    parser.add_argument("--version", action="version", version=f"foncier-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default transport: stdio)")
    serve.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    serve.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    serve.add_argument("--port", type=int, default=8080, help="HTTP port")

    call = sub.add_parser("call", help="call one tool and print its JSON envelope")
    call.add_argument("tool", choices=sorted(TOOL_FUNCTIONS))
    call.add_argument(
        "--json",
        dest="payload",
        default="{}",
        help='tool inputs as a JSON object, for example {"lon": 3.8946, "lat": 43.5986}',
    )

    sub.add_parser("tools", help="list the tools and their descriptions")

    keys = sub.add_parser("keys", help="issue, list or revoke API keys for the HTTP transport")
    keys_sub = keys.add_subparsers(dest="keys_command", required=True)
    issue = keys_sub.add_parser("issue", help="issue a key; the plain key is printed once")
    issue.add_argument("--email", required=True)
    issue.add_argument("--tier", default="free")
    keys_sub.add_parser("list", help="list issued keys (ids only, never the keys)")
    revoke = keys_sub.add_parser("revoke", help="revoke a key by its id")
    revoke.add_argument("key_id")
    keys_sub.add_parser(
        "export", help="print the store as compact JSON (hashes only) for FONCIER_MCP_KEYS_JSON"
    )
    return parser


def _cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    from .server import FoncierServer

    server = FoncierServer(settings)
    if args.transport == "http":
        server.run_http(args.host, args.port)
    else:
        server.run_stdio()
    return 0


def _cmd_tools() -> int:
    from .server import DESCRIPTIONS

    for name in TOOL_FUNCTIONS:
        print(f"{name}\n    {DESCRIPTIONS.get(name, '')}\n")
    return 0


def _cmd_call(settings: Settings, args: argparse.Namespace) -> int:
    try:
        inputs = json.loads(args.payload)
    except ValueError as exc:
        print(f"--json is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(inputs, dict):
        print("--json must be a JSON object", file=sys.stderr)
        return 2
    result = asyncio.run(_call(settings, args.tool, inputs))
    _print_json(result)
    return 0 if result.get("ok") else 1


def _cmd_keys(settings: Settings, args: argparse.Namespace) -> int:
    store = KeyStore(settings.keys_file, inline=settings.keys_json)
    tiers = tiers_for(settings)
    if args.keys_command == "issue":
        try:
            plain, record = store.issue(args.email, args.tier, tiers)
        except FoncierError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 1
        print(plain)
        print(
            f"key {record.id} issued for {record.email}, tier {record.tier}, "
            f"{tiers[record.tier].daily_quota} calls per day. Shown once, store it now.",
            file=sys.stderr,
        )
        return 0
    if args.keys_command == "list":
        rows = store.list()
        if not rows:
            print("no key issued", file=sys.stderr)
            return 0
        for key in rows:
            state = "active" if key.active else "revoked"
            print(f"{key.id}\t{key.tier}\t{state}\t{key.created_at}\t{key.email}")
        return 0
    if args.keys_command == "revoke":
        try:
            revoked = store.revoke(args.key_id)
        except FoncierError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 1
        if revoked:
            print(f"key {args.key_id} revoked", file=sys.stderr)
            return 0
        print(f"no active key with id {args.key_id}", file=sys.stderr)
        return 1
    if args.keys_command == "export":
        print(store.export())
        print(f"{len(store.list())} key records exported, hashes only", file=sys.stderr)
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _utf8_stdout()
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    command = args.command or "serve"
    if command == "serve":
        if args.command is None:
            args.transport, args.host, args.port = "stdio", "127.0.0.1", 8080
        return _cmd_serve(settings, args)
    if command == "tools":
        return _cmd_tools()
    if command == "call":
        return _cmd_call(settings, args)
    if command == "keys":
        return _cmd_keys(settings, args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
