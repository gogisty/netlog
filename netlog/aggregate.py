from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .model import BarStatus, Config, MinuteAgg, Outage, Sample, floor_to_minute


def detect_outages(samples: List[Sample], interval_s: float, outage_seconds: int) -> List[Outage]:
    """
    Outage = consecutive timeout samples (best_latency_ms == -1) lasting >= outage_seconds.
    Returns list of (start_ts, end_ts, sample_count) where end_ts is timestamp of last timeout sample.
    """
    if not samples:
        return []

    required_samples = max(1, math.ceil(outage_seconds / max(interval_s, 0.001)))

    outages: List[Outage] = []
    run_start: Optional[dt.datetime] = None
    run_count = 0
    last_ts: Optional[dt.datetime] = None

    for s in samples:
        is_timeout = (s.best_latency_ms < 0)
        if is_timeout:
            if run_start is None:
                run_start = s.ts
                run_count = 1
            else:
                run_count += 1
            last_ts = s.ts
        else:
            if run_start is not None and run_count >= required_samples and last_ts is not None:
                outages.append(Outage(run_start, last_ts, run_count))
            run_start = None
            run_count = 0
            last_ts = None

    # Close trailing run
    if run_start is not None and run_count >= required_samples and last_ts is not None:
        outages.append(Outage(run_start, last_ts, run_count))

    return outages


def build_per_minute(samples: List[Sample], outage_intervals: List[Outage]) -> List[MinuteAgg]:
    """
    Aggregate by minute using best_latency_ms:
    - avg_latency_ms: average of successful samples only
    - loss_pct: % timeout samples (best_latency_ms == -1)
    - timeouts: count of timeout samples
    """
    buckets: Dict[dt.datetime, List[Sample]] = defaultdict(list)
    for s in samples:
        buckets[floor_to_minute(s.ts)].append(s)

    # Precompute minute -> outage overlap
    outage_minutes: set[dt.datetime] = set()
    for outage in outage_intervals:
        m = floor_to_minute(outage.start)
        end_min = floor_to_minute(outage.end)
        while m <= end_min:
            outage_minutes.add(m)
            m = m + dt.timedelta(minutes=1)

    minutes_sorted = sorted(buckets.keys())
    aggs: List[MinuteAgg] = []
    for m in minutes_sorted:
        ss = buckets[m]
        total = len(ss)
        timeouts = sum(1 for x in ss if x.best_latency_ms < 0)
        successes = [x.best_latency_ms for x in ss if x.best_latency_ms >= 0]
        if successes:
            avg = sum(successes) / len(successes)
        else:
            avg = float("nan")
        loss_pct = (timeouts / total * 100.0) if total else 0.0
        aggs.append(
            MinuteAgg(
                minute_start=m,
                avg_latency_ms=avg,
                loss_pct=loss_pct,
                timeouts=timeouts,
                samples=total,
                outage_in_minute=(m in outage_minutes),
            )
        )

    return aggs


def window_has_outage(window_start: dt.datetime, window_end: dt.datetime, outages: List[Outage]) -> bool:
    for outage in outages:
        if outage.start <= window_end and outage.end >= window_start:
            return True
    return False


def compute_window_stats(
    samples: List[Sample],
    window_start: dt.datetime,
    window_end: dt.datetime,
    outages: List[Outage],
) -> Tuple[Optional[float], float, bool]:
    total = 0
    timeouts = 0
    latencies: List[float] = []

    for s in samples:
        if s.ts < window_start or s.ts >= window_end:
            continue
        total += 1
        if s.best_latency_ms < 0:
            timeouts += 1
        elif not math.isnan(s.best_latency_ms):
            latencies.append(s.best_latency_ms)

    if total == 0:
        loss_pct = 0.0
    else:
        loss_pct = (timeouts / total) * 100.0

    avg_latency = (sum(latencies) / len(latencies)) if latencies else None
    has_outage = window_has_outage(window_start, window_end, outages)
    return avg_latency, loss_pct, has_outage


def classify_window(avg_latency: Optional[float], loss_pct: float, has_outage: bool, cfg: Config) -> BarStatus:
    if has_outage or loss_pct > cfg.bar_red_loss_pct or (avg_latency is not None and avg_latency > cfg.bar_red_lat_ms):
        return BarStatus.BAD
    if loss_pct > cfg.bar_green_loss_pct or (avg_latency is not None and avg_latency > cfg.bar_green_lat_ms):
        return BarStatus.WARN
    return BarStatus.GOOD


def build_three_min_bars(
    samples: List[Sample],
    outages: List[Outage],
    start_ts: dt.datetime,
    cfg: Config,
) -> List[BarStatus]:
    if not samples:
        return []

    last_ts = samples[-1].ts
    completed_windows = int(((last_ts - start_ts).total_seconds()) // cfg.bar_window_seconds)
    if completed_windows <= 0:
        return []

    start_idx = max(0, completed_windows - cfg.bar_max_windows)
    bars: List[BarStatus] = []
    for i in range(start_idx, completed_windows):
        w_start = start_ts + dt.timedelta(seconds=i * cfg.bar_window_seconds)
        w_end = w_start + dt.timedelta(seconds=cfg.bar_window_seconds)
        avg_latency, loss_pct, has_outage = compute_window_stats(samples, w_start, w_end, outages)
        bars.append(classify_window(avg_latency, loss_pct, has_outage, cfg))

    return bars
