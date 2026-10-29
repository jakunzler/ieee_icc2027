#!/usr/bin/env python3
"""Offline comparison of the five E1-valid recordings.

Uses saved frozen-detector events and read-only IQ. Does not retune
thresholds, modify IQ, or map host UTC onto sample indices.

IQ and events.json live on the laboratory NAS (see DATA.md). Point this
script at them with IQ_ROOT and EVENTS_ROOT. Without those files the
published comparison_summary.json is left unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
import matplotlib.pyplot as plt
import numpy as np

BUNDLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE / "src"))
from ref_repro.dsp.nr_ssb import pss_sequence  # noqa: E402

CAMPAIGN = BUNDLE / "campaign" / "e1e2_batch1"
FIGURES = BUNDLE / "figures"
LEDGER = CAMPAIGN / "ledger.json"
FS = 184_320_000.0
OFFSET = -40_080_000.0
NFFT = 6144
PERIOD = 1_843_200
EXPECTED = {
    1: "3e1041d51eb4b321a96b06bc7f0be4168767cc41e85eda5c7bc1182bfc18682c",
    2: "3ef9c00927d47cce348882129f49b87e5cf3993ad20578475d2bca6cb83d5296",
    3: "e8ab967c10f2eb2296cdc0a5d172a955e3acbe36d4a59e65d53b6fc32fc095af",
    4: "f7a27348f57f39748d9482b703d7614f356690895acc24b0d637ba206194c90a",
    5: "c8909fbeac51c3b5cabd96e735a0c7f30df1029f6636332838d8b958f43d339e",
}


def pss_score_at(segment: np.ndarray, local_start: int, nid2: int) -> float:
    n = np.arange(len(segment))
    x = segment.astype(np.complex128) * np.exp(-2j * np.pi * OFFSET * n / FS)
    grid = np.zeros(NFFT, dtype=np.complex128)
    grid[np.arange(-64, 63) % NFFT] = pss_sequence(nid2)
    template = np.fft.ifft(grid)
    chunk = x[local_start : local_start + NFFT]
    denom = np.sqrt(np.sum(np.abs(chunk) ** 2) * np.vdot(template, template).real)
    if denom == 0:
        return 0.0
    return float(np.abs(np.vdot(template, chunk)) / denom)


def file_mean_power(path: Path) -> float:
    mm = np.memmap(path, dtype=np.complex64, mode="r")
    total = 0.0
    count = 0
    step = 1 << 20
    for start in range(0, len(mm), step):
        chunk = np.asarray(mm[start : start + step])
        total += float(np.sum(np.abs(chunk) ** 2))
        count += len(chunk)
    return total / count


def symbol_power(mm: np.memmap, useful: int) -> float:
    chunk = np.asarray(mm[useful : useful + NFFT])
    return float(np.mean(np.abs(chunk) ** 2))


def resolve_iq(row: dict, iq_root: Path | None) -> Path | None:
    candidates = []
    raw = row.get("iq")
    if isinstance(raw, str) and raw not in ("", "NOT_IN_THIS_BUNDLE"):
        candidates.append(Path(raw))
    name = row.get("filename")
    if iq_root and name:
        candidates.append(iq_root / name)
        candidates.append(iq_root / row["run_id"] / name)
    if iq_root:
        rid = row["run_id"]
        candidates.append(iq_root / f"{rid}.c64")
        candidates.append(iq_root / rid / f"{rid}.c64")
        candidates.extend(sorted(iq_root.glob(f"*{rid}*.c64")))
        candidates.extend(sorted(iq_root.glob(f"{rid}/*{rid}*.c64")))
    for path in candidates:
        if path.is_file():
            return path
    return None


def resolve_events(row: dict, events_root: Path | None) -> Path | None:
    candidates = []
    raw = row.get("events_json")
    if isinstance(raw, str) and raw not in ("", "NOT_IN_THIS_BUNDLE"):
        candidates.append(Path(raw))
    if events_root:
        candidates.append(events_root / row["run_id"] / "events.json")
    local = CAMPAIGN / row["run_id"] / "events.json"
    candidates.append(local)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _figures(series: list[dict], summary: list[dict]) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(3, 1, figsize=(7.2, 7.2), sharex=True)
    for item in series:
        style = "-" if item["e2"] == "PASS" else "--"
        label = f"Attempt {item['attempt']} ({item['e2']})"
        axes[0].plot(item["t_ms"], item["pss"], style, linewidth=0.8, label=label)
        axes[1].plot(item["t_ms"], item["sss"], style, linewidth=0.8)
        axes[2].plot(item["t_ms"], item["power"], style, linewidth=0.8)
    axes[0].axhline(0.7, color="black", linewidth=0.5)
    axes[0].set_ylabel("PSS score")
    axes[1].set_ylabel("SSS score")
    axes[2].set_ylabel("PSS-symbol mean power")
    axes[2].set_xlabel("Time from file start (ms)")
    axes[0].legend(
        fontsize=7,
        ncol=5,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
    )
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_scores_and_power.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_scores_and_power.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    labels = [f"A{item['attempt']}" for item in summary]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    med = [item["pss_median"] for item in summary]
    lo = [item["pss_median"] - item["pss_min"] for item in summary]
    hi = [item["pss_max"] - item["pss_median"] for item in summary]
    colors = ["0.35" if item["e2"] == "PASS" else "0.75" for item in summary]
    ax.bar(x, med, color=colors, edgecolor="black", yerr=np.vstack([lo, hi]), capsize=3)
    ax.axhline(0.7, color="black", linewidth=0.6)
    ax.set_xticks(x, labels)
    ax.set_ylabel("PSS score")
    ax.set_xlabel("Acquisition")
    ax.set_ylim(0.65, 1.0)
    ax.set_title("Run-level PSS median, with min and max. Eligible runs are darker.", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGURES / "fig_pss_median_by_run.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_pss_median_by_run.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    iq_root = Path(os.environ["IQ_ROOT"]) if os.environ.get("IQ_ROOT") else None
    events_root = Path(os.environ["EVENTS_ROOT"]) if os.environ.get("EVENTS_ROOT") else None
    rows = json.loads(LEDGER.read_text())
    missing = []
    summary = []
    series = []
    for row in rows:
        attempt = row["attempt"]
        if row["sha256"] != EXPECTED[attempt]:
            raise SystemExit(f"ledger hash mismatch {attempt}")
        iq = resolve_iq(row, iq_root)
        events_path = resolve_events(row, events_root)
        if iq is None or events_path is None:
            missing.append(row["run_id"])
            continue
        if iq.stat().st_size != 2_949_120_000:
            raise SystemExit(f"size mismatch {iq}")
        events = json.loads(events_path.read_text())
        mm = np.memmap(iq, dtype=np.complex64, mode="r")
        first = events[0]
        local = first["absolute_pss_useful"] - first["window_start"]
        segment = np.asarray(mm[first["window_start"] : first["window_start"] + 2_000_000])
        check = pss_score_at(segment, local, first["nid2"])
        if abs(check - first["pss_score"]) > 1e-6:
            raise SystemExit(f"score check failed attempt {attempt}: {check} vs {first['pss_score']}")
        samples = np.array([ev["absolute_pss_useful"] for ev in events], dtype=np.int64)
        delta = np.diff(samples) - PERIOD
        pss = np.array([ev["pss_score"] for ev in events])
        sss = np.array([ev["sss_score"] for ev in events])
        power = np.array([symbol_power(mm, int(s)) for s in samples])
        predicted = None
        predicted_score = None
        if samples[0] >= PERIOD:
            predicted = int(samples[0] - PERIOD)
            pred_segment = np.asarray(mm[0:2_000_000])
            predicted_score = pss_score_at(pred_segment, predicted, int(first["nid2"]))
        item = {
            "attempt": attempt,
            "e1": row["e1"],
            "e2": row["e2"],
            "run_id": row["run_id"],
            "sha256": row["sha256"],
            "accepted": len(events),
            "pci": sorted({ev["pci"] for ev in events}),
            "pss_min": float(pss.min()),
            "pss_median": float(np.median(pss)),
            "pss_max": float(pss.max()),
            "sss_min": float(sss.min()),
            "sss_median": float(np.median(sss)),
            "sss_max": float(sss.max()),
            "spacing_exact": int(np.sum(delta == 0)),
            "spacing_plus": int(np.sum(delta == 1)),
            "spacing_minus": int(np.sum(delta == -1)),
            "spacing_other": int(np.sum(np.abs(delta) > 1)),
            "symbol_power_median": float(np.median(power)),
            "symbol_power_min": float(power.min()),
            "symbol_power_max": float(power.max()),
            "file_mean_power": file_mean_power(iq),
            "predicted_opening_useful": predicted,
            "predicted_opening_pss_score": predicted_score,
            "score_check_abs_error": abs(check - first["pss_score"]),
        }
        summary.append(item)
        series.append(
            {
                "attempt": attempt,
                "e2": row["e2"],
                "t_ms": samples / FS * 1e3,
                "pss": pss,
                "sss": sss,
                "power": power,
                "delta": delta,
            }
        )
        print(json.dumps(item), flush=True)

    if missing:
        published = CAMPAIGN / "comparison_summary.json"
        print(
            "IQ/events not in this bundle for:",
            ", ".join(missing),
            file=sys.stderr,
        )
        print(
            "Set IQ_ROOT and EVENTS_ROOT to the laboratory copies. "
            f"Published numbers remain in {published}.",
            file=sys.stderr,
        )
        if not summary:
            raise SystemExit(2)

    (CAMPAIGN / "comparison_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    _figures(series, summary)
    print("WROTE", CAMPAIGN, "and", FIGURES)


if __name__ == "__main__":
    main()
