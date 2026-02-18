#!/usr/bin/env python3
"""
netlog.py — Simple home internet stability logger (Windows-friendly)

See README.md for usage, examples, and output details.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
import time
from pathlib import Path
from typing import List, Optional

from netlog.aggregate import build_per_minute, build_three_min_bars, detect_outages
from netlog.io import save_text, write_per_minute_csv, write_raw_csv
from netlog.model import (
    Config,
    Sample,
    TARGETS,
    LATENCY_PNG_NAME,
    PER_MINUTE_NAME,
    RAW_SAMPLES_NAME,
    SUMMARY_NAME,
    format_duration,
    iso_ts,
    local_now,
)
from netlog.ping import choose_best_latency, detect_os, is_supported_os, ping_targets
from netlog.report import build_summary, plot_latency_png, render_bar_line


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Log ping evidence for unstable home internet.")
    dur = p.add_mutually_exclusive_group()
    dur.add_argument("--hours", type=float, default=8.0, help="How long to run (hours). Default: 8")
    dur.add_argument("--minutes", type=float, default=None, help="How long to run (minutes).")
    dur.add_argument("--seconds", type=float, default=None, help="How long to run (seconds).")

    p.add_argument("--interval", type=float, default=2.0, help="Sampling interval seconds. Default: 2")
    p.add_argument("--outdir", type=str, default="logs", help="Output folder. Default: logs")

    p.add_argument("--ping_timeout_ms", type=int, default=1000, help="Per-ping timeout in ms. Default: 1000")

    # Outage definition
    p.add_argument("--outage_seconds", type=int, default=30, help="Outage = consecutive timeouts lasting >= N seconds. Default: 30")

    # OK-minute compliance thresholds
    p.add_argument("--ok_latency_ms", type=float, default=80.0, help="OK minute latency threshold (ms). Default: 80")
    p.add_argument("--ok_loss_pct", type=float, default=5.0, help="OK minute loss threshold (%). Default: 5")
    p.add_argument("--ok_minutes_pct", type=float, default=80.0, help="Must be OK in at least this % of minutes. Default: 80")

    # 3-minute bar thresholds
    p.add_argument("--bar_window_seconds", type=int, default=180, help="Status bar window size in seconds. Default: 180")
    p.add_argument("--bar_max_windows", type=int, default=20, help="Max number of windows shown. Default: 20")
    p.add_argument("--bar_green_lat_ms", type=float, default=60.0, help="Bar green latency threshold (ms). Default: 60")
    p.add_argument("--bar_red_lat_ms", type=float, default=120.0, help="Bar red latency threshold (ms). Default: 120")
    p.add_argument("--bar_green_loss_pct", type=float, default=2.0, help="Bar green loss threshold (%). Default: 2")
    p.add_argument("--bar_red_loss_pct", type=float, default=10.0, help="Bar red loss threshold (%). Default: 10")

    return p.parse_args()


def compute_total_seconds(args: argparse.Namespace) -> float:
    if args.minutes is not None:
        return float(args.minutes) * 60.0
    if args.seconds is not None:
        return float(args.seconds)
    return float(args.hours) * 3600.0


def build_config(args: argparse.Namespace) -> Config:
    total_seconds = compute_total_seconds(args)
    return Config(
        outdir=Path(args.outdir),
        interval_s=float(args.interval),
        total_seconds=total_seconds,
        ping_timeout_ms=int(args.ping_timeout_ms),
        outage_seconds=int(args.outage_seconds),
        ok_latency_ms=float(args.ok_latency_ms),
        ok_loss_pct=float(args.ok_loss_pct),
        ok_minutes_pct=float(args.ok_minutes_pct),
        bar_window_seconds=int(args.bar_window_seconds),
        bar_max_windows=int(args.bar_max_windows),
        bar_green_lat_ms=float(args.bar_green_lat_ms),
        bar_red_lat_ms=float(args.bar_red_lat_ms),
        bar_green_loss_pct=float(args.bar_green_loss_pct),
        bar_red_loss_pct=float(args.bar_red_loss_pct),
    )


def validate_config(cfg: Config) -> Optional[str]:
    if cfg.interval_s <= 0:
        return "ERROR: --interval must be > 0"
    if cfg.total_seconds <= 0:
        return "ERROR: duration must be > 0"
    if cfg.bar_window_seconds <= 0:
        return "ERROR: --bar_window_seconds must be > 0"
    if cfg.bar_max_windows <= 0:
        return "ERROR: --bar_max_windows must be > 0"
    if cfg.bar_green_lat_ms >= cfg.bar_red_lat_ms:
        return "ERROR: --bar_green_lat_ms must be < --bar_red_lat_ms"
    if cfg.bar_green_loss_pct >= cfg.bar_red_loss_pct:
        return "ERROR: --bar_green_loss_pct must be < --bar_red_loss_pct"
    return None


def main() -> int:
    args = parse_args()
    cfg = build_config(args)

    error = validate_config(cfg)
    if error:
        print(error, file=sys.stderr)
        return 2

    os_name = detect_os()
    if not is_supported_os(os_name):
        print("ERROR: Unsupported OS. This tool currently supports Linux and Windows.", file=sys.stderr)
        return 2

    run_id = local_now().strftime("run_%Y%m%d_%H%M%S")
    run_outdir = cfg.outdir / run_id
    run_outdir.mkdir(parents=True, exist_ok=True)
    raw_csv_path = run_outdir / RAW_SAMPLES_NAME
    per_min_path = run_outdir / PER_MINUTE_NAME
    png_path = run_outdir / LATENCY_PNG_NAME
    summary_path = run_outdir / SUMMARY_NAME

    print(f"Writing outputs to: {run_outdir.resolve()}")
    print(
        f"Duration: {format_duration(cfg.total_seconds)} | Interval: {cfg.interval_s:.2f}s | "
        f"Targets: {', '.join(TARGETS)}"
    )
    print("Press Ctrl+C to stop early (summary will still be generated).")
    print("")

    samples: List[Sample] = []
    start = local_now()
    end_deadline = start + dt.timedelta(seconds=cfg.total_seconds)
    next_bar_elapsed_s = cfg.bar_window_seconds

    # Main loop
    next_sample_time = time.monotonic()
    try:
        while local_now() < end_deadline:
            # Sleep until next scheduled sample time (helps keep a steady interval)
            now_mono = time.monotonic()
            if now_mono < next_sample_time:
                time.sleep(next_sample_time - now_mono)

            ts = local_now()

            latency = ping_targets(TARGETS, cfg.ping_timeout_ms)
            best_latency, best_t = choose_best_latency(latency)

            samples.append(
                Sample(
                    ts=ts,
                    latency_ms={
                        "8.8.8.8": float(latency["8.8.8.8"]),
                        "1.1.1.1": float(latency["1.1.1.1"]),
                    },
                    best_latency_ms=float(best_latency),
                    best_target=best_t,
                )
            )

            # Light console progress every ~30 seconds
            if len(samples) == 1 or (len(samples) % max(1, int(30 / cfg.interval_s)) == 0):
                last = samples[-1]
                bl = last.best_latency_ms
                bl_str = "timeout" if bl < 0 else ("NaN" if math.isnan(bl) else f"{bl:.0f} ms")
                print(f"[{iso_ts(last.ts)}] best={bl_str} (best_target={last.best_target or 'none'})")

            # Status bars (print when a new window completes)
            elapsed_s = (samples[-1].ts - start).total_seconds()
            if elapsed_s >= next_bar_elapsed_s:
                outages_live = detect_outages(samples, interval_s=cfg.interval_s, outage_seconds=cfg.outage_seconds)
                bars = build_three_min_bars(samples, outages_live, start_ts=start, cfg=cfg)
                bar_line = render_bar_line(bars, use_color=True)
                if bar_line:
                    print(f"Status bars (last {cfg.bar_max_windows}): {bar_line}")
                next_bar_elapsed_s += cfg.bar_window_seconds

            next_sample_time += cfg.interval_s

    except KeyboardInterrupt:
        print("\nStopped early (Ctrl+C). Generating outputs...")

    if not samples:
        print("No samples collected.")
        return 1

    # Write raw samples
    write_raw_csv(raw_csv_path, samples)

    # Detect outages
    outages = detect_outages(samples, interval_s=cfg.interval_s, outage_seconds=cfg.outage_seconds)

    # Per-minute aggregation
    aggs = build_per_minute(samples, outages)
    write_per_minute_csv(per_min_path, aggs)

    # Plot
    png_written = False
    try:
        plot_latency_png(png_path, aggs, ok_latency_ms=cfg.ok_latency_ms, ok_loss_pct=cfg.ok_loss_pct)
        png_written = True
    except RuntimeError:
        print("\nNOTE: matplotlib not installed, skipping latency.png")
        print("Install it with: pip install matplotlib")
    except Exception as e:
        print(f"\nWARNING: Failed to generate plot: {e}")

    # Summary
    bars_final = build_three_min_bars(samples, outages, start_ts=start, cfg=cfg)
    bar_line_plain = render_bar_line(bars_final, use_color=False)
    summary = build_summary(
        samples=samples,
        aggs=aggs,
        outages=outages,
        cfg=cfg,
        bar_line_plain=bar_line_plain,
    )

    print("\n" + summary)
    save_text(summary_path, summary)

    print("Saved:")
    print(f" - {raw_csv_path}")
    print(f" - {per_min_path}")
    if png_written:
        print(f" - {png_path}")
    print(f" - {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
