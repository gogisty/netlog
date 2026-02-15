from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import List

from .model import MinuteAgg, Sample, iso_ts


def write_raw_csv(path: Path, samples: List[Sample]) -> None:
    fieldnames = [
        "timestamp_local_iso",
        "latency_ms_8.8.8.8",
        "latency_ms_1.1.1.1",
        "best_latency_ms",
        "best_target",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in samples:
            row = {
                "timestamp_local_iso": iso_ts(s.ts),
                "latency_ms_8.8.8.8": f"{s.latency_ms['8.8.8.8']:.3f}",
                "latency_ms_1.1.1.1": f"{s.latency_ms['1.1.1.1']:.3f}",
                "best_latency_ms": f"{s.best_latency_ms:.3f}",
                "best_target": s.best_target,
            }
            w.writerow(row)


def write_per_minute_csv(path: Path, aggs: List[MinuteAgg]) -> None:
    fieldnames = [
        "minute_start_local_iso",
        "avg_latency_ms",
        "loss_pct",
        "timeouts",
        "samples",
        "outage_in_minute",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for a in aggs:
            w.writerow(
                {
                    "minute_start_local_iso": iso_ts(a.minute_start),
                    "avg_latency_ms": "" if math.isnan(a.avg_latency_ms) else f"{a.avg_latency_ms:.3f}",
                    "loss_pct": f"{a.loss_pct:.2f}",
                    "timeouts": str(a.timeouts),
                    "samples": str(a.samples),
                    "outage_in_minute": "1" if a.outage_in_minute else "0",
                }
            )


def save_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(text)
