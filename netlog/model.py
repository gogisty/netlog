from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict

TARGETS = ["8.8.8.8", "1.1.1.1"]

RAW_SAMPLES_NAME = "raw_samples.csv"
PER_MINUTE_NAME = "per_minute.csv"
LATENCY_PNG_NAME = "latency.png"
SUMMARY_NAME = "summary.txt"


@dataclass
class Sample:
    ts: dt.datetime  # timezone-aware local timestamp
    # Per-target latency in ms; -1.0 means timeout/failed
    latency_ms: Dict[str, float]
    # Chosen "best" latency across targets; -1.0 means both failed
    best_latency_ms: float
    # Which target was best; "" if both failed
    best_target: str


@dataclass
class MinuteAgg:
    minute_start: dt.datetime  # timezone-aware local timestamp (floor to minute)
    avg_latency_ms: float      # average of best_latency_ms for successful samples; NaN if none
    loss_pct: float            # % samples in that minute that are timeouts (best_latency_ms == -1)
    timeouts: int              # count of timeouts in that minute (best-of-two)
    samples: int               # total samples in that minute
    # Whether an outage (>= outage_seconds consecutive timeouts) overlaps this minute
    outage_in_minute: bool


@dataclass(frozen=True)
class Outage:
    start: dt.datetime
    end: dt.datetime
    sample_count: int


class BarStatus(Enum):
    GOOD = "|"
    WARN = "%"
    BAD = "X"


@dataclass(frozen=True)
class Config:
    outdir: Path
    interval_s: float
    total_seconds: float
    ping_timeout_ms: int
    outage_seconds: int
    ok_latency_ms: float
    ok_loss_pct: float
    ok_minutes_pct: float
    bar_window_seconds: int
    bar_max_windows: int
    bar_green_lat_ms: float
    bar_red_lat_ms: float
    bar_green_loss_pct: float
    bar_red_loss_pct: float


def local_now() -> dt.datetime:
    # timezone-aware local time
    return dt.datetime.now().astimezone()


def iso_ts(ts: dt.datetime) -> str:
    # ISO8601 with offset
    return ts.isoformat()


def floor_to_minute(ts: dt.datetime) -> dt.datetime:
    return ts.replace(second=0, microsecond=0)


def format_duration(seconds: float) -> str:
    # Simple H:MM:SS
    seconds_int = int(round(seconds))
    h = seconds_int // 3600
    m = (seconds_int % 3600) // 60
    s = seconds_int % 60
    return f"{h:d}:{m:02d}:{s:02d}"
