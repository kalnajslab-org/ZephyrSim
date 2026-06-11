# ZephyrSim.app (macOS Dock launcher)

This is a minimal macOS application bundle that lets you launch ZephyrSim
from the Dock or Launchpad instead of a terminal.

It does not contain a Python interpreter or any of ZephyrSim's code — it's
just a thin wrapper that runs the `zephyrsim` console script from a Python
environment where `zephyrsim` is already installed (see the main
[README](../../README.md) for installation instructions).

## Setup

1. Copy `ZephyrSim.app` to `~/Applications` (or `/Applications`):

   ```sh
   cp -R Resources/MacOS/ZephyrSim.app ~/Applications/
   ```

2. Edit `~/Applications/ZephyrSim.app/Contents/MacOS/ZephyrSim` and set
   `ZEPHYRSIM_BIN` to the path of the `zephyrsim` executable inside the
   Python environment where ZephyrSim is installed. For a conda
   environment, find it with:

   ```sh
   conda activate <env-name>
   which -a zephyrsim
   ```

   Use `which -a` (not just `which`) — on some systems the first match on
   `PATH` is a `zephyrsim` entry point from a *different* Python
   installation, which will fail with import errors for PyQt6.

3. Refresh the bundle so macOS picks up the change, then launch it:

   ```sh
   touch ~/Applications/ZephyrSim.app
   open ~/Applications/ZephyrSim.app
   ```

4. While ZephyrSim is running, right-click its Dock icon and choose
   **Options → Keep in Dock**.

## Troubleshooting

If the app fails to launch, check the log file written by the launcher:

```sh
cat ~/Library/Logs/ZephyrSim.log
```

If the Dock icon doesn't update after replacing `icon.icns`, restart the
Dock:

```sh
killall Dock
```

## Updating the version

`Contents/Info.plist` has `CFBundleVersion` / `CFBundleShortVersionString`
fields that are purely cosmetic (shown in Finder's "Get Info" panel). They
are independent of the installed `zephyrsim` package version, which is
shown in the application's window title bars.
