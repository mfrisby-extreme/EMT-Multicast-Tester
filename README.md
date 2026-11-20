# EMT Multicast Tester

A cross-platform (Windows / macOS / Linux) multicast send/receive tool for
validating IGMP/MLD, IGMP snooping, queriers and PIM. It has a GUI and a
scriptable CLI, and measures loss, duplicates, reordering, jitter and join
latency per stream.

## Features

- **Sender:** several streams at once, each with its own group, port, interface, packet size, rate (pps or Mbps), TTL, DSCP, packet count or duration, and loopback setting.
- **Receiver:** joins one or more groups, either any-source (ASM) or source-specific (SSM / IGMPv3), on the interface you choose.
- **Live receive stats:**
  - pps and Mbps
  - lost packets and loss %
  - duplicates and out-of-order packets
  - RFC 3550 jitter (doesn't need synchronized clocks)
  - **join → first packet** time, which shows how quickly the network starts forwarding the flow
- **Bulk streams:** set a first group and a number of groups (e.g. 234.5.6.7 × 5) to start or join consecutive groups in one click.
- **Per-stream colors and a live graph:** each stream gets a color and its own line on the rate graph (Mbps or pps, 30 s to 10 min window), so a drop on a single group stands out. Untick a stream's color box to hide its line.
- **Leave / Rejoin / Reset counters** to test IGMP leave and graft behaviour.
- **Third-party streams:** traffic that isn't from EMT (VLC, iperf, encoders) is still counted as "raw" packets, without the sequence analysis.
- **Export:** CSV export from the GUI. The CLI can write a CSV log and a JSON summary.

## Quick start (from source)

Keep the virtual environment **outside OneDrive**; syncing thousands of Qt
files is slow.

```bash
python3 -m venv ~/.venvs/emt
source ~/.venvs/emt/bin/activate        # Windows: %USERPROFILE%\.venvs\emt\Scripts\activate
pip install -e ".[dev,build]"

python -m emt                            # GUI
emt-cli ifaces                           # list interfaces
emt-cli send -g 239.1.1.1 -p 5000 --pps 1000 -i en0
emt-cli recv -g 239.1.1.1 -p 5000 -i en0 --duration 30 --json
emt-cli recv -g 232.1.1.1 -s 10.1.1.10 -i eth0      # SSM join
```

Run `emt-cli send -h` and `emt-cli recv -h` to see every option.

## Building distributables

PyInstaller can't cross-compile, so each OS and CPU architecture is built on
its own machine. The GitHub Actions workflow in `.github/workflows/build.yml`
builds all six targets in 7 jobs: one per target, each building both the GUI
and the CLI, plus a separate Linux ARM64 CLI build on an older Ubuntu so it
runs on more ARM machines. Each job runs the tests and a startup smoke test,
then uploads its zip/tar.gz files.

### Supported platforms

| Target | GUI (`EMT`) minimum | CLI (`emt-cli`) minimum | CI runner |
|---|---|---|---|
| Windows x64 | Windows 10 | Windows 10 | `windows-latest` |
| Windows ARM64 | Windows 11 on ARM | Windows 11 on ARM | `windows-11-arm` |
| macOS Apple Silicon | macOS 14 Sonoma | macOS 11 (expected) | `macos-latest` |
| macOS Intel | macOS 14 Sonoma | macOS 11 (expected) | `macos-15-intel` |
| Linux x64 | glibc 2.35 (Ubuntu 22.04, RHEL 9, Debian 12) | glibc 2.35 | `ubuntu-22.04` |
| Linux ARM64 | glibc 2.39 (Ubuntu 24.04, Debian 13) | glibc 2.35 (Ubuntu 22.04, Raspberry Pi OS 12) | `ubuntu-24.04-arm` / `ubuntu-22.04-arm` |

The GUI minimums come from the PySide6 6.12 wheels (macOS 14; glibc 2.34 on
x64 and 2.39 on ARM64). The CLI has no Qt, so on older systems, notably
Raspberry Pi OS Bookworm, use the CLI. Pinning PySide6 6.9.x would lower the GUI
to macOS 12 and glibc 2.28 on Linux x64, but it doesn't help Linux ARM64.

On Linux, the GUI also needs the usual X11/Wayland libraries for Qt (for
example `libxcb-cursor0`, `libxkbcommon-x11-0` and `libegl1` on Debian/Ubuntu).

The ARM64 runners (`windows-11-arm`, `ubuntu-*-arm`) are free for public
repositories. A private repository may need ARM runners enabled on its GitHub
plan, or an organization-hosted ARM runner.

```bash
python scripts/build.py           # GUI + CLI into dist/
python scripts/build.py --cli     # CLI only (small, no Qt)
```

| Output | Description |
|---|---|
| `dist/EMT.app` (macOS), `dist/EMT/` (Windows/Linux) | GUI, one-folder build |
| `dist/emt-cli/emt-cli.exe` (Windows), `dist/emt-cli` (macOS/Linux) | CLI: a folder on Windows, a single file elsewhere |

### Antivirus false positives

Unsigned PyInstaller executables are often flagged by Windows antivirus. The
build reduces this in two ways: UPX compression is always off, and both
Windows executables are folder builds rather than single files that unpack
to a temp folder. Code signing (below) is the main remaining step.

### Code signing (not set up yet)

- **macOS:** unsigned apps are blocked by Gatekeeper; users must right-click the app and choose **Open**. Signing with a Developer ID and notarizing (`codesign` + `notarytool`) removes this.
- **Windows:** unsigned PyInstaller builds may trigger SmartScreen or Defender warnings. Sign them with an Authenticode certificate.

## Releasing

Releases are made by pushing a version tag; CI does the rest.

1. Set `__version__` in `src/emt/__init__.py` (e.g. `"0.2.0"`) and commit.
2. Tag that commit and push the tag:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
3. CI builds all targets. If every job passes, the **release** job checks that the
   tag matches `__version__`, then creates a **draft** release with all twelve
   downloads attached. Its notes are `packaging/release-notes.md` (download table
   and first-run tips), followed by notes generated from the commits since the
   previous tag.
4. On GitHub, open **Releases**, review the draft, edit the notes if needed, and click
   **Publish release**. Nothing is public until then.

Re-running the workflow for the same tag replaces the files on the existing draft.

## Platform notes

| Topic | Notes |
|---|---|
| Firewall | Windows Defender Firewall and the macOS application firewall will ask on the first receive. Allow the app, or no multicast will arrive. |
| Interface choice | Always choose the interface explicitly on multi-homed hosts (VPN, Docker, Wi-Fi + wired). "Auto" follows the OS default route, which is often not the test NIC. |
| Same-machine tests | By default the sender doesn't loop traffic back to its own host, and the receiver ignores packets from this host's addresses. A receiver therefore only counts traffic that really came through the network. To test on one machine, tick **Loop back to this host** (CLI `--loop`) and untick **Ignore traffic from this host** (CLI `--include-local`). The receiver-side filter exists because loopback is a sender setting on macOS/Linux but a receiver setting on Windows. |
| DSCP | Works on macOS and Linux. Windows ignores `IP_TOS` unless a Group Policy QoS policy allows it. |
| SSM | Supported for IPv4 on all three OSes. IPv6 SSM (MLDv2 source filters) is not implemented yet. |
| Rate | Pure Python sends tens of thousands of packets per second per stream. That's fine for functional and snooping tests, but it isn't a line-rate traffic generator. |

## Packet format

Each EMT packet starts with a 24-byte header (`src/emt/core/packet.py`):

```
magic "EMT1" | stream_id u32 | seq u64 | send_time_ns u64     (network byte order)
```

## Project layout

```
src/emt/core/     Qt-free engine: sockets (all OS quirks), sender, receiver, stats
src/emt/cli.py    command-line interface
src/emt/gui/      PySide6 GUI
packaging/        PyInstaller entry scripts (+ optional emt.ico / emt.icns)
scripts/build.py  build script
tests/            pytest (unit + loopback send/receive)
```

## License note

PySide6 is LGPLv3. The one-folder GUI build keeps the Qt libraries as
separate, replaceable files. If you distribute the build outside the
company, include the LGPL notice and point users to the PySide6 source.
