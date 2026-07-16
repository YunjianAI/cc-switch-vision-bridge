from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from .config import default_app_dir, load_config
from .credentials import delete_api_key, set_api_key
from .proxy import run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccsvb")
    parser.add_argument(
        "--config", default=os.environ.get("CCSVB_CONFIG", str(default_app_dir() / "config.toml"))
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Run the vision bridge")
    run_parser.add_argument("--verbose", action="store_true")
    key_parser = sub.add_parser("set-key", help="Store API key in Windows Credential Manager")
    key_parser.add_argument("--stdin", action="store_true", required=True)
    sub.add_parser("delete-key", help="Delete the stored API key")
    sub.add_parser("status", help="Read local bridge health")
    return parser


async def _status(config_path: str) -> int:
    config = load_config(config_path)
    url = f"http://{config.proxy.listen_host}:{config.proxy.listen_port}/health"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(url)
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            return 0
    except httpx.HTTPError as exc:
        print(f"Bridge is not healthy: {type(exc).__name__}")
        return 1


def main() -> int:
    args = _parser().parse_args()
    if args.command == "run":
        run(Path(args.config), verbose=args.verbose)
        return 0
    if args.command == "set-key":
        value = os.sys.stdin.readline().rstrip("\r\n")
        set_api_key(value)
        return 0
    if args.command == "delete-key":
        delete_api_key()
        return 0
    if args.command == "status":
        return asyncio.run(_status(args.config))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
