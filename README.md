# netlog

Simple home internet stability logger (Linux and Windows).

## Overview
- Pings two stable targets (Google DNS 8.8.8.8 and Cloudflare 1.1.1.1) every N seconds using the system `ping` command.
- Logs raw samples to CSV (timestamp + per-target latency + chosen "best" latency).
- Aggregates per-minute stats (average latency, loss %, timeouts).
- Generates a PNG graph and a plain-text summary report.

Why "best-of-two" per sample?
ISPs sometimes have odd routing to one destination. Using two independent, highly available targets reduces false positives.
For each sample we pick the better result (prefer successful ping; then lower latency). This reflects "did the internet work"
rather than "did a specific path to one host work". Both targets are still logged for diagnosis.

## Requirements
- Python 3 (standard library only).
- Supported OS: Linux and Windows (detected at startup).
- Optional: `matplotlib` for the PNG graph (`pip install matplotlib`).

## Quick start
From the folder containing `netlog.py`:

```powershell
# Default: 8 hours, sample every 2 seconds, output folder "logs"
python netlog.py

# Run 2 hours, sample every 1 second, write into a custom folder
python netlog.py --hours 2 --interval 1 --outdir my_logs

# Run for 30 minutes
python netlog.py --minutes 30 --interval 2 --outdir logs_30min

# Change outage definition and quality thresholds
python netlog.py --outage_seconds 30 --ok_latency_ms 80 --ok_loss_pct 5 --ok_minutes_pct 80
```

## Output files
Created under `--outdir` in a run-specific folder (example: `logs/run_20260215_114428/`).
- `raw_samples.csv`
- `per_minute.csv`
- `latency.png` (only if `matplotlib` is installed)
- `summary.txt`

Timestamps are saved in local time, ISO 8601 with your UTC offset.
Example: `2026-02-14T21:03:05.123456+02:00`

## Outage definition
Outage = consecutive timeout samples lasting at least `--outage_seconds`.

## Project layout
- `netlog.py` is the CLI entry point.
- `netlog/` contains the functional modules:
	- `model.py` (dataclasses, constants, time helpers)
	- `ping.py` (ping execution + best-of-two selection)
	- `aggregate.py` (outage detection, per-minute stats, status bars)
	- `report.py` (summary and plotting)
	- `io.py` (CSV/text output)

## Using this as evidence
- Highlight repeated timeouts, outage count, and total outage duration.
- Point to worst minutes by loss and by latency in `summary.txt`.
- Attach: `raw_samples.csv`, `per_minute.csv`, `latency.png`, and `summary.txt`.
- Repeating runs across multiple days makes time-of-day patterns compelling.
