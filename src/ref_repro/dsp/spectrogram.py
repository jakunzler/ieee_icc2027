"""Espectrogramas fiéis ao artigo: FFT simples 1024, stack 100×1024, -log(x+ε).

Diferença crítica vs. watchdog antigo:
- NÃO passa por PNG / matplotlib / resize para treinar ML.
- Matrizes float32 salvas em .npy com shape (100, 1024).
"""
from __future__ import annotations

import numpy as np


def apply_log_transform(psd: np.ndarray, eps: float = 1e-21) -> np.ndarray:
    """f(x) = -log(x + eps) — escala log e evita zeros (Sec. II-D)."""
    return (-np.log(psd.astype(np.float64) + eps)).astype(np.float32)


def periodogram_fft(x: np.ndarray, n_fft: int = 1024) -> np.ndarray:
    """PSD via FFT simples (não Welch), como no artigo.

    Retorna potência linear |X[k]|^2 / N para k=0..n_fft-1.
    """
    if len(x) < n_fft:
        raise ValueError(f"Janela precisa de {n_fft} amostras, recebeu {len(x)}")
    window = np.hanning(n_fft).astype(np.float32)
    xw = x[:n_fft].astype(np.complex64) * window
    X = np.fft.fft(xw, n=n_fft)
    psd = (np.abs(X) ** 2) / n_fft
    return psd.astype(np.float32)


def iq_to_spectrogram(
    iq: np.ndarray,
    n_fft: int = 1024,
    n_psd: int = 100,
    hop: int | None = None,
    eps: float = 1e-21,
    apply_log: bool = True,
) -> np.ndarray:
    """Constrói espectrograma (n_psd × n_fft) a partir de IQ complexos.

    Parameters
    ----------
    iq : complex array
    n_fft, n_psd, hop : parâmetros do artigo (hop=n_fft → sem overlap)
    eps : constante para -log
    apply_log : se True, aplica f(x)=-log(x+eps)

    Returns
    -------
    S : float32 array shape (n_psd, n_fft)
    """
    hop = n_fft if hop is None else hop
    need = hop * (n_psd - 1) + n_fft
    if len(iq) < need:
        raise ValueError(
            f"IQ curto: precisa de {need} amostras para {n_psd}×{n_fft} (hop={hop}), "
            f"tem {len(iq)}"
        )

    rows = []
    for i in range(n_psd):
        start = i * hop
        seg = iq[start : start + n_fft]
        rows.append(periodogram_fft(seg, n_fft=n_fft))
    S = np.stack(rows, axis=0)  # (n_psd, n_fft)
    if apply_log:
        S = apply_log_transform(S, eps=eps)
    return S.astype(np.float32)


def mean_psd(spectrogram: np.ndarray) -> np.ndarray:
    """Média temporal do espectrograma → vetor 1024-D (entrada KNN/SVM)."""
    return np.mean(spectrogram, axis=0).astype(np.float32)


def spectrogram_duration_s(n_fft: int, n_psd: int, hop: int, fs_hz: float) -> float:
    """Duração temporal coberta pelo espectrograma."""
    n_samples = hop * (n_psd - 1) + n_fft
    return n_samples / fs_hz
