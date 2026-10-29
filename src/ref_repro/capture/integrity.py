"""Offline integrity primitives. Hardware time is not UTC or clock calibration."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


def positive_rate(value: float) -> float:
    rate = float(value)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Sample rate must be positive and finite")
    return rate


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    """Replace an already-owned manifest; never use to reserve a run name."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class ContinuityTracker:
    """Compare successive first-sample timestamps on a sample-rate tick grid.

    Missing timestamps never count as PASS. Counter consistency describes the
    recorded receive blocks, not physical oscillator accuracy or UTC alignment.
    """
    def __init__(self, sample_rate: float):
        self.fs = positive_rate(sample_rate)
        self.expected_tick = None
        self.blocks = 0
        self.unknown_blocks = 0
        self.discontinuities = 0
        self.error_events = 0

    def observe(self, samples: int, tick: int | None, *, error: bool = False) -> str:
        if not isinstance(samples, int) or samples < 0 or (tick is not None and not isinstance(tick, int)):
            raise ValueError("Negative sample count")
        if error:
            self.error_events += 1
        if samples:
            self.blocks += 1
            if tick is None:
                self.unknown_blocks += 1
                self.expected_tick = None
            else:
                if self.expected_tick is not None and tick != self.expected_tick:
                    self.discontinuities += 1
                self.expected_tick = tick + samples
        return self.status

    @property
    def status(self) -> str:
        if self.error_events or self.discontinuities:
            return "FAIL"
        if not self.blocks or self.unknown_blocks:
            return "NOT_VERIFIED"
        return "PASS"


def metadata_tick(metadata, sample_rate: float) -> int | None:
    if not getattr(metadata, "has_time_spec", False):
        return None
    spec = metadata.time_spec
    if hasattr(spec, "to_ticks"):
        return int(spec.to_ticks(sample_rate))
    # Compatibility with test adapters: reject floating seconds that cannot
    # resolve a sample. Real UHD TimeSpec exposes to_ticks().
    seconds = float(spec.get_real_secs())
    if not math.isfinite(seconds) or math.ulp(seconds) * sample_rate > 0.5:
        return None
    return round(seconds * sample_rate)


def audit_capture(path: str | Path) -> dict:
    """Recompute byte/sample/hash/time checks without UHD or any file mutation."""
    from ref_repro.dsp.iq_io import load_metadata, resolve_fs
    path = Path(path)
    meta = load_metadata(path) or {}
    size = path.stat().st_size
    result = {"byte_integrity": "PASS" if size % 8 == 0 else "FAIL",
              "sample_integrity": "NOT_VERIFIED", "hash_integrity": "NOT_VERIFIED",
              "time_continuity": "NOT_VERIFIED", "eligible": False}
    if "n_samples" in meta:
        result["sample_integrity"] = "PASS" if size == 8 * meta["n_samples"] else "FAIL"
    if meta.get("sha256"):
        result["hash_integrity"] = "PASS" if sha256_file(path) == meta["sha256"] else "FAIL"
    events_path = Path(str(path) + ".events.jsonl")
    metadata_sample_check = result["sample_integrity"]
    if events_path.exists():
        if not meta.get("events_sha256") or sha256_file(events_path) != meta["events_sha256"]:
            result["hash_integrity"] = "FAIL"
            return result
        tracker = ContinuityTracker(resolve_fs(meta, float("nan")))
        written = 0
        index_ok = True
        try:
            with events_path.open(encoding="utf-8") as stream:
                for line in stream:
                    event = json.loads(line)
                    index_ok &= event["stored_index"] == written
                    tracker.observe(event["received_samples"], event["hardware_time_ticks"],
                                    error=event["error"] != "none")
                    if not 0 <= event["stored_samples"] <= event["received_samples"]:
                        raise ValueError("Invalid stored sample count")
                    written += event["stored_samples"]
        except (ValueError, TypeError, KeyError):
            result["sample_integrity"] = "FAIL"
            return result
        result["sample_integrity"] = "PASS" if index_ok and written * 8 == size and metadata_sample_check == "PASS" else "FAIL"
        result["time_continuity"] = tracker.status
    result["eligible"] = meta.get("status") == "complete" and meta.get("valid") is True and all(
        result[k] == "PASS" for k in ("byte_integrity", "sample_integrity", "hash_integrity", "time_continuity"))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Offline IQ byte/sample/hash/time audit; no UHD access")
    parser.add_argument("iq", type=Path)
    args = parser.parse_args()
    result = audit_capture(args.iq)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["eligible"] else 2)
