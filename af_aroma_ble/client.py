"""Async Bluetooth client for AF Aroma diffusers, built on bleak."""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from . import protocol as P

_LOG = logging.getLogger(__name__)


def looks_like_af_aroma(device: BLEDevice, adv: AdvertisementData) -> bool:
    return P.MANUFACTURER_ID in adv.manufacturer_data or (adv.local_name or device.name or "").upper().startswith("AP1")


async def discover(timeout: float = 10.0) -> List[tuple]:
    """Return [(address, name, rssi)] for nearby devices that look like AF Aroma diffusers."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = [(d.address, adv.local_name or d.name, adv.rssi) for d, adv in found.values() if looks_like_af_aroma(d, adv)]
    return sorted(out, key=lambda x: -x[2])


class AFAromaError(Exception):
    pass


class AFAroma:
    """Connect, do the handshake, then send commands. Use as an async context manager.

    The diffuser accepts a single Bluetooth connection, so close the phone app first.
    sync_time=True sets the clock on connect, like the official app, but note that a clock
    write restarts the active cycle, so it triggers a spray.
    """

    def __init__(self, address: str, *, sync_time: bool = False, connect_timeout: float = 20.0):
        self.address = address
        self.sync_time_on_connect = sync_time
        self.connect_timeout = connect_timeout
        self._client: Optional[BleakClient] = None
        self._q: asyncio.Queue = asyncio.Queue()

    # connection ----------------------------------------------------------

    def _on_notify(self, _char, data: bytearray) -> None:
        _LOG.debug("rx %s", bytes(data).hex())
        self._q.put_nowait(bytes(data))

    async def connect(self) -> None:
        dev = await BleakScanner.find_device_by_address(self.address, timeout=self.connect_timeout)
        if dev is None:
            raise AFAromaError(f"{self.address} not found: out of range, or another phone/app is connected to it")
        self._client = BleakClient(dev, timeout=self.connect_timeout)
        await self._client.connect()
        await self._client.start_notify(P.NOTIFY_UUID, self._on_notify)
        packet, expected = P.hello()
        await self._write(packet)
        reply = await self._wait_for(P.OP_HELLO)
        if len(reply) < 12 or reply[4] != expected:
            raise AFAromaError(f"handshake rejected: {reply.hex()}")
        await self._write(P.hello_ack(reply))
        await self._drain(0.5)
        if self.sync_time_on_connect:
            await self.sync_time()

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # BlueZ sometimes errors on a link that is already gone
                pass
            self._client = None

    async def __aenter__(self) -> "AFAroma":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    # low level -----------------------------------------------------------

    async def _write(self, data: bytes) -> None:
        if self._client is None:
            raise AFAromaError("not connected")
        _LOG.debug("tx %s", data.hex())
        await self._client.write_gatt_char(P.WRITE_UUID, data, response=True)

    async def _wait_for(self, opcode: int, timeout: float = 4.0) -> bytes:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                raise AFAromaError(f"no reply to opcode {opcode:#04x}")
            try:
                p = await asyncio.wait_for(self._q.get(), left)
            except asyncio.TimeoutError:
                continue
            if p and p[0] == opcode:
                return p

    async def _drain(self, seconds: float) -> List[bytes]:
        out = []
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while True:
            left = end - loop.time()
            if left <= 0:
                return out
            try:
                out.append(await asyncio.wait_for(self._q.get(), left))
            except asyncio.TimeoutError:
                return out

    async def send_raw(self, data: bytes, listen: float = 2.0) -> List[bytes]:
        await self._drain(0)
        await self._write(data)
        return await self._drain(listen)

    # reads ---------------------------------------------------------------

    async def home(self) -> dict:
        await self._drain(0)
        await self._write(P.query(P.OP_HOME))
        return P.decode(await self._wait_for(P.OP_HOME))

    async def config(self) -> dict:
        await self._write(P.query(P.OP_CONFIG))
        return P.decode(await self._wait_for(P.OP_CONFIG))

    async def options(self) -> P.Options:
        await self._write(P.query(P.OP_OPTIONS))
        return P.decode_options(await self._wait_for(P.OP_OPTIONS))

    async def cycle_range(self) -> dict:
        await self._write(P.query(P.OP_CYCLE_RANGE))
        return P.decode(await self._wait_for(P.OP_CYCLE_RANGE))

    async def groups(self, max_groups: int = 5) -> List[P.Group]:
        await self._drain(0)
        await self._write(P.query_groups())
        out = {}
        try:
            while len(out) < max_groups:
                g = P.decode_group(await self._wait_for(P.OP_GROUP, timeout=1.5))
                out[g.index] = g
        except AFAromaError:
            pass  # the device sends as many groups as it has
        return [out[k] for k in sorted(out)]

    async def status(self) -> dict:
        cfg = await self.config()
        opts = await self.options()
        home = await self.home()
        groups = await self.groups(cfg["max_groups"] or 5)
        return {"power": opts.power, "key_lock": opts.key_lock, "oil": opts.oil, "oil_max": cfg["oil_max"],
                "active_group": home["active_group"], "cycle": home["cycle"],
                "groups": [g.to_dict() for g in groups]}

    # writes --------------------------------------------------------------

    async def sync_time(self, now=None) -> None:
        """Set the clock. Restarts the active cycle, so it sprays if a group is active."""
        await self._write(P.time_sync(now))
        await asyncio.sleep(0.2)

    async def set_options(self, **changes) -> P.Options:
        cur = await self.options()
        for k, v in changes.items():
            setattr(cur, k, v)
        await self._write(cur.encode())
        await asyncio.sleep(0.4)
        return await self.options()

    async def set_power(self, on: bool) -> P.Options:
        return await self.set_options(power=on)

    async def set_key_lock(self, on: bool) -> P.Options:
        return await self.set_options(key_lock=on)

    async def write_group(self, group: P.Group) -> None:
        """Write a schedule slot. Writing a slot that covers the current time restarts its cycle with a spray."""
        await self._write(group.encode())
        await asyncio.sleep(0.3)

    async def spray_now(self) -> dict:
        """Spray once by rewriting the active schedule group unchanged.

        Works only while a group is active (inside its time window, on one of its days, power on).
        """
        home = await self.home()
        if home["active_group"] is None:
            raise AFAromaError("no schedule group is active right now, so there is no cycle to restart")
        idx = home["active_group"] - 1
        for g in await self.groups():
            if g.index == idx:
                await self._drain(0)
                await self._write(g.encode())
                # The device pushes a 0x02 status when the cycle restarts; wait for "spraying".
                loop = asyncio.get_running_loop()
                end = loop.time() + 4.0
                last = None
                while loop.time() < end:
                    try:
                        last = P.decode(await self._wait_for(P.OP_HOME, timeout=end - loop.time()))
                    except AFAromaError:
                        break
                    if last.get("cycle") == "spraying":
                        return last
                raise AFAromaError(f"group rewritten but the device did not report a spray (last status: {last})")
        raise AFAromaError("could not read the active group")

    async def rename(self, name: str) -> None:
        await self._write(P.rename(name))

    async def reset_oil_level(self) -> None:
        await self._write(P.oil_level_reset())


async def with_retries(address: str, fn, *, attempts: int = 3, delay: float = 5.0, sync_time: bool = False):
    """Run fn(device) inside a fresh connection, retrying on Bluetooth hiccups."""
    last = None
    for i in range(attempts):
        try:
            async with AFAroma(address, sync_time=sync_time) as d:
                return await fn(d)
        except Exception as e:  # bleak raises a mix of exception types
            last = e
            _LOG.warning("attempt %d/%d failed: %s", i + 1, attempts, e)
            if i + 1 < attempts:
                await asyncio.sleep(delay)
    raise AFAromaError(f"giving up after {attempts} attempts: {last}")
