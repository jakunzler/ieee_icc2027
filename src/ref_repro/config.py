"""Utilitários de configuração e seeds."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "configs" / "default.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Resolve relative paths against package root
    for key, rel in cfg.get("paths", {}).items():
        p = Path(rel)
        cfg["paths"][key] = str(p if p.is_absolute() else ROOT / p)
    return cfg


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
