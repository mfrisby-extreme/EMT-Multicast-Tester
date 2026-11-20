## Downloads

| Platform | GUI | Command line |
|---|---|---|
| Windows x64 | `EMT-windows-x64.zip` | `emt-cli-windows-x64.zip` |
| Windows ARM64 | `EMT-windows-arm64.zip` | `emt-cli-windows-arm64.zip` |
| macOS Apple Silicon | `EMT-macos-arm64.zip` | `emt-cli-macos-arm64.tar.gz` |
| macOS Intel | `EMT-macos-x64.zip` | `emt-cli-macos-x64.tar.gz` |
| Linux x64 | `EMT-linux-x64.tar.gz` | `emt-cli-linux-x64.tar.gz` |
| Linux ARM64 | `EMT-linux-arm64.tar.gz` | `emt-cli-linux-arm64.tar.gz` |

**Minimum versions:** the GUI needs Windows 10 / 11 on ARM, macOS 14, or a Linux with
glibc 2.35 (x64) / 2.39 (ARM64). The command-line tool also runs on older Linux,
including Raspberry Pi OS 12.

## First run

- **Windows:** unzip and run `EMT.exe` from the `EMT` folder; keep the folder together.
  If SmartScreen says *"Windows protected your PC"*, choose **More info → Run anyway**.
- **macOS:** unzip and move `EMT.app` to Applications. The first time, right-click it
  and choose **Open**: the app isn't signed yet, so a double-click is blocked.
- **Linux:** extract and run `EMT/EMT`. Qt needs the usual desktop libraries
  (e.g. `libxcb-cursor0`, `libxkbcommon-x11-0`, `libegl1` on Debian/Ubuntu).
- **All platforms:** allow the app through the firewall when asked, or no multicast
  traffic will be received.

