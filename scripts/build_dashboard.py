"""Render dashboard 6 panel từ data/logs.jsonl theo contract config/dashboard.yaml.

Ví dụ:
    python scripts/build_dashboard.py
    python scripts/build_dashboard.py --output submission/evidence/11-dashboard-overview.png
    python scripts/build_dashboard.py --watch      # render lại mỗi refresh_seconds
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$" trong nhãn cost là ký tự thường
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import yaml  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.cli import configure_utf8_stdio  # noqa: E402
from app.metrics import percentile  # noqa: E402

DISPLAY_TZ = timezone(timedelta(hours=7), "UTC+7")

# Palette tham chiếu (light): series theo thứ tự cố định, status tách riêng.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"


def load_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            event["_ts"] = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        events.append(event)
    return events


def window_stats(events: list[dict]) -> dict:
    received = [e for e in events if e.get("event") == "request_received"]
    responses = [e for e in events if e.get("event") == "response_sent"]
    failed = [e for e in events if e.get("event") == "request_failed"]
    tool_events = [e for e in events if e.get("tool_success") is not None]
    latencies = [e["latency_ms"] for e in responses if e.get("latency_ms") is not None]
    ttfts = [e["ttft_ms"] for e in responses if e.get("ttft_ms") is not None]
    qualities = [e["quality_score"] for e in responses if e.get("quality_score") is not None]
    return {
        "requests": len(received),
        "responses": len(responses),
        "failed": len(failed),
        "p50": percentile(latencies, 50) if latencies else None,
        "p95": percentile(latencies, 95) if latencies else None,
        "p99": percentile(latencies, 99) if latencies else None,
        "ttft_p95": percentile(ttfts, 95) if ttfts else None,
        "error_rate_pct": 100 * len(failed) / len(received) if received else None,
        "errors_by_type": dict(Counter(e.get("error_type") or "unknown" for e in failed)),
        "retrieval_success_pct": (
            100 * sum(e["tool_success"] is True for e in tool_events) / len(tool_events)
            if tool_events
            else None
        ),
        "cost_usd": sum(e.get("cost_usd") or 0 for e in responses),
        "tokens_in": sum(e.get("tokens_in") or 0 for e in responses),
        "tokens_out": sum(e.get("tokens_out") or 0 for e in responses),
        "quality_mean": mean(qualities) if qualities else None,
    }


def per_minute(events: list[dict], start: datetime, end: datetime) -> list[tuple[datetime, dict]]:
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for event in events:
        buckets[event["_ts"].replace(second=0, microsecond=0)].append(event)
    minutes = []
    cursor = start.replace(second=0, microsecond=0)
    while cursor <= end:
        minutes.append((cursor, window_stats(buckets.get(cursor, []))))
        cursor += timedelta(minutes=1)
    return minutes


def threshold_value(stats: dict, panel_id: str, aggregation: str, active_minutes: float):
    if panel_id == "traffic" and aggregation == "rate_per_minute":
        return stats["requests"] / active_minutes if active_minutes else None
    mapping = {
        ("latency", "p95"): stats["p95"],
        ("errors", "error_rate_pct"): stats["error_rate_pct"],
        ("cost", "total"): stats["cost_usd"],
        ("tokens", "sum_by_field"): max(stats["tokens_in"], stats["tokens_out"]),
        ("quality", "mean"): stats["quality_mean"],
    }
    return mapping.get((panel_id, aggregation))


def is_ok(value, operator: str, limit: float) -> bool | None:
    if value is None:
        return None
    return value <= limit if operator == "lte" else value >= limit


def _plot_series(ax, points, values, label, color, *, markers=True, linestyle="-") -> None:
    xs = [t for t, v in zip(points, values) if v is not None]
    ys = [v for v in values if v is not None]
    ax.plot(
        xs,
        ys,
        color=color,
        linewidth=1.6,
        linestyle=linestyle,
        marker="o" if markers else None,
        markersize=4.5,
        label=label,
    )


def _threshold_line(ax, value: float, label: str) -> None:
    ax.axhline(value, color=STATUS_CRITICAL, linewidth=1.3, linestyle=(0, (5, 3)), zorder=1)
    ax.annotate(
        label,
        xy=(0, value),
        xycoords=("axes fraction", "data"),
        xytext=(4, 3),
        textcoords="offset points",
        ha="left",
        va="bottom",
        fontsize=8,
        color=STATUS_CRITICAL,
    )


def _style_axis(ax, start: datetime, end: datetime, unit: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_xlim(start.astimezone(DISPLAY_TZ), end.astimezone(DISPLAY_TZ))
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, 10), tz=DISPLAY_TZ))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=DISPLAY_TZ))
    ax.set_ylabel(unit, color=TEXT_SECONDARY, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def _fmt(value, pattern: str = "{:.0f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def render(config: dict, events: list[dict], output: Path) -> dict:
    dashboard = config["dashboard"]
    window = timedelta(minutes=dashboard["time_range_minutes"])
    end = max((e["_ts"] for e in events), default=datetime.now(timezone.utc))
    start = end - window
    in_window = [e for e in events if start <= e["_ts"] <= end]
    stats = window_stats(in_window)
    minutes = per_minute(in_window, start, end)
    times = [t.astimezone(DISPLAY_TZ) for t, _ in minutes]
    requests = [e for e in in_window if e.get("event") == "request_received"]
    active_minutes = (
        max(1.0, (end - min(e["_ts"] for e in requests)).total_seconds() / 60) if requests else 0
    )

    fig, axes = plt.subplots(2, 3, figsize=(19, 10.5), facecolor=SURFACE)
    fig.subplots_adjust(left=0.05, right=0.985, top=0.86, bottom=0.07, hspace=0.55, wspace=0.22)
    fig.text(0.05, 0.955, dashboard["title"], fontsize=17, weight="bold", color=TEXT_PRIMARY)
    fig.text(
        0.05,
        0.918,
        f"Time range: last {dashboard['time_range_minutes']} min  "
        f"({start.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M} → {end.astimezone(DISPLAY_TZ):%H:%M} UTC+7)  ·  "
        f"refresh {dashboard['refresh_seconds']}s  ·  source: {dashboard['panels'][0]['source']}  ·  "
        f"{len(in_window)} events, {stats['requests']} requests  ·  bucket 1 min",
        fontsize=10,
        color=TEXT_SECONDARY,
    )

    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    order = ["latency", "traffic", "errors", "cost", "tokens", "quality"]
    status: dict[str, dict] = {}

    for ax, panel_id in zip(axes.flat, order):
        panel = panels[panel_id]
        threshold = panel["threshold"]
        op_symbol = "≤" if threshold["operator"] == "lte" else "≥"
        current = threshold_value(stats, panel_id, threshold["aggregation"], active_minutes)
        ok = is_ok(current, threshold["operator"], threshold["value"])
        status[panel_id] = {**threshold, "current": current, "ok": ok}
        _style_axis(ax, start, end, panel["unit"])
        series = [s for _, s in minutes]

        if panel_id == "latency":
            for i, (key, label) in enumerate(
                [("p50", "P50"), ("p95", "P95"), ("p99", "P99"), ("ttft_p95", "TTFT P95")]
            ):
                # P99 nét đứt để không che P95 khi hai giá trị trùng nhau.
                style = (0, (2, 2)) if key == "p99" else "-"
                _plot_series(ax, times, [s[key] for s in series], label, SERIES[i], linestyle=style)
            summary = (
                f"P50 {_fmt(stats['p50'])} · P95 {_fmt(stats['p95'])} · "
                f"P99 {_fmt(stats['p99'])} · TTFT P95 {_fmt(stats['ttft_p95'])} ms"
            )
        elif panel_id == "traffic":
            counts = [s["requests"] for s in series]
            ax.bar(times, counts, width=timedelta(seconds=45), color=SERIES[0], label="requests/min")
            summary = (
                f"{stats['requests']} requests · avg {_fmt(current, '{:.1f}')}/min "
                f"over {active_minutes:.0f} active min"
            )
        elif panel_id == "errors":
            _plot_series(ax, times, [s["error_rate_pct"] for s in series], "error rate %", SERIES[1])
            _plot_series(
                ax, times, [s["retrieval_success_pct"] for s in series], "retrieval success %", SERIES[2]
            )
            ax.set_ylim(-5, 110)
            breakdown = ", ".join(f"{k}={v}" for k, v in stats["errors_by_type"].items()) or "none"
            summary = (
                f"error rate {_fmt(stats['error_rate_pct'], '{:.1f}')}% · retrieval success "
                f"{_fmt(stats['retrieval_success_pct'], '{:.1f}')}% · errors: {breakdown}"
            )
        elif panel_id == "cost":
            per_min = [s["cost_usd"] for s in series]
            cumulative = [sum(per_min[: i + 1]) for i in range(len(per_min))]
            _plot_series(ax, times, cumulative, "cumulative cost", SERIES[0], markers=False)
            ax.bar(times, per_min, width=timedelta(seconds=45), color=SERIES[1], label="cost / min")
            summary = f"total ${stats['cost_usd']:.4f} · peak ${max(per_min, default=0):.4f}/min"
        elif panel_id == "tokens":
            for i, key in enumerate(["tokens_in", "tokens_out"]):
                per_min = [s[key] for s in series]
                cumulative = [sum(per_min[: j + 1]) for j in range(len(per_min))]
                _plot_series(ax, times, cumulative, f"cumulative {key}", SERIES[i], markers=False)
            summary = f"tokens_in {stats['tokens_in']:,} · tokens_out {stats['tokens_out']:,}"
        else:  # quality
            _plot_series(ax, times, [s["quality_mean"] for s in series], "mean quality", SERIES[0])
            ax.set_ylim(0, 1.05)
            summary = f"mean quality {_fmt(stats['quality_mean'], '{:.3f}')}"

        _threshold_line(
            ax,
            threshold["value"],
            f"threshold {threshold['aggregation']} {op_symbol} {threshold['value']:g} {panel['unit']}",
        )
        if panel_id not in ("errors", "quality"):
            ax.set_ylim(bottom=0, top=max(ax.get_ylim()[1], threshold["value"] * 1.15))

        ax.set_title(panel["title"], loc="left", fontsize=12, weight="bold", color=TEXT_PRIMARY, pad=24)
        verdict = {True: "OK", False: "BREACH", None: "NO DATA"}[ok]
        verdict_color = {True: STATUS_GOOD, False: STATUS_CRITICAL, None: TEXT_SECONDARY}[ok]
        ax.text(0, 1.03, summary, transform=ax.transAxes, fontsize=8.5, color=TEXT_SECONDARY)
        ax.text(
            1,
            1.105,
            f"● {verdict}",
            transform=ax.transAxes,
            ha="right",
            fontsize=10,
            weight="bold",
            color=verdict_color,
        )
        ax.legend(loc="center left", fontsize=8, frameon=False, labelcolor=TEXT_SECONDARY)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=110, facecolor=SURFACE)
    plt.close(fig)
    return {"window_start": start, "window_end": end, "stats": stats, "thresholds": status}


def print_summary(result: dict, output: Path) -> None:
    stats = result["stats"]
    print(f"Dashboard: {output}")
    print(
        f"Window (UTC): {result['window_start']:%Y-%m-%d %H:%M:%S} → {result['window_end']:%H:%M:%S}"
    )
    print(
        f"requests={stats['requests']} p50={_fmt(stats['p50'])} p95={_fmt(stats['p95'])} "
        f"p99={_fmt(stats['p99'])} ttft_p95={_fmt(stats['ttft_p95'])} ms"
    )
    print(
        f"error_rate={_fmt(stats['error_rate_pct'], '{:.1f}')}% errors={stats['errors_by_type']} "
        f"retrieval_success={_fmt(stats['retrieval_success_pct'], '{:.1f}')}%"
    )
    print(
        f"cost_total=${stats['cost_usd']:.4f} tokens_in={stats['tokens_in']} "
        f"tokens_out={stats['tokens_out']} quality_mean={_fmt(stats['quality_mean'], '{:.3f}')}"
    )
    for panel_id, item in result["thresholds"].items():
        verdict = {True: "OK", False: "BREACH", None: "NO DATA"}[item["ok"]]
        print(
            f"  [{verdict}] {panel_id}: {item['aggregation']}={_fmt(item['current'], '{:.4g}')} "
            f"{item['operator']} {item['value']}"
        )


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "dashboard.yaml")
    parser.add_argument("--logs", type=Path, default=REPO_ROOT / "data" / "logs.jsonl")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data" / "dashboard.png")
    parser.add_argument("--watch", action="store_true", help="Render lại theo refresh_seconds")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    while True:
        if not args.logs.exists():
            print(f"Không tìm thấy {args.logs}; chạy API và load test trước.")
            return 1
        result = render(config, load_events(args.logs), args.output)
        print_summary(result, args.output)
        if not args.watch:
            return 0
        time.sleep(config["dashboard"]["refresh_seconds"])


if __name__ == "__main__":
    raise SystemExit(main())
