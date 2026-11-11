"""Command line tool: af-aroma <command> [--address XX:XX:...]"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from . import protocol as P
from .client import AFAromaError, discover, with_retries


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=lambda o: o.__dict__))


def _days(text: str) -> list:
    if text in ("all", "every"):
        return [True] * 7
    wanted = {d.strip().lower()[:3] for d in text.split(",")}
    unknown = wanted - set(P.DAYS)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown day(s): {', '.join(sorted(unknown))}")
    return [d in wanted for d in P.DAYS]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="af-aroma", description="Control an AF Aroma diffuser over Bluetooth LE.")
    ap.add_argument("--address", "-a", default=os.environ.get("AF_AROMA_ADDRESS"),
                    help="Bluetooth address (or set AF_AROMA_ADDRESS); run 'af-aroma scan' to find it")
    ap.add_argument("--debug", action="store_true", help="log every packet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help="list nearby diffusers")
    sub.add_parser("status", help="power, key lock, oil level, cycle and schedule groups")
    sub.add_parser("on", help="power on")
    sub.add_parser("off", help="power off")
    sub.add_parser("spray", help="spray once now (needs an active schedule group)")
    sub.add_parser("lock", help="lock the buttons on the unit")
    sub.add_parser("unlock", help="unlock the buttons on the unit")
    sub.add_parser("sync-time", help="set the diffuser clock from this computer")
    g = sub.add_parser("set-group", help="write one schedule group")
    g.add_argument("group", type=int, help="group number as shown in the app, starting at 1")
    g.add_argument("--start", default="08:00")
    g.add_argument("--end", default="20:00")
    g.add_argument("--spray", type=int, default=5, help="spray seconds")
    g.add_argument("--pause", type=int, default=900, help="pause seconds (device range is usually 5-900, step 5)")
    g.add_argument("--days", type=_days, default=[True] * 7, help="'all' or e.g. mon,tue,fri")
    g.add_argument("--disabled", action="store_true", help="store the group switched off")
    d = sub.add_parser("clear-group", help="delete one schedule group")
    d.add_argument("group", type=int)
    r = sub.add_parser("raw", help="send raw hex packets and print the replies (for protocol work)")
    r.add_argument("hex", nargs="+")
    return ap


async def run(args) -> int:
    if args.cmd == "scan":
        rows = await discover()
        if not rows:
            print("no AF Aroma diffuser found (is the phone app connected to it?)")
        for addr, name, rssi in rows:
            print(f"{addr}  {name or '?':<12} {rssi} dBm")
        return 0
    if not args.address:
        print("missing --address (run 'af-aroma scan' first)", file=sys.stderr)
        return 2

    async def act(dev):
        c = args.cmd
        if c == "status":
            return await dev.status()
        if c in ("on", "off"):
            return (await dev.set_power(c == "on")).__dict__
        if c in ("lock", "unlock"):
            return (await dev.set_key_lock(c == "lock")).__dict__
        if c == "spray":
            return await dev.spray_now()
        if c == "sync-time":
            await dev.sync_time()
            return {"ok": True}
        if c == "set-group":
            grp = P.Group(index=args.group - 1, state=P.GROUP_DISABLED if args.disabled else P.GROUP_ENABLED,
                          start=args.start, end=args.end, spray_s=args.spray, pause_s=args.pause, days=args.days)
            await dev.write_group(grp)
            return [g.to_dict() for g in await dev.groups()]
        if c == "clear-group":
            await dev.write_group(P.Group(index=args.group - 1, state=P.GROUP_EMPTY, start="00:00", end="00:00",
                                          spray_s=5, pause_s=90))
            return [g.to_dict() for g in await dev.groups()]
        if c == "raw":
            out = []
            for h in args.hex:
                for rep in await dev.send_raw(bytes.fromhex(h)):
                    out.append({"sent": h, "reply": rep.hex(), "decoded": P.decode(rep)})
            return out
        raise AssertionError(c)

    _print(await with_retries(args.address, act))
    return 0


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        sys.exit(asyncio.run(run(args)))
    except AFAromaError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
