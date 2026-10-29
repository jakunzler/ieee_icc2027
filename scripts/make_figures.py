#!/usr/bin/env python3
"""Figures that can be rebuilt from this bundle, with optional lab IQ.

Without IQ, this script redraws the published PSS-median bar chart and the
run-eligibility bars from campaign/e1e2_batch1/comparison_summary.json.
The 20 ms spectrogram and per-event traces need the laboratory .c64 files
and events.json (see DATA.md).
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
CAMPAIGN = BUNDLE / "campaign" / "e1e2_batch1"
FIGURES = BUNDLE / "figures"
SUMMARY = CAMPAIGN / "comparison_summary.json"
LEDGER = CAMPAIGN / "ledger.json"

FS = 184_320_000.0
FC = 3_750_000_000.0
SSB_HZ = 3_709_920_000.0
PERIOD = 1_843_200
NFFT = 4096
HOP = 1024
SLICE_START = 1_700_000
SLICE_LEN = 3_686_400  # 20 ms


def _save(fig: plt.Figure, stem: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / f"{stem}.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")
    boxes = [
        (0.25, 1.15, 2.7, 3.5, "WS02\nOpen5GS + srsRAN gNB\nX310 192.168.40.3"),
        (4.15, 3.35, 2.7, 1.45, "WS03 watchdog\nX310 RX2, receive only\n192.168.40.2"),
        (4.15, 0.85, 2.7, 1.45, "WS03 Telit UE\nwwan0\nnot an IQ source"),
        (7.35, 3.35, 2.4, 1.45, "NVMe original\n+ Seagate copy"),
    ]
    for x, y, w, h, text in boxes:
        ax.add_patch(
            plt.Rectangle((x, y), w, h, fill=False, linewidth=1.0, edgecolor="black")
        )
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=8)
    ax.annotate("", xy=(4.15, 4.05), xytext=(2.95, 4.05), arrowprops=dict(arrowstyle="->", color="black"))
    ax.text(3.55, 4.3, "OTA", ha="center", fontsize=8)
    ax.annotate("", xy=(4.15, 1.55), xytext=(2.95, 1.55), arrowprops=dict(arrowstyle="->", color="black"))
    ax.text(3.55, 1.8, "OTA", ha="center", fontsize=8)
    ax.annotate("", xy=(7.35, 4.05), xytext=(6.85, 4.05), arrowprops=dict(arrowstyle="->", color="black"))
    ax.text(7.1, 4.3, "IQ", ha="center", fontsize=8)
    ax.set_title("Documented campaign allocation, 2026-10-26. The X410 was not used.", fontsize=9)
    _save(fig, "fig_architecture")


def pss_median_from_summary(summary: list[dict]) -> None:
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
    _save(fig, "fig_pss_median_by_run")


def eligibility(summary: list[dict]) -> None:
    labels = [f"A{item['attempt']}" for item in summary]
    e1 = [1 for _ in summary]
    e2 = [1 if item["e2"] == "PASS" else 0 for item in summary]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = np.arange(len(labels))
    ax.bar(x - 0.15, e1, width=0.3, color="0.35", label="E1 integrity")
    ax.bar(x + 0.15, e2, width=0.3, color="0.75", edgecolor="black", label="E2 reference eligibility")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, 1.25)
    ax.set_ylabel("Pass (1) or fail (0)")
    ax.set_xlabel("Acquisition, in campaign order")
    ax.set_title("Run-level gates. n=5. Failed E2 attempts stay in the denominator.", fontsize=9)
    ax.legend(frameon=False, fontsize=8)
    _save(fig, "fig_run_eligibility")


def resolve_iq(row: dict, iq_root: Path | None) -> Path | None:
    if iq_root is None:
        return None
    name = row.get("filename")
    rid = row["run_id"]
    candidates = []
    if name:
        candidates.append(iq_root / name)
        candidates.append(iq_root / rid / name)
    candidates.append(iq_root / f"{rid}.c64")
    candidates.append(iq_root / rid / f"{rid}.c64")
    candidates.extend(sorted(iq_root.glob(f"*{rid}*.c64")))
    candidates.extend(sorted(iq_root.glob(f"{rid}/*{rid}*.c64")))
    for path in candidates:
        if path.is_file():
            return path
    return None


def resolve_events(row: dict, events_root: Path | None) -> Path | None:
    candidates = [CAMPAIGN / row["run_id"] / "events.json"]
    if events_root:
        candidates.insert(0, events_root / row["run_id"] / "events.json")
    for path in candidates:
        if path.is_file():
            return path
    return None


def spectrogram(row: dict, iq: Path, events_path: Path) -> dict:
    if iq.stat().st_size != 2_949_120_000:
        raise SystemExit(f"unexpected IQ size {iq.stat().st_size}")
    mm = np.memmap(iq, dtype=np.complex64, mode="r", offset=SLICE_START * 8, shape=(SLICE_LEN,))
    x = np.array(mm, dtype=np.complex64, copy=True)
    del mm
    nframes = 1 + (SLICE_LEN - NFFT) // HOP
    window = np.hanning(NFFT).astype(np.float64)
    scale = FS * float(np.sum(window**2))
    spec = np.empty((nframes, NFFT), dtype=np.float64)
    for i in range(nframes):
        start = i * HOP
        frame = x[start : start + NFFT] * window
        spec[i] = np.abs(np.fft.fftshift(np.fft.fft(frame))) ** 2 / scale
    ref = float(np.median(spec))
    db = 10.0 * np.log10(spec / ref)
    freqs = FC + np.fft.fftshift(np.fft.fftfreq(NFFT, d=1.0 / FS))
    times_ms = (SLICE_START + np.arange(nframes) * HOP + NFFT / 2) / FS * 1e3
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    image = ax.imshow(
        db.T,
        origin="lower",
        aspect="auto",
        extent=[times_ms[0], times_ms[-1], freqs[0] / 1e6, freqs[-1] / 1e6],
        cmap="gray_r",
        vmin=np.percentile(db, 5),
        vmax=np.percentile(db, 99),
    )
    ax.axhline(SSB_HZ / 1e6, color="black", linestyle="--", linewidth=0.8)
    ax.text(
        times_ms[0] + 0.4,
        SSB_HZ / 1e6 + 8,
        "Configured SSB center 3709.92 MHz\n(not estimated from this slice)",
        fontsize=7,
        color="black",
    )
    events = json.loads(events_path.read_text())
    for ev in events:
        sample = ev["absolute_pss_useful"]
        if SLICE_START <= sample < SLICE_START + SLICE_LEN:
            ax.axvline(sample / FS * 1e3, color="black", linewidth=0.6)
    cbar = fig.colorbar(image, ax=ax, pad=0.02)
    cbar.set_label("Relative level (dB above the slice median)")
    ax.set_xlabel("Time from file start (ms)")
    ax.set_ylabel("Frequency (MHz)")
    ax.set_title(
        f"Attempt {row['attempt']}, run {row['run_id'][:8]}, 20 ms slice. Hann, N=4096, hop=1024. Not dBm.",
        fontsize=9,
    )
    _save(fig, "fig_spectrogram_attempt1_20ms")
    return {
        "iq": str(iq),
        "iq_bytes": iq.stat().st_size,
        "slice_start_sample": SLICE_START,
        "slice_len_samples": SLICE_LEN,
        "nfft": NFFT,
        "hop": HOP,
        "window": "hann",
        "reference": "median power of this slice",
        "ssb_hz_configured": SSB_HZ,
        "center_hz": FC,
        "fs_hz": FS,
    }


def spacing_from_events(rows: list[dict], events_root: Path | None) -> None:
    fig, ax = plt.subplots(figsize=(3.45, 2.15))
    plotted = 0
    for row in rows:
        path = resolve_events(row, events_root)
        if path is None:
            continue
        evs = json.loads(path.read_text())
        samples = np.array([ev["absolute_pss_useful"] for ev in evs], dtype=np.int64)
        delta = np.diff(samples) - PERIOD
        ax.plot(np.arange(1, len(delta) + 1), delta, linewidth=1.0, label=f"A{row['attempt']}")
        plotted += 1
    if plotted == 0:
        plt.close(fig)
        return
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_ylim(-1.6, 1.6)
    ax.set_ylabel("Samples from 1,843,200", fontsize=8)
    ax.set_xlabel("Interval index within the run", fontsize=8)
    ax.tick_params(labelsize=8)
    ax.legend(frameon=False, fontsize=7, ncol=5, loc="upper center", bbox_to_anchor=(0.5, 1.22))
    fig.tight_layout()
    _save(fig, "fig_periodicity_by_run")


def main() -> None:
    summary = json.loads(SUMMARY.read_text())
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else summary
    iq_root = Path(os.environ["IQ_ROOT"]) if os.environ.get("IQ_ROOT") else None
    events_root = Path(os.environ["EVENTS_ROOT"]) if os.environ.get("EVENTS_ROOT") else None

    architecture()
    pss_median_from_summary(summary)
    eligibility(summary)
    spacing_from_events(ledger, events_root)

    first = next((row for row in ledger if row["attempt"] == 1), None)
    if first is not None:
        iq = resolve_iq(first, iq_root)
        events = resolve_events(first, events_root)
        if iq is not None and events is not None:
            spec = spectrogram(first, iq, events)
            (CAMPAIGN / "spectrogram_parameters.json").write_text(json.dumps(spec, indent=2) + "\n")
        else:
            print(
                "Skipping 20 ms spectrogram: IQ/events for attempt 1 are not in this bundle.",
                file=sys.stderr,
            )
    print("WROTE", FIGURES)


if __name__ == "__main__":
    main()
