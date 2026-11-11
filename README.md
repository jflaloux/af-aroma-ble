# af-aroma-ble

Local Bluetooth LE control for waterless scent diffusers that use the **AF Aroma** app (Zhuhai Rongyuan Electronic Technology). No cloud, no account, no phone: a Python library, a command line tool and a tiny HTTP bridge so Home Assistant or any other automation system can switch the diffuser on and off, spray on demand and manage its schedules.

The protocol is documented in [PROTOCOL.md](PROTOCOL.md).

## Supported devices

Tested on a wall-mounted 140 ml diffuser sold on AliExpress as **HAOYUNMA XS-105**, which shows up over Bluetooth as `AP105`. Other diffusers controlled by the AF Aroma app very likely speak the same protocol. If yours works (or doesn't), please open an issue with the Bluetooth name and the output of `af-aroma status`.

## Install

On a Linux machine with Bluetooth within range of the diffuser (a Raspberry Pi works well):

```
pip install git+https://github.com/jflaloux/af-aroma-ble
```

It needs Python 3.9+ and [bleak](https://github.com/hbldh/bleak). Close the AF Aroma app before using it: the diffuser accepts only one connection at a time.

## Command line

```
af-aroma scan                                   # find the diffuser and its address
export AF_AROMA_ADDRESS=XX:XX:XX:XX:XX:XX
af-aroma status                                 # power, key lock, oil, cycle, schedule groups
af-aroma on | off
af-aroma spray                                  # one spray now (needs an active schedule group)
af-aroma lock | unlock                          # lock the buttons on the unit
af-aroma sync-time
af-aroma set-group 1 --start 08:00 --end 20:00 --spray 5 --pause 900 --days all
af-aroma set-group 2 --start 19:00 --end 22:00 --pause 300 --days mon,tue,wed,thu,fri
af-aroma clear-group 2
af-aroma raw 02 1101                            # send raw packets, print the replies
```

Commands retry a few times, because a weak link or a reconnect right after a disconnect can fail once. Unlike the official app, connecting does not set the diffuser clock: a clock write restarts the active cycle and causes a spray. Run `af-aroma sync-time` when you need it; the bridge does it once a day at 04:00 (`--sync-at`).

## Library

```python
import asyncio
from af_aroma_ble import AFAroma, Group

async def main():
    async with AFAroma("XX:XX:XX:XX:XX:XX") as d:
        print(await d.status())
        await d.set_power(True)
        await d.write_group(Group(index=0, start="08:00", end="20:00", spray_s=5, pause_s=600))
        await d.spray_now()

asyncio.run(main())
```

## HTTP bridge for Home Assistant

If Home Assistant can't reach the diffuser over Bluetooth (it runs elsewhere, or the diffuser is out of range), run the bridge on a small Linux box near the diffuser:

```
af-aroma-bridge --address XX:XX:XX:XX:XX:XX --port 8765 --token CHANGE_ME
```

| Method | Path | Body |
|---|---|---|
| GET | `/status` | |
| POST | `/power` | `{"on": true}` |
| POST | `/spray` | |
| POST | `/key-lock` | `{"on": false}` |
| POST | `/oil-reset` | |
| POST | `/sync-time` | |
| POST | `/groups/<n>` | any of `start`, `end`, `spray_s`, `pause_s`, `days`, `enabled` |

`/status` is cached for 30 seconds by default (`--cache`). Requests are queued, since the diffuser takes one connection at a time. A systemd unit and Home Assistant YAML are in [examples/](examples/).

## Known limitations

- **Spray on demand:** there is no real "spray now" command. `spray` rewrites the active schedule group unchanged, which restarts its cycle with a spray, so it only works inside an active time window.
- **Test mode:** the "test mode" command from the app is accepted but ignored by the AP105 firmware.
- **Clock sync sprays:** setting the clock restarts the active cycle, so it sprays once if a group is active. The official app syncs on every connection, which is why the unit sprays when you open the app.
- **Password:** the app password is not enforced by the device (see PROTOCOL.md), so treat Bluetooth range as the only access control.

## Disclaimer

This project is not affiliated with, endorsed by or connected to Zhuhai Rongyuan Electronic Technology, HAOYUNMA or any seller. It contains no code from the official app. The protocol was worked out for interoperability and tested on one device. Use it at your own risk.

## License

MIT
