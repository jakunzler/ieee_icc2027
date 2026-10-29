"""Limited offline consistency checks for the two n78/30 kHz/1T1R candidates.

This is not a full 3GPP validator, srsRAN semantic validator, or hardware test.
NR resource-block counts: TS 38.104, 5.3.2 (FR1, 30 kHz, 40/100 MHz).
"""
import argparse
import json
import re
from pathlib import Path
import numpy as np
import yaml
from ref_repro.capture.integrity import sha256_file


def audit_nr_profile(path):
    path = Path(path)
    cfg = yaml.safe_load(path.read_text())
    cell, radio = cfg["cell_cfg"], cfg["ru_sdr"]
    bandwidth, scs = cell["channel_bandwidth_MHz"], cell["common_scs"]
    errors = []
    if cell["band"] != 78 or bandwidth not in (40, 100) or scs != 30:
        errors.append("Outside the limited n78/30 kHz/40-or-100 MHz audit scope")
    if cell["nof_antennas_dl"] != 1 or cell["nof_antennas_ul"] != 1:
        errors.append("Candidate comparison requires 1T1R")
    arfcn = cell["dl_arfcn"]
    fc = 3e9 + (arfcn-600000)*15000
    if not 3300e6 <= fc-bandwidth*1e6/2 <= fc+bandwidth*1e6/2 <= 3800e6:
        errors.append("Requested channel does not fit n78")
    fs = float(radio["srate"])*1e6
    match = re.search(r"(?:^|,)master_clock_rate=([^,]+)", radio["device_args"])
    mcr = float(match.group(1)) if match else None
    if not np.isfinite(fs) or fs <= 0 or mcr not in (184.32e6, 200e6):
        errors.append("Rate/master-clock values not in audit scope")
    elif not np.isclose(mcr/fs, round(mcr/fs), atol=1e-8, rtol=0):
        errors.append("Sample rate is not an integer master-clock divisor")
    nrb = {40:106, 100:273}.get(bandwidth)
    if nrb and fs < nrb*12*scs*1000:
        errors.append("Sample rate cannot cover the occupied resource-block span")
    return {"profile": str(path), "sha256": sha256_file(path),
            "offline_consistency": "FAIL" if errors else "PASS", "errors": errors,
            "carrier_hz_calculated": fc, "sample_rate_requested": fs,
            "resource_blocks_reference": nrb,
            "ssb_explicit": any("ssb" in key.lower() for key in cell),
            "full_nr_compliance": "NOT_VERIFIED", "operational_support": "NOT_VERIFIED"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps([audit_nr_profile(p) for p in args.profile], indent=2))
