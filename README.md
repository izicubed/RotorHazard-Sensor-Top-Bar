# Sensor Top Bar (RotorHazard plugin)

A dark, modern, flat-design bar pinned to the top of the page, showing live
telemetry from whatever sensors are installed — plus live Raspberry Pi load.

- **Network** — connection type (Wi-Fi / Ethernet / both); the IP address(es)
  and adapter names appear on hover or when you click the chevron.
- **Core temperature** (Raspberry Pi), emphasised as the most important reading.
- **System** — live Raspberry Pi load: **CPU** usage %, **RAM** usage %, and
  **free disk** space. Load averages, MB used/total and GB free/used/total
  appear on hover / click. Values turn amber then red as usage climbs.
- **Battery** — charge **%** with a colour-coded glyph, plus pack voltage.
  Per-cell voltage, current, power and estimated mAh remaining appear on
  hover / click.
- **Climate** — outside temperature, humidity, pressure.

Groups are visually separated. Tiles only appear for sensors that are present.

## Installation

### From the Community Plugins manager (recommended)

Open the RotorHazard web UI → **Settings → Plugins**, find **Sensor Top Bar**
in the community catalog, install it, and restart the server.

### Via the web UI (Upload)

1. Download **`sensor_topbar.zip`** from the
   [latest release](https://github.com/izicubed/RotorHazard-Sensor-Top-Bar/releases/latest)
   (the asset named `sensor_topbar.zip` — **not** the "Source code (zip)").
2. In RotorHazard → **Settings → Plugins → Upload**, select that `.zip`
   **as-is** — do not unzip and re-zip it. The archive must contain a single
   `sensor_topbar/` folder with `manifest.json` inside; a zip whose files sit at
   the root is rejected with *"Uploaded plugin is invalid."*
3. Restart the server when prompted.

### Manual (copy folder)

1. Copy the `custom_plugins/sensor_topbar` folder into `<rh-data>/plugins/`
   (typically `~/rh-data/plugins/sensor_topbar/`).
2. Restart the RotorHazard server.

There are **no edits to any RotorHazard core template or file**. The plugin
injects its own front-end (JS + CSS) through the standard plugin UI
panel/markdown mechanism, so it survives RotorHazard upgrades untouched.

To uninstall, delete the folder (or use the plugin manager) and restart.

### Where the bar appears

Because RotorHazard offers no hook for a plugin to add global scripts to
*every* page, the bar shows on the pages that render plugin UI panels:
**Run, Settings, Results, Marshal and Format**. These are the operational
pages where telemetry is most useful. (The plain landing/Event/Current pages
do not host plugin panels, so the bar does not appear there.)

## Raspberry Pi load (CPU / RAM / Disk)

The System group reads the Pi's load with pure standard-library helpers — no
`psutil` dependency:

- **CPU %** — whole-CPU busy percentage from `/proc/stat` deltas, plus load
  averages via `os.getloadavg()`.
- **RAM** — used/total MB and percent from `/proc/meminfo`.
- **Disk** — free/used/total GB and percent for `/` via `os.statvfs`.

Each field degrades gracefully: a tile is simply hidden if the reading is
unavailable on the current platform.

## Battery configuration

Open **Settings → Sensor Top Bar** and set:

| Option | Meaning |
| --- | --- |
| Battery voltage source | Which sensor reading is the pack voltage. *Auto-detect* picks the first reading measured in volts. |
| Battery cells (S) | Number of cells in series. |
| Empty voltage per cell | Per-cell voltage treated as 0 % (e.g. 3.3 V for LiPo). |
| Full voltage per cell | Per-cell voltage treated as 100 % (e.g. 4.2 V for LiPo). |
| Pack capacity (mAh) | Used to estimate remaining capacity. |
| Charge % method | *LiPo discharge curve* (accurate for lithium packs) or *Linear*. |
| Refresh interval | How often the bar updates (seconds). |
| Demo mode | Simulates sensor data so the bar can be previewed on a PC with no hardware. |

The charge % is computed server-side. In *LiPo curve* mode a standard
resting-voltage discharge curve is rescaled between your configured empty/full
per-cell voltages, so it stays realistic for non-standard ranges.

## Notes

- Works over RotorHazard's existing Socket.IO connection; no extra ports.
- Enable **Demo mode** in Settings to preview the bar on a machine with no
  sensors connected.

## License

Released under the [MIT NON-AI License](LICENSE), matching the RotorHazard
project license.
