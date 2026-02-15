from __future__ import annotations

import math
from pathlib import Path
from typing import List

from .model import BarStatus, Config, MinuteAgg, Outage, Sample, TARGETS, format_duration, iso_ts

# Matplotlib is NOT in the standard library. We'll use it if available; otherwise we raise a clear message.
# Install: pip install matplotlib
try:
    import matplotlib.pyplot as plt  # type: ignore
except Exception:
    plt = None


def plot_latency_png(path: Path, aggs: List[MinuteAgg], ok_latency_ms: float, ok_loss_pct: float) -> None:
    if plt is None:
        raise RuntimeError("matplotlib is not installed. Install it with: pip install matplotlib")

    # x axis: minute index
    x = list(range(len(aggs)))
    y = [a.avg_latency_ms for a in aggs]

    # Markers for minutes with any loss/timeout (loss_pct > 0 or timeouts > 0) or outage
    loss_or_timeout_idx = [i for i, a in enumerate(aggs) if (a.timeouts > 0 or a.loss_pct > 0.0)]
    outage_idx = [i for i, a in enumerate(aggs) if a.outage_in_minute]

    plt.figure(figsize=(12, 5))
    plt.plot(x, y)  # default style/colors per requirement
    plt.xlabel("Minute index (0 = first minute in run)")
    plt.ylabel("Avg latency (ms) — best of two targets")
    plt.title("Per-minute average latency (with minutes flagged for loss/timeout/outage)")

    # Horizontal guideline for OK latency
    plt.axhline(ok_latency_ms, linestyle="--", linewidth=1)

    # Mark minutes with loss/timeout using scatter at their y value (or at 0 if NaN)
    if loss_or_timeout_idx:
        yy = []
        for i in loss_or_timeout_idx:
            v = y[i]
            yy.append(0.0 if math.isnan(v) else v)
        plt.scatter(loss_or_timeout_idx, yy, marker="x")

    # Mark outage minutes with a different marker at y=0
    if outage_idx:
        plt.scatter(outage_idx, [0.0] * len(outage_idx), marker="o")

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def render_bar_line(statuses: List[BarStatus], use_color: bool) -> str:
    if not statuses:
        return ""

    if not use_color:
        return " ".join(s.value for s in statuses)

    color_map = {
        BarStatus.GOOD: "\x1b[32m",
        BarStatus.WARN: "\x1b[33m",
        BarStatus.BAD: "\x1b[31m",
    }
    reset = "\x1b[0m"
    colored = [f"{color_map.get(s, '')}{s.value}{reset}" for s in statuses]
    return " ".join(colored)


def build_summary(
    samples: List[Sample],
    aggs: List[MinuteAgg],
    outages: List[Outage],
    cfg: Config,
    bar_line_plain: str,
) -> str:
    lines: List[str] = []

    if not samples:
        return "No samples collected.\n"

    start_ts = samples[0].ts
    end_ts = samples[-1].ts
    elapsed_s = (end_ts - start_ts).total_seconds()
    total_samples = len(samples)
    total_timeouts = sum(1 for s in samples if s.best_latency_ms < 0)
    overall_loss_pct = total_timeouts / total_samples * 100.0 if total_samples else 0.0

    # Worst 10 minutes by loss, then by latency (ignore NaN latency when ranking latency)
    worst_by_loss = sorted(aggs, key=lambda a: (a.loss_pct, a.timeouts), reverse=True)[:10]
    worst_by_latency = sorted(
        [a for a in aggs if not math.isnan(a.avg_latency_ms)],
        key=lambda a: a.avg_latency_ms,
        reverse=True
    )[:10]

    # Outage count + total duration estimate:
    # For each outage run, duration approx = run_count * interval_s (bounded to >= outage_seconds)
    outage_durations = [o.sample_count * cfg.interval_s for o in outages]
    total_outage_s = sum(outage_durations)

    # "OK minute" compliance: avg_latency <= threshold AND loss% <= threshold AND no outage_in_minute
    ok_minutes = 0
    for a in aggs:
        latency_ok = (not math.isnan(a.avg_latency_ms)) and (a.avg_latency_ms <= cfg.ok_latency_ms)
        loss_ok = (a.loss_pct <= cfg.ok_loss_pct)
        outage_ok = (not a.outage_in_minute)
        if latency_ok and loss_ok and outage_ok:
            ok_minutes += 1
    total_minutes = len(aggs)
    ok_pct = (ok_minutes / total_minutes * 100.0) if total_minutes else 0.0
    meets = ok_pct >= cfg.ok_minutes_pct

    # Extra: per-target stats (helpful for diagnosis)
    per_target_timeouts = {t: 0 for t in TARGETS}
    per_target_success_latencies = {t: [] for t in TARGETS}
    for s in samples:
        for t in TARGETS:
            ms = s.latency_ms[t]
            if ms < 0:
                per_target_timeouts[t] += 1
            else:
                per_target_success_latencies[t].append(ms)

    def avg_or_nan(values: List[float]) -> float:
        return sum(values) / len(values) if values else float("nan")

    lines.append("Internet Connection Evidence Summary")
    lines.append("=" * 36)
    lines.append(f"Start (local): {iso_ts(start_ts)}")
    lines.append(f"End   (local): {iso_ts(end_ts)}")
    lines.append(f"Elapsed: {format_duration(elapsed_s)}")
    lines.append(f"Sampling interval: {cfg.interval_s:.2f} s")
    lines.append("")
    lines.append("Overall (best-of-two targets per sample)")
    lines.append("--------------------------------------")
    lines.append(f"Total samples: {total_samples}")
    lines.append(f"Total timeouts: {total_timeouts}")
    lines.append(f"Overall packet loss % (timeouts/samples): {overall_loss_pct:.2f}%")
    lines.append("")
    lines.append("Per-target (raw) stats")
    lines.append("----------------------")
    for t in TARGETS:
        avg_ms = avg_or_nan(per_target_success_latencies[t])
        loss_pct = per_target_timeouts[t] / total_samples * 100.0 if total_samples else 0.0
        lines.append(
            f"{t}: timeouts={per_target_timeouts[t]} ({loss_pct:.2f}%), "
            f"avg_latency_ms={'NaN' if math.isnan(avg_ms) else f'{avg_ms:.2f}'}"
        )
    lines.append("")
    lines.append("Worst 10 minutes by loss% (then timeouts)")
    lines.append("----------------------------------------")
    for a in worst_by_loss:
        lines.append(
            f"{iso_ts(a.minute_start)}  loss={a.loss_pct:.2f}%  timeouts={a.timeouts}/{a.samples}  "
            f"avg_latency_ms={'NaN' if math.isnan(a.avg_latency_ms) else f'{a.avg_latency_ms:.2f}'}  "
            f"outage_in_minute={'YES' if a.outage_in_minute else 'NO'}"
        )
    lines.append("")
    lines.append("Worst 10 minutes by avg latency (successful samples only)")
    lines.append("---------------------------------------------------------")
    for a in worst_by_latency:
        lines.append(
            f"{iso_ts(a.minute_start)}  avg_latency_ms={a.avg_latency_ms:.2f}  "
            f"loss={a.loss_pct:.2f}%  timeouts={a.timeouts}/{a.samples}  "
            f"outage_in_minute={'YES' if a.outage_in_minute else 'NO'}"
        )
    lines.append("")
    lines.append(f"Outages (consecutive timeouts >= {int(cfg.outage_seconds)} seconds definition)")
    lines.append("---------------------------------------------------------")
    if outages:
        lines.append(f"Outage count: {len(outages)}")
        lines.append(f"Estimated total outage time: {format_duration(total_outage_s)}")
        lines.append("Outage list (start -> end, estimated duration):")
        for outage in outages:
            dur_s = outage.sample_count * cfg.interval_s
            lines.append(
                f"- {iso_ts(outage.start)} -> {iso_ts(outage.end)}  "
                f"(~{format_duration(dur_s)})  samples={outage.sample_count}"
            )
    else:
        lines.append("No outages detected by the configured definition.")
    lines.append("")
    lines.append("Compliance check")
    lines.append("----------------")
    lines.append("OK minute means:")
    lines.append(f"  a) avg latency <= {cfg.ok_latency_ms:.0f} ms")
    lines.append(f"  b) loss% <= {cfg.ok_loss_pct:.0f}%")
    lines.append("  c) no outage in that minute")
    lines.append(f"OK minutes: {ok_minutes}/{total_minutes} ({ok_pct:.2f}%)")
    lines.append(f"Requirement: OK in at least {cfg.ok_minutes_pct:.0f}% of minutes -> {'PASS' if meets else 'FAIL'}")
    if bar_line_plain:
        lines.append("")
        lines.append(f"Status bars ({cfg.bar_window_seconds}-second windows, last {cfg.bar_max_windows})")
        lines.append("-------------------------------------")
        lines.append(bar_line_plain)
        lines.append("Legend: G=good, Y=high latency/loss, R=outage/very poor")
    lines.append("")
    lines.append("How to use this report in a complaint")
    lines.append("-------------------------------------")
    lines.append("- Emphasize: total timeouts, outage count and total outage duration.")
    lines.append("- Point to: worst minutes by loss and by latency (specific timestamps).")
    lines.append("- Attach the PNG graph + per_minute.csv (easy to digest).")
    lines.append("- If you repeat runs across multiple days, patterns (e.g., evenings) strengthen the case.")
    lines.append("- Mention that two independent DNS targets were used; both targets timing out indicates broader connectivity issues.")

    return "\n".join(lines) + "\n"
