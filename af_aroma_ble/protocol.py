"""Packet encoding and decoding for AF Aroma diffusers. Pure Python, no Bluetooth.

Every packet, in both directions, is a one-byte opcode followed by its payload.
There is no framing, length byte or checksum. See PROTOCOL.md for the details.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import List, Optional

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 0x5952  # "RY" (Rongyuan), seen in the advertising data

OP_TIME = 0x01
OP_HOME = 0x02
OP_GROUP = 0x04
OP_FACTORY_RESET = 0x05
OP_RENAME = 0x06
OP_HELLO = 0x07
OP_HELLO_ACK = 0x08
OP_CYCLE_RANGE = 0x09
OP_PASSWORD = 0x10
OP_CONFIG = 0x11
OP_OPTIONS = 0x12
OP_OIL_RESET = 0x13
OP_TEST_MODE = 0x14

GROUP_EMPTY = 0
GROUP_ENABLED = 1
GROUP_DISABLED = 2

CYCLE_IDLE = 0
CYCLE_PAUSING = 1
CYCLE_SPRAYING = 2

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# Weekday code sent in the time-sync packet, indexed by Python's date.weekday()
# (Monday=0). Tuesday and Wednesday are swapped on purpose: the official app does
# this and the firmware matches it (verified on an AP105, a schedule with only the
# Wednesday flag set runs on a Wednesday synced with code 1).
WEEKDAY_CODE = (0, 2, 1, 3, 4, 5, 6)


def _be16(b: bytes, i: int) -> int:
    return (b[i] << 8) | b[i + 1]


def _u16(v: int) -> bytes:
    if not 0 <= v <= 0xFFFF:
        raise ValueError(f"value out of range: {v}")
    return bytes([(v >> 8) & 0xFF, v & 0xFF])


# Handshake ---------------------------------------------------------------

def hello(challenge: Optional[bytes] = None) -> tuple[bytes, int]:
    """Return the 0x07 hello packet and the byte the device must echo back at reply[4]."""
    r = challenge if challenge is not None else os.urandom(6)
    if len(r) != 6:
        raise ValueError("challenge must be 6 bytes")
    expected = r[2] ^ (r[1] | r[4])
    return bytes([OP_HELLO]) + r, expected


def hello_ack(reply: bytes, nonce: Optional[bytes] = None) -> bytes:
    """Build the 0x08 packet that answers the device's 0x07 reply."""
    if len(reply) < 12:
        raise ValueError(f"hello reply too short: {reply.hex()}")
    s = bytearray(nonce if nonce is not None else os.urandom(6))
    s[3] = reply[10] ^ (reply[8] | reply[11])
    return bytes([OP_HELLO_ACK]) + bytes(s)


# Commands ----------------------------------------------------------------

def time_sync(now: Optional[_dt.datetime] = None) -> bytes:
    now = now or _dt.datetime.now()
    return bytes([OP_TIME, now.year // 100, now.year % 100, now.month, now.day,
                  now.hour, now.minute, now.second, WEEKDAY_CODE[now.weekday()]])


def query(opcode: int) -> bytes:
    return bytes([opcode])


def query_groups() -> bytes:
    return bytes([OP_GROUP, 0xFF])


@dataclass
class Options:
    """The 0x12 block. It is always written as a whole, so read it, change one field, write it back."""
    oil: int
    fan: int
    power: bool
    key_lock: bool

    def encode(self) -> bytes:
        return bytes([OP_OPTIONS, self.oil & 0xFF, self.fan & 0xFF, int(self.power), int(self.key_lock)])


@dataclass
class Group:
    """One schedule slot ("Group n" in the app). index is 0-based on the wire."""
    index: int
    state: int = GROUP_ENABLED
    start: str = "00:00"
    end: str = "23:59"
    spray_s: int = 5
    pause_s: int = 900
    days: List[bool] = field(default_factory=lambda: [True] * 7)  # Monday..Sunday

    def encode(self) -> bytes:
        sh, sm = (int(x) for x in self.start.split(":"))
        eh, em = (int(x) for x in self.end.split(":"))
        if len(self.days) != 7:
            raise ValueError("days must have 7 entries, Monday first")
        return (bytes([OP_GROUP, self.index, self.state, sh, sm, eh, em]) + _u16(self.spray_s)
                + _u16(self.pause_s) + bytes(int(bool(d)) for d in self.days))

    def to_dict(self) -> dict:
        return {"group": self.index + 1, "state": {0: "empty", 1: "enabled", 2: "disabled"}.get(self.state, self.state),
                "start": self.start, "end": self.end, "spray_s": self.spray_s, "pause_s": self.pause_s,
                "days": [d for d, on in zip(DAYS, self.days) if on]}


def rename(name: str) -> bytes:
    return bytes([OP_RENAME]) + name.encode("utf-8")


def oil_level_reset() -> bytes:
    return bytes([OP_OIL_RESET, 0x01])


# Replies -----------------------------------------------------------------

def decode(p: bytes) -> dict:
    """Decode a notification into a plain dict. Unknown packets come back as {'type': 'opXX', 'raw': hex}."""
    if not p:
        return {"type": "empty"}
    op = p[0]
    if op == OP_HOME and len(p) >= 12:
        return {"type": "home", "error": p[1], "active_group": None if p[2] == 0xFF else p[2] + 1,
                "cycle": {0: "idle", 1: "pausing", 2: "spraying"}.get(p[3], p[3]),
                "start": f"{p[4]:02d}:{p[5]:02d}", "end": f"{p[6]:02d}:{p[7]:02d}",
                "spray_s": _be16(p, 8), "pause_s": _be16(p, 10)}
    if op == OP_GROUP and len(p) >= 18:
        return {"type": "group", **decode_group(p).to_dict()}
    if op == OP_CYCLE_RANGE and len(p) >= 11:
        return {"type": "cycle_range", "pause_step_s": p[6], "pause_min_s": _be16(p, 7), "pause_max_s": _be16(p, 9)}
    if op == OP_CONFIG and len(p) >= 8:
        return {"type": "config", "oil_max": p[1], "fan_max": p[2], "has_power": bool(p[3]), "max_groups": p[4],
                "has_lock": bool(p[5]), "has_reset": bool(p[6]), "has_oil_reset": bool(p[7])}
    if op == OP_OPTIONS and len(p) >= 5:
        o = decode_options(p)
        return {"type": "options", "oil": o.oil, "fan": o.fan, "power": o.power, "key_lock": o.key_lock}
    if op == OP_PASSWORD:
        # The device hands its app password to anyone who asks; the app checks it locally.
        return {"type": "password", "set": len(p) > 1}
    return {"type": f"op{op:02x}", "raw": p.hex()}


def decode_options(p: bytes) -> Options:
    return Options(oil=p[1], fan=p[2], power=bool(p[3]), key_lock=p[4] == 1)


def decode_group(p: bytes) -> Group:
    return Group(index=p[1], state=p[2], start=f"{p[3]:02d}:{p[4]:02d}", end=f"{p[5]:02d}:{p[6]:02d}",
                 spray_s=_be16(p, 7), pause_s=_be16(p, 9), days=[b == 1 for b in p[11:18]])
