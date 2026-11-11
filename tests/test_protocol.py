import datetime

import pytest

from af_aroma_ble import protocol as P

# Packets captured from a real AP105 (HAOYUNMA XS-105) during development.
HOME = bytes.fromhex("02000001080014000005038401010101010101")
GROUP1 = bytes.fromhex("040001080014000005038401010101010101")
GROUP2 = bytes.fromhex("040102000000000005005a01010101010101")


def test_hello_expected_byte():
    packet, expected = P.hello(bytes([0x10, 0x21, 0x32, 0x43, 0x54, 0x65]))
    assert packet == bytes([0x07, 0x10, 0x21, 0x32, 0x43, 0x54, 0x65])
    assert expected == 0x32 ^ (0x21 | 0x54)


def test_hello_ack_answer_byte():
    reply = bytes(range(12))
    ack = P.hello_ack(reply, nonce=bytes(6))
    assert ack[0] == 0x08 and len(ack) == 7
    assert ack[4] == reply[10] ^ (reply[8] | reply[11])


def test_hello_ack_rejects_short_reply():
    with pytest.raises(ValueError):
        P.hello_ack(bytes(5))


def test_decode_home():
    d = P.decode(HOME)
    assert d == {"type": "home", "error": 0, "active_group": 1, "cycle": "pausing", "start": "08:00",
                 "end": "20:00", "spray_s": 5, "pause_s": 900}


def test_group_roundtrip():
    g = P.decode_group(GROUP1)
    assert (g.index, g.state, g.start, g.end, g.spray_s, g.pause_s) == (0, 1, "08:00", "20:00", 5, 900)
    assert g.encode() == GROUP1
    assert P.decode_group(GROUP2).state == P.GROUP_DISABLED


def test_group_days_are_monday_first():
    g = P.Group(index=2, start="07:30", end="09:00", spray_s=10, pause_s=120,
                days=[d in ("mon", "fri") for d in P.DAYS])
    assert g.encode() == bytes.fromhex("04 02 01 07 1e 09 00 000a 0078 01 00 00 00 01 00 00")
    assert g.to_dict()["days"] == ["mon", "fri"]


def test_options_roundtrip():
    o = P.decode_options(bytes([0x12, 10, 1, 1, 0]))
    assert (o.oil, o.fan, o.power, o.key_lock) == (10, 1, True, False)
    o.power = False
    assert o.encode() == bytes([0x12, 10, 1, 0, 0])


def test_time_sync_weekday_codes():
    # Wednesday 11 Nov 2026 must use code 1 (the firmware swaps Tuesday and Wednesday).
    wed = datetime.datetime(2026, 11, 11, 12, 4, 5)
    assert P.time_sync(wed) == bytes([0x01, 20, 26, 11, 11, 12, 4, 5, 1])
    tue = datetime.datetime(2026, 11, 10, 0, 0, 0)
    assert P.time_sync(tue)[-1] == 2
    sun = datetime.datetime(2026, 11, 15, 0, 0, 0)
    assert P.time_sync(sun)[-1] == 6


def test_password_reply_is_not_exposed():
    assert P.decode(b"\x10" + b"1234") == {"type": "password", "set": True}
