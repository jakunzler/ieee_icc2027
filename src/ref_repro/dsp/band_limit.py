"""Limita IQ capturado (ex.: 200 MS/s) à faixa de análise (ex.: 3.69–3.81 GHz).

No X310, 120e6 não é taxa válida: captura-se a 200e6 e decima para
``f_hi - f_lo`` (120 MHz), o que preserva o conteúdo em torno de ``fc``.
"""
from __future__ import annotations

from fractions import Fraction

import numpy as np


def analysis_fs_hz(f_lo_hz: float, f_hi_hz: float) -> float:
    bw = float(f_hi_hz) - float(f_lo_hz)
    if bw <= 0:
        raise ValueError(f"f_lim inválido: [{f_lo_hz}, {f_hi_hz}]")
    return bw


def capture_samples_for_window(
    n_analysis: int,
    fs_capture: float,
    fs_analysis: float,
) -> int:
    """Quantas amostras na taxa de captura equivalem a ``n_analysis`` na análise."""
    if fs_capture <= 0 or fs_analysis <= 0:
        raise ValueError("fs deve ser > 0")
    return int(np.ceil(n_analysis * float(fs_capture) / float(fs_analysis)))


def resample_iq(iq: np.ndarray, fs_in: float, fs_out: float) -> np.ndarray:
    """Reamostra IQ complexo de ``fs_in`` → ``fs_out`` (polyphase)."""
    iq = np.asarray(iq, dtype=np.complex64)
    if abs(fs_in - fs_out) / fs_out < 1e-6:
        return iq
    if fs_in < fs_out * 0.95:
        raise ValueError(
            f"fs_capture={fs_in:.3e} < fs_analysis={fs_out:.3e}: "
            "a captura não cobre a faixa pedida (3.69–3.81 GHz / 120 MHz). "
            "Use capture_fs_hz: 200e6 no ml_capture."
        )
    frac = Fraction(fs_out / fs_in).limit_denominator(1000)
    from scipy.signal import resample_poly

    out = resample_poly(iq, frac.numerator, frac.denominator)
    return np.asarray(out, dtype=np.complex64)


def prepare_analysis_chunk(
    iq_chunk: np.ndarray,
    *,
    fs_capture: float,
    fs_analysis: float,
    n_analysis: int,
    fc_hz: float | None = None,
    f_lo_hz: float | None = None,
    f_hi_hz: float | None = None,
) -> np.ndarray:
    """Chunk na taxa de captura → janela exata ``n_analysis`` na taxa de análise.

    Se ``f_lo/f_hi`` e ``fc`` forem dados e o centro da banda ≠ fc, aplica
    shift de frequência antes da reamostragem.
    """
    x = np.asarray(iq_chunk, dtype=np.complex64)
    if (
        fc_hz is not None
        and f_lo_hz is not None
        and f_hi_hz is not None
    ):
        band_c = 0.5 * (float(f_lo_hz) + float(f_hi_hz))
        df = band_c - float(fc_hz)
        if abs(df) > 1.0:
            t = np.arange(len(x), dtype=np.float64) / float(fs_capture)
            x = (x * np.exp(-2j * np.pi * df * t)).astype(np.complex64)

    y = resample_iq(x, fs_capture, fs_analysis)
    if len(y) < n_analysis:
        raise ValueError(
            f"Após resample: {len(y)} amostras < n_analysis={n_analysis}"
        )
    return y[:n_analysis]
