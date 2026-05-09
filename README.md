# PingMonitor

A lightweight desktop utility for monitoring the availability of network hosts in real time. Built with Python and Tkinter — no external dependencies required.

## Features

- Add any number of IP addresses or hostnames to monitor
- Configurable check interval (10 seconds to 5 minutes)
- Per-host tracking of uptime %, total uptime, live downtime, and latency
- Live downtime counter updates in real time while a host is offline
- Failure count — tracks how many times each host has gone offline
- Event log with timestamped online/offline transitions
- Summary stats bar: total hosts, online, offline, failures, check rounds
- Export log and host stats to CSV
- Config and log auto-saved between sessions (JSON)
- Runs as a standalone executable (no installation needed)

## Requirements

- Python 3.10+
- No external packages — standard library only (`tkinter`, `subprocess`, `threading`, `csv`, `json`, `platform`)

## Usage

```bash
python ping_monitor.py
```

Or run the compiled `.exe` directly on Windows.

## Building

Build a standalone executable using [auto-py-to-exe](https://github.com/brentvollebregt/auto-py-to-exe):

| Setting | Value |
|---------|-------|
| Script | `ping_monitor.py` |
| One File | ✅ |
| Console | ❌ Window Based |
| Additional files | None required |

## Notes

- `ping_monitor_config.json` — saved host list and interval setting (auto-created on first run)
- `ping_monitor_log.json` — event log, last 500 entries (auto-created on first run)
