from __future__ import annotations

import math
import platform
import re
import subprocess
from typing import Dict, Optional, Tuple


def detect_os() -> str:
    return platform.system().lower()


def is_supported_os(os_name: str) -> bool:
    return os_name in {"windows", "linux"}


def build_ping_command(target: str, timeout_ms: int) -> list[str]:
    os_name = detect_os()
    if os_name == "windows":
        return ["ping", "-n", "1", "-w", str(timeout_ms), target]
    if os_name == "linux":
        # Linux ping timeout is in seconds; keep millisecond precision as fractional seconds.
        timeout_s = max(timeout_ms, 1) / 1000.0
        timeout_arg = f"{timeout_s:.3f}".rstrip("0").rstrip(".")
        return ["ping", "-n", "-c", "1", "-W", timeout_arg, target]
    raise RuntimeError("Unsupported OS. This tool currently supports Linux and Windows.")


def run_ping_once(target: str, timeout_ms: int) -> Tuple[bool, Optional[float], str]:
    """
    Runs one OS-specific single-echo ping command.
    Returns: (success, latency_ms or None, raw_output_text)
    """
    cmd = build_ping_command(target, timeout_ms)

    try:
        # text=True gives str, not bytes
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=(timeout_ms / 1000.0 + 2.0))
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        # Both Linux and Windows ping output include time=<N>ms on success.
        if proc.returncode == 0:
            # Parse time=XXms or time<1ms
            m = re.search(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", out, re.IGNORECASE)
            if m:
                return True, float(m.group(1)), out
            # Sometimes localized or odd output; treat as success without a parsed time
            return True, None, out
        else:
            return False, None, out
    except subprocess.TimeoutExpired as e:
        out = ""
        if getattr(e, "stdout", None):
            out += str(e.stdout)
        if getattr(e, "stderr", None):
            out += "\n" + str(e.stderr)
        return False, None, out
    except Exception as e:
        return False, None, f"Exception running ping: {e}"


def ping_targets(targets: list[str], timeout_ms: int) -> Dict[str, float]:
    latency: Dict[str, float] = {}
    for target in targets:
        ok, ms, _out = run_ping_once(target, timeout_ms)
        if ok:
            # If we couldn't parse the time, record as NaN but not timeout.
            if ms is None:
                latency[target] = float("nan")
            else:
                latency[target] = float(ms)
        else:
            latency[target] = -1.0
    return latency


def best_of_two(latencies: Dict[str, float]) -> Tuple[float, str]:
    """
    latencies: per-target latency_ms; -1 means timeout.
    Choose:
    - any successful ping beats timeout
    - among successes, lower latency wins
    """
    successes = [(t, ms) for t, ms in latencies.items() if ms >= 0]
    if not successes:
        return -1.0, ""
    t_best, ms_best = min(successes, key=lambda x: x[1])
    return ms_best, t_best


def choose_best_latency(latency: Dict[str, float]) -> Tuple[float, str]:
    # Treat NaN (success but unknown time) as a very high latency, still better than timeout.
    lat_for_choice: Dict[str, float] = {}
    for t, v in latency.items():
        if v == -1.0:
            lat_for_choice[t] = -1.0
        elif math.isnan(v):
            lat_for_choice[t] = 10_000.0
        else:
            lat_for_choice[t] = v

    best_ms, best_t = best_of_two(lat_for_choice)

    # If the chosen best was placeholder due to NaN parse, store NaN as best_latency.
    if best_t and math.isnan(latency[best_t]):
        best_latency = float("nan")
    else:
        best_latency = best_ms

    # If both timeouts, best_ms == -1.
    if best_t == "":
        best_latency = -1.0

    return best_latency, best_t
