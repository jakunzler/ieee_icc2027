"""Reprodução visual da Fig. 1 do artigo (Varotto et al.).

Este módulo NÃO gera features de ML. As matrizes 100×1024 para treino
continuam em ``ref_repro.dsp.spectrogram`` / ``scripts/01_build_spectrograms.py``.

Fig. 1: espectrograma de longa duração com eixos físicos
  - X: frequência [GHz] (artigo: 3.69–3.81 ≈ fc ± 60 MHz)
  - Y: tempo [s] (artigo: 0–20)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def memmap_iq(path: Path | str) -> np.memmap:
    return np.memmap(path, dtype=np.complex64, mode="r")


def compute_display_hop(n_samples: int, n_fft: int, n_time_bins: int) -> int:
    """Hop para cobrir o IQ com ``n_time_bins`` linhas de exibição."""
    if n_time_bins < 2:
        return max(1, n_samples - n_fft)
    max_start = max(1, n_samples - n_fft)
    return max(1, max_start // (n_time_bins - 1))


def stft_power(
    iq: np.ndarray,
    *,
    n_fft: int = 1024,
    hop: int = 1024,
) -> np.ndarray:
    """PSD linear shape (n_time, n_fft) — apenas FFTs nos instantes de hop."""
    n = len(iq)
    if n < n_fft:
        raise ValueError(f"IQ curto demais: {n} < n_fft={n_fft}")
    n_rows = 1 + (n - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    S = np.empty((n_rows, n_fft), dtype=np.float32)
    for i in range(n_rows):
        start = i * hop
        seg = np.asarray(iq[start : start + n_fft], dtype=np.complex64) * win
        X = np.fft.fft(seg, n=n_fft)
        S[i] = (np.abs(X) ** 2) / n_fft
    return S


def fftshift_rows(S: np.ndarray) -> np.ndarray:
    return np.fft.fftshift(S, axes=1)


def to_display_scale(S: np.ndarray, *, eps: float = 1e-21, mode: str = "log10") -> np.ndarray:
    """Escala visual da Fig. 1 (independente de f(x)=-log do pipeline ML)."""
    S = np.asarray(S, dtype=np.float64)
    if mode == "db":
        return (10.0 * np.log10(S + eps)).astype(np.float32)
    if mode == "neglog":
        return (-np.log(S + eps)).astype(np.float32)
    return np.log10(S + eps).astype(np.float32)


def frequency_axis_ghz(fc_hz: float, fs_hz: float, n_fft: int) -> np.ndarray:
    freqs = np.fft.fftshift(np.fft.fftfreq(n_fft, d=1.0 / fs_hz))
    return (fc_hz + freqs) / 1e9


def time_axis_s(n_rows: int, hop: int, n_fft: int, fs_hz: float) -> np.ndarray:
    return (np.arange(n_rows) * hop + n_fft / 2.0) / fs_hz


def save_fig1_spectrogram(
    S_display: np.ndarray,
    path: str | Path,
    *,
    f_ghz: np.ndarray,
    t_s: np.ndarray,
    f_lim_ghz: tuple[float, float] | None = (3.69, 3.81),
    t_lim_s: tuple[float, float] | None = (0.0, 20.0),
    cmap: str = "turbo",
    dpi: int = 200,
    title: str | None = None,
    colorbar_label: str = r"$\log_{10}(\mathrm{PSD})$",
    clim: tuple[float, float] | None = None,
    clim_percentiles: tuple[float, float] | None = (5.0, 99.0),
) -> None:
    """PNG estilo Fig. 1: X = Frequency [GHz], Y = Time [s].

    A escala de cor usa percentis (default p5–p99) no recorte visível, para o
    piso de ruído mapear para tons frios e picos (DC/sinais) para tons quentes.
    Sem isso, ``imshow`` autoscale min→max e o piso fica “quente”.
    """
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Estatística de clim só no recorte que o usuário vê (xlim)
    Z = np.asarray(S_display, dtype=np.float64)
    if f_lim_ghz is not None:
        m = (f_ghz >= f_lim_ghz[0]) & (f_ghz <= f_lim_ghz[1])
        Z_stats = Z[:, m] if np.any(m) else Z
    else:
        Z_stats = Z

    if clim is not None:
        vmin, vmax = float(clim[0]), float(clim[1])
    elif clim_percentiles is not None:
        lo, hi = clim_percentiles
        vmin = float(np.percentile(Z_stats, lo))
        vmax = float(np.percentile(Z_stats, hi))
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
            vmin, vmax = float(np.min(Z_stats)), float(np.max(Z_stats))
    else:
        vmin, vmax = float(np.min(Z_stats)), float(np.max(Z_stats))

    extent = [float(f_ghz[0]), float(f_ghz[-1]), float(t_s[0]), float(t_s[-1])]
    fig, ax = plt.subplots(figsize=(8, 6), dpi=dpi)
    im = ax.imshow(
        S_display,
        aspect="auto",
        origin="lower",
        cmap=cmap,
        interpolation="nearest",
        extent=extent,
        vmin=vmin,
        vmax=vmax,
    )
    ax.set_xlabel("Frequency [GHz]")
    ax.set_ylabel("Time [s]")
    if f_lim_ghz is not None:
        ax.set_xlim(float(f_lim_ghz[0]), float(f_lim_ghz[1]))
    if t_lim_s is not None:
        ax.set_ylim(float(t_lim_s[0]), float(t_lim_s[1]))
    if title:
        ax.set_title(title)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label=colorbar_label)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def render_fig1_from_iq(
    iq_path: str | Path,
    out_png: str | Path,
    *,
    fc_hz: float,
    fs_hz: float,
    n_fft: int = 1024,
    n_time_bins: int = 800,
    f_lim_ghz: tuple[float, float] | None = (3.69, 3.81),
    t_lim_s: tuple[float, float] | None = (0.0, 20.0),
    freq_axis: str = "article",
    cmap: str = "turbo",
    dpi: int = 200,
    title: str | None = None,
    scale_mode: str = "log10",
    clim: tuple[float, float] | None = (-7.5, 0.0),
    clim_percentiles: tuple[float, float] | None = (5.0, 99.0),
    notch_dc: bool = True,
) -> dict:
    """IQ → PNG Fig. 1 (não escreve .npy de treino).

    Parameters
    ----------
    freq_axis :
        ``article`` — xlim fixo em ``f_lim_ghz`` (3.69–3.81 como no paper);
        ``physical`` — xlim = fc ± fs/2 da captura.
    clim :
        Limites absolutos de cor ``(vmin, vmax)`` na escala de exibição
        (ex.: ``(-7.5, 0.0)`` em ``log10``). Se definido, ignora percentis.
    clim_percentiles :
        Limites de cor por percentil no recorte visível (evita piso “quente”).
    notch_dc :
        Se True (default), substitui o bin DC (fc) pela mediana local — só visualização.
    """
    iq_path = Path(iq_path)
    iq = memmap_iq(iq_path)
    n = len(iq)
    hop = compute_display_hop(n, n_fft, n_time_bins)
    S = fftshift_rows(stft_power(iq, n_fft=n_fft, hop=hop))
    S_disp = to_display_scale(S, mode=scale_mode)

    if notch_dc:
        mid = S_disp.shape[1] // 2
        # média dos vizinhos (exclui LO/DC leak visual)
        left = S_disp[:, max(0, mid - 3) : mid]
        right = S_disp[:, mid + 1 : mid + 4]
        fill = np.median(np.concatenate([left, right], axis=1), axis=1)
        S_disp = S_disp.copy()
        S_disp[:, mid] = fill

    f_ghz = frequency_axis_ghz(fc_hz, fs_hz, n_fft)
    t_s = time_axis_s(S.shape[0], hop, n_fft, fs_hz)

    if freq_axis == "physical":
        plot_f_lim = (float(f_ghz[0]), float(f_ghz[-1]))
        plot_t_lim = (float(t_s[0]), float(t_s[-1])) if t_lim_s is None else t_lim_s
    else:
        plot_f_lim = f_lim_ghz
        plot_t_lim = t_lim_s

    save_fig1_spectrogram(
        S_disp,
        out_png,
        f_ghz=f_ghz,
        t_s=t_s,
        f_lim_ghz=plot_f_lim,
        t_lim_s=plot_t_lim,
        cmap=cmap,
        dpi=dpi,
        title=title,
        colorbar_label=(
            r"$\log_{10}(\mathrm{PSD})$"
            if scale_mode == "log10"
            else ("dB" if scale_mode == "db" else scale_mode)
        ),
        clim=clim,
        clim_percentiles=None if clim is not None else clim_percentiles,
    )

    return {
        "iq_path": str(iq_path),
        "out_png": str(out_png),
        "n_samples": int(n),
        "duration_s": float(n / fs_hz),
        "fs_hz": float(fs_hz),
        "fc_hz": float(fc_hz),
        "shape": list(S_disp.shape),
        "hop": int(hop),
        "freq_axis": freq_axis,
        "clim": list(clim) if clim is not None else None,
        "clim_percentiles": (
            None
            if clim is not None
            else (list(clim_percentiles) if clim_percentiles else None)
        ),
        "notch_dc": bool(notch_dc),
        "f_span_ghz": [float(f_ghz[0]), float(f_ghz[-1])],
        "t_span_s": [float(t_s[0]), float(t_s[-1])],
        "note": (
            "Fig.1 visual only — not ML features. "
            "X310 lab: use fs=200e6 and crop 3.69–3.81 GHz for 120 MHz span."
        ),
    }
