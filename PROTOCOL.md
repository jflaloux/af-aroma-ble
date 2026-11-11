# AF Aroma BLE protocol

These notes describe the Bluetooth LE protocol spoken by waterless scent diffusers that are controlled with the **AF Aroma** app (Zhuhai Rongyuan Electronic Technology, Android package `com.rongcheng.afdiffuser`). They were worked out for interoperability, by studying how the app talks to the device and then testing every command on a real unit. The reference device is a wall-mounted 140 ml diffuser sold on AliExpress as HAOYUNMA XS-105, which advertises itself as `AP105`.

## Discovery

The device advertises its name (for example `AP105`), manufacturer data with company id `0x5952` ("RY") and, oddly, the 16-bit Heart Rate service UUID `0x180D`. The control service is not advertised.

## GATT layout

| Item | UUID | Properties |
|---|---|---|
| Service | `0000fff0-0000-1000-8000-00805f9b34fb` | |
| Write (commands) | `0000fff1-0000-1000-8000-00805f9b34fb` | write with response |
| Notify (replies) | `0000fff2-0000-1000-8000-00805f9b34fb` | notify |

The default MTU of 23 is enough: the largest packet is 19 bytes. The device accepts one connection at a time, so the phone app must be closed while another client is connected.

## Framing

Every packet in both directions is a single opcode byte followed by its payload. There is no header, length byte or checksum. Multi-byte numbers are big-endian. Replies arrive as notifications on `FFF2` and start with the same opcode as the request.

## Handshake

Right after enabling notifications on `FFF2`, the client must authenticate itself with a small challenge-response. Without it the device ignores commands.

1. Client sends `07 r0 r1 r2 r3 r4 r5`, six random bytes. It keeps `expected = r2 ^ (r1 | r4)`.
2. Device replies with a packet of at least 12 bytes starting with `07`. Byte 4 of the reply must equal `expected`.
3. Client answers `08 s0 s1 s2 s3 s4 s5` with six random bytes, except `s3`, which must be `reply[10] ^ (reply[8] | reply[11])`.

The official app then reads the app password (opcode `10`, see below) and syncs the clock, which makes the unit spray (see Weekday code).

## Commands

| Opcode | Request | Reply | Meaning |
|---|---|---|---|
| `01` | `01 CC YY MM DD hh mm ss W` | none | Set the clock. `CC YY` is the year split in two (20, 26), `W` is the weekday code below |
| `02` | `02` | `02 err grp cyc sh sm eh em SS SS PP PP d0..d6` | Live status (also pushed unprompted when the cycle changes) |
| `04` | `04 FF` | one `04 ...` packet per schedule group | Read all schedule groups |
| `04` | `04 i st sh sm eh em SS SS PP PP d0..d6` | `02 ...` status | Write schedule group `i` (0-based) |
| `05` | `05` | | Factory reset |
| `06` | `06 <UTF-8 name>` | | Rename the device |
| `09` | `09` | `09 ....  step min min max max` | Allowed pause range, bytes 6..10 |
| `10` | `10` | `10 <ASCII password>` | Read the app password |
| `10` | `10 <ASCII password>` | | Set the app password |
| `11` | `11` | `11 oilMax fanMax hasPower maxGroups hasLock hasReset hasOilReset` | Capabilities |
| `12` | `12` | `12 oil fan power lock` | Read options |
| `12` | `12 oil fan power lock` | | Write options (all four at once) |
| `13` | `13 01` | | Reset the oil level counter (after a refill) |
| `14` | `14 01` | | "Test mode, spray 5 s" in the app. Accepted but ignored by the AP105 firmware |

Field notes:

- `err` is an error code, 0 when fine. `grp` is the active schedule group (0-based) or `FF` when none is active. `cyc` is the cycle state: 0 idle, 1 pausing, 2 spraying.
- `SS SS` is the spray time in seconds, `PP PP` the pause time in seconds. The AP105 reports a pause range of 5 to 900 s in steps of 5 (opcode `09`).
- `st` is the group state: 0 empty, 1 enabled, 2 disabled. `d0..d6` are day flags, Monday first, 1 = active.
- In the options block, `power` is 1 for on, `lock` is 1 when the buttons on the unit are locked (only on models whose capabilities report `hasLock`; the AP105 does not and ignores it), `fan` is the fan gear (0 = off) and `oil` is the remaining oil level the app tracks. Because the block is written as a whole, read it first and write it back with only the field you want to change.
- Groups have priority in order: when windows overlap, the lowest group number wins.

## Weekday code

The clock packet carries a weekday code that is **not** a plain Monday=0 numbering. The app sends:

| Day | Code |
|---|---|
| Monday | 0 |
| Tuesday | 2 |
| Wednesday | 1 |
| Thursday | 3 |
| Friday | 4 |
| Saturday | 5 |
| Sunday | 6 |

Writing the clock restarts the active group's cycle, so it causes a spray if a group is active. Avoid syncing on every connection.

Tuesday and Wednesday are swapped, and the firmware agrees with it: on a Wednesday synced with code 1, a group with only the Wednesday flag set is active and a group with only the Tuesday flag set is not.

## Spraying on demand

There is no working "spray now" command on the AP105. Writing a schedule group restarts that group's cycle, and a cycle starts with a spray. So rewriting the active group unchanged triggers one spray without changing the schedule. This only works while a group is active (inside its time window, on one of its days, with power on).

## Password

The app password (default `9999` on the unit we tested) is not enforced by the device. The device returns it to any client that asks with opcode `10`, and the app compares it locally. Do not rely on it for security.
