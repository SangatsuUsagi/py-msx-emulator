#!/usr/bin/env python3
"""Thin CLI client for the emulator's Unix-socket JSON-RPC server.

For manual testing without the MCP layer. Sends one method call and prints the
response. Params are given as a JSON object or as key=value pairs.

Examples:
    python tools/rpc_client.py debugger.status
    python tools/rpc_client.py debugger.pause
    python tools/rpc_client.py memory.read address=0xC000 length=16
    python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
    python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any

DEFAULT_SOCKET = "/tmp/py_msx_emu.sock"


def _readline(sock: socket.socket) -> bytes:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.strip()


def _coerce(value: str) -> Any:
    """Turn a key=value string value into an int/bool/str as appropriate."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(value, 0)
    except ValueError:
        return value


def _parse_params(tokens: list[str]) -> dict[str, Any] | None:
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0].lstrip().startswith("{"):
        parsed = json.loads(tokens[0])
        if not isinstance(parsed, dict):
            raise ValueError("JSON params must be an object")
        return parsed
    params: dict[str, Any] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"expected key=value, got: {token!r}")
        key, _, raw = token.partition("=")
        params[key] = _coerce(raw)
    return params


def main() -> None:
    parser = argparse.ArgumentParser(description="py-msx-emulator RPC test client")
    parser.add_argument("--socket", default=DEFAULT_SOCKET,
                        help=f"Unix socket path (default: {DEFAULT_SOCKET})")
    parser.add_argument("method", help="RPC method name, e.g. debugger.status")
    parser.add_argument("params", nargs="*",
                        help="Params as a JSON object or key=value pairs")
    args = parser.parse_args()

    try:
        params = _parse_params(args.params)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    payload: dict[str, Any] = {"id": "1", "method": args.method}
    if params:
        payload["params"] = params

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect(args.socket)
    except OSError as exc:
        print(f"error: cannot connect to {args.socket}: {exc}", file=sys.stderr)
        print("Is py-msx-emulator running with --rpc?", file=sys.stderr)
        sys.exit(1)

    try:
        _readline(sock)  # drain the connection banner
        sock.sendall((json.dumps(payload) + "\n").encode())
        while True:
            resp = json.loads(_readline(sock))
            # Print (and skip) push notifications until our response arrives.
            if "notification" in resp:
                print(json.dumps(resp), file=sys.stderr)
                continue
            break
    finally:
        sock.close()

    print(json.dumps(resp, indent=2))
    if "error" in resp:
        sys.exit(1)


if __name__ == "__main__":
    main()
