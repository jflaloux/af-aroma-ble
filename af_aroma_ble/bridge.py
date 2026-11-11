"""Tiny HTTP bridge so Home Assistant (or anything else) can drive a diffuser through a Linux box in Bluetooth range.

    af-aroma-bridge --address XX:XX:XX:XX:XX:XX --port 8765 [--token SECRET]

GET  /status            cached for --cache seconds
POST /power             {"on": true|false}
POST /spray
POST /key-lock          {"on": true|false}
POST /sync-time
POST /groups/<n>        {"start": "08:00", "end": "20:00", "spray_s": 5, "pause_s": 900,
                         "days": ["mon", ...], "enabled": true}   (missing fields keep their current value)

If --token is set, requests must carry "Authorization: Bearer <token>".
Requests are serialised: the diffuser only takes one connection at a time.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import protocol as P
from .client import AFAromaError, with_retries

_LOG = logging.getLogger("af_aroma_ble.bridge")


class Bridge:
    def __init__(self, address: str, cache_s: float):
        self.address = address
        self.cache_s = cache_s
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._lock = asyncio.Lock()
        self._status = None
        self._status_at = 0.0

    def run(self, fn, invalidate=True):
        async def guarded():
            async with self._lock:
                return await with_retries(self.address, fn)
        result = asyncio.run_coroutine_threadsafe(guarded(), self._loop).result(timeout=120)
        if invalidate:
            self._status_at = 0.0
        return result

    def status(self):
        if self._status is not None and time.monotonic() - self._status_at < self.cache_s:
            return self._status
        self._status = self.run(lambda d: d.status(), invalidate=False)
        self._status_at = time.monotonic()
        return self._status


def make_handler(bridge: Bridge, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "af-aroma-bridge"

        def log_message(self, fmt, *args):
            _LOG.info("%s %s", self.address_string(), fmt % args)

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            if token and self.headers.get("Authorization", "") != f"Bearer {token}":
                self._send(401, {"error": "unauthorised"})
                return False
            return True

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if not self._authorised():
                return
            if self.path.rstrip("/") == "/status":
                return self._guard(bridge.status)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorised():
                return
            path = self.path.rstrip("/")
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "body must be JSON"})
            if path == "/power":
                return self._guard(lambda: bridge.run(lambda d: d.set_power(bool(body.get("on", True)))).__dict__)
            if path == "/key-lock":
                return self._guard(lambda: bridge.run(lambda d: d.set_key_lock(bool(body.get("on", True)))).__dict__)
            if path == "/spray":
                return self._guard(lambda: bridge.run(lambda d: d.spray_now()))
            if path == "/sync-time":
                return self._guard(lambda: bridge.run(lambda d: d.sync_time()) or {"ok": True})
            if path.startswith("/groups/"):
                try:
                    n = int(path.split("/")[2])
                except ValueError:
                    return self._send(400, {"error": "group must be a number"})
                return self._guard(lambda: bridge.run(lambda d: _update_group(d, n, body)))
            self._send(404, {"error": "not found"})

        def _guard(self, fn):
            try:
                self._send(200, fn())
            except AFAromaError as e:
                self._send(503, {"error": str(e)})
            except Exception as e:  # keep the server alive whatever happens
                _LOG.exception("request failed")
                self._send(500, {"error": str(e)})

    return Handler


async def _update_group(dev, n: int, body: dict):
    current = {g.index: g for g in await dev.groups()}
    g = current.get(n - 1) or P.Group(index=n - 1)
    if "start" in body:
        g.start = body["start"]
    if "end" in body:
        g.end = body["end"]
    if "spray_s" in body:
        g.spray_s = int(body["spray_s"])
    if "pause_s" in body:
        g.pause_s = int(body["pause_s"])
    if "days" in body:
        wanted = {d.lower()[:3] for d in body["days"]}
        g.days = [d in wanted for d in P.DAYS]
    if "enabled" in body:
        g.state = P.GROUP_ENABLED if body["enabled"] else P.GROUP_DISABLED
    elif g.state == P.GROUP_EMPTY:
        g.state = P.GROUP_ENABLED
    await dev.write_group(g)
    return [x.to_dict() for x in await dev.groups()]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="af-aroma-bridge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", "-a", default=os.environ.get("AF_AROMA_ADDRESS"), required=not os.environ.get("AF_AROMA_ADDRESS"))
    ap.add_argument("--host", default=os.environ.get("AF_AROMA_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("AF_AROMA_PORT", "8765")))
    ap.add_argument("--token", default=os.environ.get("AF_AROMA_TOKEN"))
    ap.add_argument("--cache", type=float, default=float(os.environ.get("AF_AROMA_CACHE", "30")), help="seconds to cache /status")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.token:
        _LOG.warning("no --token set: anyone who can reach this port can control the diffuser")
    bridge = Bridge(args.address, args.cache)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge, args.token))
    _LOG.info("listening on %s:%d for %s", args.host, args.port, args.address)
    server.serve_forever()


if __name__ == "__main__":
    main()
