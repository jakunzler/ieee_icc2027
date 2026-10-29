"""Carrega IQ (.c64 / .fc32 / .npy) e metadados JSON opcionais."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ref_repro.capture.integrity import positive_rate


def load_iq(path: str | Path) -> np.ndarray:
    path = Path(path)
    if path.suffix == ".npy":
        x = np.load(path, allow_pickle=False)
    elif path.suffix in {".c64", ".fc32", ".iq"}:
        if path.stat().st_size % np.dtype(np.complex64).itemsize:
            raise ValueError("Incomplete complex sample: payload bytes must be a multiple of 8")
        meta = load_metadata(path) or {}
        datatype = meta.get("datatype")
        if datatype not in (None, "cf32_le", "cf32_be"):
            raise ValueError("Unsupported complex sample datatype")
        dtype = {"cf32_le": "<c8", "cf32_be": ">c8"}.get(datatype, np.complex64)
        x = np.fromfile(path, dtype=dtype)
    else:
        raise ValueError(f"Formato IQ não suportado: {path.suffix}")
    if x.ndim != 1:
        raise ValueError("Expected a single-channel one-dimensional IQ array")
    if not np.iscomplexobj(x):
        raise ValueError(f"Esperado complexo, recebeu {x.dtype}")
    if not np.isfinite(x).all():
        raise ValueError("IQ contains non-finite samples")
    return x.astype(np.complex64, copy=False)


def load_metadata(iq_path: str | Path) -> dict | None:
    meta_path = Path(str(iq_path) + ".json")
    if not meta_path.exists():
        alt = Path(iq_path).with_suffix(".json")
        meta_path = alt if alt.exists() else meta_path
    if not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("IQ metadata must be a JSON object")
    return value


def resolve_fs(meta: dict | None, default_fs: float) -> float:
    if not meta:
        return positive_rate(default_fs)
    for key in ("fs_effective", "fs_usrp", "fs_actual", "fs", "samp_rate"):
        if key in meta:
            fs = positive_rate(meta[key])
            if key == "fs_usrp":
                return positive_rate(fs / positive_rate(meta.get("decimation", 1)))
            return fs
    return positive_rate(default_fs)


def infer_case_from_name(name: str) -> str | None:
    """Extrai A/B/C de nomes tipo casoA_*, classA_*, capture_C_..."""
    import re

    m = re.search(r"(?:caso|class|case)[_-]?([ABC])", name, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?:^|[_\-])([ABC])(?:[_\-]|$)", name)
    if m:
        return m.group(1).upper()
    return None
