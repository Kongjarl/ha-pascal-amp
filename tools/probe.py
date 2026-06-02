#!/usr/bin/env python3
"""Standalone connectivity probe for a Pascal IP amplifier.

This does NOT require Home Assistant. Use it to confirm an amplifier is
reachable and to inspect the registers the integration will consume.

Usage (PowerShell or any shell):

    python tools/probe.py 192.168.64.100
    python tools/probe.py 192.168.64.100 --port 7621 --subscribe 5

It connects over the line-based TCP API (port 7621 by default), runs ``GET *``
to dump every register, and optionally streams live updates for a few seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

DEFAULT_PORT = 7621


async def _read_until_echo(
    reader: asyncio.StreamReader, command: str, timeout: float
) -> list[str]:
    """Read lines until the ``*command`` echo (or ``#error``) arrives."""
    lines: list[str] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(line)
        if line.startswith("*") or line.startswith("#"):
            break
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Pascal IP amplifier.")
    parser.add_argument("host", help="Amplifier IP address or hostname")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--subscribe",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Stream live updates for this many seconds after the dump.",
    )
    args = parser.parse_args()

    print(f"Connecting to {args.host}:{args.port} ...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(args.host, args.port), timeout=10
        )
    except (OSError, asyncio.TimeoutError) as err:
        print(f"  FAILED: {err}")
        return 1
    print("  connected.\n")

    try:
        writer.write(b"GET *\n")
        await writer.drain()
        lines = await _read_until_echo(reader, "GET *", timeout=20)
        registers = [ln[1:] for ln in lines if ln.startswith("+")]
        print(f"Received {len(registers)} registers:")
        for entry in sorted(registers):
            print(f"  {entry}")

        if args.subscribe > 0:
            print(f"\nSubscribing for {args.subscribe:.0f}s ...")
            writer.write(b"SUBSCRIBE\n")
            await writer.drain()
            deadline = asyncio.get_event_loop().time() + args.subscribe
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if not raw:
                    break
                print("  " + raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except (asyncio.TimeoutError, Exception):
            pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
