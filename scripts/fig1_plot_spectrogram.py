#!/usr/bin/env python3
"""Gera a Fig. 1 do artigo (espectrograma longo, eixos GHz × s).

Separado de ``01_build_spectrograms.py`` (matrizes 100×1024 para ML).

Nome padrão da figura (alinhado ao stamp do IQ):
  ``YYYYMMDD_HHMMSS-fig1_<dur>s_<bw>MHz.png``
ex.: ``20260715_110944-fig1_20s_120MHz.png`` (+ ``.json`` sidecar).

Uso típico:

  /usr/bin/python3 -m ref_repro.capture.watchdog_capture \\
      --case A --seconds 20 --fs 200e6 --fc 3.750e9 --gain 20 \\
      --addr 192.168.40.2 --out data/raw/fig1

  source .venv/bin/activate
  python scripts/fig1_plot_spectrogram.py --iq 'data/raw/fig1/casoA_*.c64'
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ref_repro.config import ensure_dir, load_config
from ref_repro.dsp.iq_io import load_metadata, resolve_fs
from ref_repro.viz.fig1 import render_fig1_from_iq


def _resolve_iq(pattern: str) -> Path:
    matches = sorted(glob.glob(pattern))
    if not matches:
        p = Path(pattern)
        if p.is_file():
            return p
        raise FileNotFoundError(f"Nenhum IQ em: {pattern}")
    return Path(matches[-1])  # mais recente se glob


def capture_stamp_from_iq(iq_path: Path, meta: dict | None = None) -> str:
    """Extrai ``YYYYMMDD_HHMMSS`` do nome do IQ (ou do JSON / mtime)."""
    m = re.search(r"(20\d{6}_\d{6})", iq_path.stem)
    if m:
        return m.group(1)
    if meta and meta.get("stamp"):
        return str(meta["stamp"])
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(iq_path.stat().st_mtime))


def default_fig1_basename(
    iq_path: Path,
    *,
    duration_s: float,
    f_lim_ghz: tuple[float, float],
    meta: dict | None = None,
) -> str:
    """``20260715_110944-fig1_20s_120MHz`` (sem extensão)."""
    stamp = capture_stamp_from_iq(iq_path, meta)
    dur = int(round(duration_s))
    bw_mhz = int(round((f_lim_ghz[1] - f_lim_ghz[0]) * 1000))
    return f"{stamp}-fig1_{dur}s_{bw_mhz}MHz"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "configs/default.yaml"))
    ap.add_argument(
        "--iq",
        required=True,
        help="Arquivo .c64 ou glob (ex.: data/raw/fig1/casoA_*.c64)",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="PNG de saída (default: TIMESTAMP-fig1_<dur>s_<bw>MHz.png)",
    )
    ap.add_argument("--fc", type=float, default=None)
    ap.add_argument("--fs", type=float, default=None, help="Override fs (senão JSON/config)")
    ap.add_argument(
        "--freq-axis",
        choices=["article", "physical"],
        default=None,
        help="article=3.69–3.81 GHz; physical=fc±fs/2",
    )
    ap.add_argument("--title", default=None)
    ap.add_argument(
        "--notch-dc",
        dest="notch_dc",
        action="store_true",
        default=None,
        help="Remove bin DC/LO no plot (default: fig1.notch_dc no YAML)",
    )
    ap.add_argument(
        "--no-notch-dc",
        dest="notch_dc",
        action="store_false",
        help="Mantém bin DC/LO no plot",
    )
    ap.add_argument(
        "--clim",
        nargs=2,
        type=float,
        metavar=("VMIN", "VMAX"),
        default=None,
        help="Escala absoluta log10(PSD), ex.: --clim -7.5 0 (override YAML)",
    )
    ap.add_argument(
        "--percentiles",
        action="store_true",
        help="Ignora clim fixo; usa fig1.clim_percentiles por figura",
    )
    args = ap.parse_args()

    cfg = load_config(args.config)
    fig1 = cfg.get("fig1", {})
    radio = cfg["radio"]

    iq_path = _resolve_iq(args.iq)
    meta = load_metadata(iq_path)
    fs = args.fs if args.fs is not None else resolve_fs(meta, radio["fs_hz"])
    fc = args.fc if args.fc is not None else float((meta or {}).get("fc", radio["fc_hz"]))

    f_lim = fig1.get("f_lim_ghz", [3.69, 3.81])
    t_lim = fig1.get("t_lim_s", [0.0, 20.0])
    freq_axis = args.freq_axis or fig1.get("freq_axis", "article")
    clim_pct = fig1.get("clim_percentiles", [5, 99])
    if args.percentiles:
        clim = None
    elif args.clim is not None:
        clim = (float(args.clim[0]), float(args.clim[1]))
    else:
        raw_clim = fig1.get("clim", [-7.5, 0.0])
        clim = None if raw_clim is None else (float(raw_clim[0]), float(raw_clim[1]))
    if args.notch_dc is None:
        notch_dc = bool(fig1.get("notch_dc", True))
    else:
        notch_dc = bool(args.notch_dc)

    duration_s = float(
        (meta or {}).get("duration_s", (meta or {}).get("duration_rf_s", t_lim[1]))
    )
    out_dir = ensure_dir(cfg["paths"].get("figures_fig1", cfg["paths"]["figures"] + "/fig1"))
    if args.out:
        out_png = Path(args.out)
    else:
        base = default_fig1_basename(
            iq_path,
            duration_s=duration_s,
            f_lim_ghz=(float(f_lim[0]), float(f_lim[1])),
            meta=meta,
        )
        out_png = out_dir / f"{base}.png"

    stamp = capture_stamp_from_iq(iq_path, meta)
    default_title = (
        f"Fig. 1 ({stamp}, {int(round(duration_s))} s, "
        f"{int(round((f_lim[1] - f_lim[0]) * 1000))} MHz span)"
    )

    info = render_fig1_from_iq(
        iq_path,
        out_png,
        fc_hz=fc,
        fs_hz=fs,
        n_fft=int(fig1.get("n_fft", 1024)),
        n_time_bins=int(fig1.get("n_time_bins", 800)),
        f_lim_ghz=(float(f_lim[0]), float(f_lim[1])),
        t_lim_s=(float(t_lim[0]), float(t_lim[1])),
        freq_axis=freq_axis,
        cmap=fig1.get("cmap", "turbo"),
        dpi=int(fig1.get("dpi", cfg.get("viz", {}).get("dpi", 200))),
        title=args.title or fig1.get("title") or default_title,
        scale_mode=fig1.get("scale_mode", "log10"),
        clim=clim,
        clim_percentiles=(float(clim_pct[0]), float(clim_pct[1])),
        notch_dc=notch_dc,
    )

    info["stamp"] = stamp
    info["figure_basename"] = out_png.stem

    meta_out = Path(str(out_png) + ".json")
    meta_out.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"[OK] Fig.1 → {out_png}")
    print(f"     sidecar → {meta_out}")
    print(
        f"     clim={info.get('clim')} percentiles={info.get('clim_percentiles')}"
    )
    print(
        f"     duration={info['duration_s']:.2f}s fs={info['fs_hz']/1e6:.3f} MHz "
        f"span={info['f_span_ghz'][0]:.4f}–{info['f_span_ghz'][1]:.4f} GHz"
    )
    span = info["f_span_ghz"][1] - info["f_span_ghz"][0]
    lim_span = float(f_lim[1]) - float(f_lim[0])
    if freq_axis == "article" and span + 1e-6 < 0.85 * lim_span:
        print(
            "[WARN] Captura com BW < ~120 MHz: eixo 3.69–3.81 GHz estica/corta a visão. "
            "Para reproduzir o artigo, recapture com --fs 200e6."
        )


if __name__ == "__main__":
    main()
