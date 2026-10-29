"""Passive PSS/SSS identification on a contiguous, frequency-centred NR segment.

Sequences: TS 38.211 V18.4.0, 7.4.2.2/7.4.2.3 and 7.4.3 mapping.
Known SSB numerology, CP lengths and approximate SSB centre are required.
No PBCH/MIB decoding, channel equalization, blind raster search, RF or OTA claim.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
from scipy.signal import correlate, find_peaks
from functools import lru_cache

from ref_repro.capture.integrity import audit_capture, positive_rate, sha256_file
from ref_repro.dsp.iq_io import load_iq, load_metadata, resolve_fs


def _sequence(initial, tap):
    bits = np.empty(127 + 7, dtype=np.int8)
    bits[:7] = initial
    for index in range(127):
        bits[index + 7] = bits[index] ^ bits[index + tap]
    return bits[:127]


def pss_sequence(nid2: int) -> np.ndarray:
    if not isinstance(nid2, (int, np.integer)) or not 0 <= nid2 <= 2:
        raise ValueError("NID2 must be 0, 1 or 2")
    bits = _sequence([0, 1, 1, 0, 1, 1, 1], 4)
    return 1 - 2 * bits[(np.arange(127) + 43 * nid2) % 127]


def sss_sequence(pci: int) -> np.ndarray:
    if not isinstance(pci, (int, np.integer)) or not 0 <= pci <= 1007:
        raise ValueError("PCI must be in [0, 1007]")
    nid1, nid2 = divmod(int(pci), 3)
    a = _sequence([1, 0, 0, 0, 0, 0, 0], 4)
    b = _sequence([1, 0, 0, 0, 0, 0, 0], 1)
    m0, m1 = 15 * (nid1 // 112) + 5 * nid2, nid1 % 112
    return (1 - 2 * a[(np.arange(127) + m0) % 127]) * (1 - 2 * b[(np.arange(127) + m1) % 127])


@lru_cache(maxsize=3)
def _sss_bank(nid2):
    return np.stack([sss_sequence(3 * nid1 + nid2) for nid1 in range(336)])


@dataclass(frozen=True)
class SSBIdentification:
    pci: int
    nid1: int
    nid2: int
    pss_useful_start_sample: int
    ssb_start_sample: int
    pss_score: float
    sss_score: float
    pbch_status: str = "NOT_IMPLEMENTED"
    ota_validated: bool = False


def identify_pss_sss(iq, *, sample_rate: float, scs_hz: float,
                     cp_samples: tuple[int, int, int, int], ssb_offset_hz: float = 0,
                     pss_threshold: float = 0.7, sss_threshold: float = 0.7,
                     max_candidates: int = 32) -> list[SSBIdentification]:
    """Identify PCI via normalized time PSS and frequency SSS correlations.

    One-dimensional finite contiguous complex IQ only. CP is explicitly supplied
    for the four SSB symbols; approximate centre offset is removed, not estimated.
    Scores are similarities, not calibrated detection probabilities. The finite
    candidate cap bounds memory/work; coverage/thresholds need later validation.
    """
    fs, scs = positive_rate(sample_rate), positive_rate(scs_hz)
    if scs not in (15000, 30000):
        raise ValueError("Offline implementation supports FR1 15/30 kHz only")
    nfft = round(fs / scs)
    if nfft < 256 or not np.isclose(fs / scs, nfft, atol=1e-8, rtol=0):
        raise ValueError("Sample rate must yield an integral FFT of at least 256")
    if len(cp_samples) != 4 or any(not isinstance(v, (int, np.integer)) or v < 1 or v >= nfft for v in cp_samples):
        raise ValueError("Provide four positive CP lengths in samples below FFT size")
    if not np.isfinite(ssb_offset_hz) or abs(ssb_offset_hz) + 120 * scs > fs / 2:
        raise ValueError("SSB does not fit the sampled Nyquist interval")
    if not (0 < pss_threshold <= 1 and 0 < sss_threshold <= 1) or max_candidates < 1:
        raise ValueError("Invalid correlation thresholds/candidate limit")
    x = np.asarray(iq)
    if x.ndim != 1 or not np.iscomplexobj(x) or not np.isfinite(x).all():
        raise ValueError("Expected finite one-dimensional complex IQ")
    if len(x) > 2_000_000:
        raise ValueError("Process explicit contiguous windows of at most 2,000,000 samples")
    if len(x) < 4 * nfft + sum(cp_samples):
        return []
    x = np.asarray(x, dtype=np.complex128)
    if ssb_offset_hz:
        x = x * np.exp(-2j * np.pi * ssb_offset_hz * np.arange(len(x)) / fs)
    bins = np.arange(-64, 63) % nfft  # SSB-local subcarriers 56..182, centre at 120.
    energy = np.maximum(0, correlate(np.abs(x)**2, np.ones(nfft), mode="valid", method="fft"))
    candidates = []
    for nid2 in range(3):
        grid = np.zeros(nfft, dtype=np.complex128)
        grid[bins] = pss_sequence(nid2)
        template = np.fft.ifft(grid)
        corr = np.abs(correlate(x, template, mode="valid", method="fft"))
        denom = np.sqrt(energy * np.vdot(template, template).real)
        score = np.divide(corr, denom, out=np.zeros_like(corr), where=denom > 0)
        peaks = list(find_peaks(score, height=pss_threshold, distance=nfft)[0])
        if score[0] >= pss_threshold:
            peaks.append(0)
        if len(score) > 1 and score[-1] >= pss_threshold:
            peaks.append(len(score) - 1)
        candidates.extend((float(score[i]), int(i), nid2) for i in peaks)
    found = []
    for pscore, start, nid2 in sorted(candidates, reverse=True)[:max_candidates]:
        sss_start = start + 2 * nfft + cp_samples[1] + cp_samples[2]
        if start < cp_samples[0] or sss_start + nfft > len(x):
            continue
        spectrum = np.fft.fft(x[sss_start:sss_start + nfft])[bins]
        norm = np.linalg.norm(spectrum) * np.sqrt(127)
        if not norm:
            continue
        bank = _sss_bank(nid2)
        scores = np.abs(bank @ spectrum) / norm
        nid1 = int(np.argmax(scores))
        if scores[nid1] < sss_threshold:
            continue
        if any(abs(old.pss_useful_start_sample - start) < nfft for old in found):
            continue
        found.append(SSBIdentification(3 * nid1 + nid2, nid1, nid2, start,
                                      start - cp_samples[0], pscore, float(scores[nid1])))
    return sorted(found, key=lambda event: event.pss_useful_start_sample)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iq", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fs", type=float)
    parser.add_argument("--scs-hz", type=float, required=True)
    parser.add_argument("--cp-samples", type=int, nargs=4, required=True)
    parser.add_argument("--ssb-offset-hz", type=float, default=0)
    parser.add_argument("--allow-unverified-time", action="store_true")
    parser.add_argument("--start-sample", type=int, default=0)
    parser.add_argument("--sample-count", type=int, default=262144)
    args = parser.parse_args()
    meta = load_metadata(args.iq) or {}
    audit = audit_capture(args.iq)
    synthetic = meta.get("synthetic") is True
    if any(audit[key] == "FAIL" for key in ("byte_integrity", "sample_integrity", "hash_integrity", "time_continuity")) or (not synthetic and not audit["eligible"] and not args.allow_unverified_time):
        parser.error("Uncertified input: inspect integrity or explicitly allow unverified time")
    fs = positive_rate(args.fs) if args.fs is not None else resolve_fs(meta, float("nan"))
    if args.start_sample < 0 or not 1 <= args.sample_count <= 2_000_000:
        parser.error("Specify a non-negative start and 1..2,000,000 samples")
    if args.iq.suffix in {".c64", ".fc32", ".iq"}:
        if args.iq.stat().st_size % 8:
            parser.error("Incomplete complex sample")
        # New captures are explicitly little endian; legacy byte order is the
        # reader's documented native convention, not inferred from a label.
        dtype = "<c8" if meta.get("datatype") == "cf32_le" else np.complex64
        samples = np.memmap(args.iq, dtype=dtype, mode="r")
    else:
        samples = load_iq(args.iq)
    segment = samples[args.start_sample:args.start_sample + args.sample_count]
    events = identify_pss_sss(segment, sample_rate=fs, scs_hz=args.scs_hz,
                             cp_samples=tuple(args.cp_samples), ssb_offset_hz=args.ssb_offset_hz)
    result = {"source_sha256": sha256_file(args.iq), "source_synthetic": synthetic,
              "input_integrity": audit, "sample_rate": fs, "scs_hz": args.scs_hz,
              "cp_samples": args.cp_samples, "ssb_offset_hz": args.ssb_offset_hz,
              "start_sample": args.start_sample, "sample_count": len(segment),
              "event_index_reference": "relative to the specified input segment",
              "implementation_sha256": sha256_file(__file__), "pbch_status": "NOT_IMPLEMENTED",
              "ota_validated": False, "events": [asdict(event) for event in events]}
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
