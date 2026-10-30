# CAIF SDR pipeline bundle

Scripts, configuration, frozen detector, derived figures, and run-level hashes that support the manuscript *Traceable Passive SDR Measurements for 5G NR Synchronization Signals: A CAIF-Guided Testbed Study*.

Public GitHub target cited in the paper: <https://github.com/jakunzler/ieee_icc2027>

## What this bundle is

- **C1** is a classification (discovery, synchronization, broadcast information, access establishment, link maintenance). It is not a new waveform.
- **C2** is a traceable acquisition and provenance path: immutable IQ, receiver diagnostics, configuration hash, frozen detector. It is not a robustness result.

Five 2 s n78 recordings from one gNB process: E1 5/5, E2 3/5, 997 nested PSS/SSS events, all PCI 1, spacing within one sample of 10 ms. The 9/10 gate is unreachable. Whole-file score overlap is not robustness. PBCH, a false-positive rate, sensitivity, and multi-technology validation are not claimed.

## What this bundle is not

- Not a jammer or interference dataset. No interfering signal was transmitted.
- Not a Varotto WIFS 2024 reproduction package. CNN/KNN/SVM notebooks are out.
- Not SigMF-labelled historical IQ. SigMF is proposed, not applied to the `.c64` files.
- Not the original 15 GB of IQ. The `.c64` files and the lab `ledger.json` are on a restricted Drive folder named `ieee_icc2027_e1e2_batch1`.

Detector `events.json` files are in `campaign/e1e2_batch1/<run_id>/`. See `DATA.md`.

## Layout

```text
README.md DATA.md VERSIONS.md LICENSE
configs/
  gnb_n78_100mhz_1t1r_x310.yml   # executed 100 MHz 1T1R profile
  lab_addrs.env
  default.yaml                   # watchdog defaults (also contains unused ML keys)
src/ref_repro/
  capture/   watchdog, integrity, NR profile audit
  dsp/       iq_io, frozen nr_ssb.py, spectrogram helpers
  viz/       20 s display helper
scripts/     gNB / Open5GS / watchdog isolate, compare_received, make_figures
tests/       synthetic IQ integrity tests (no radio)
campaign/e1e2_batch1/
  ledger.json manifest.json comparison_summary.json
  detector.sha256 config.sha256
  <run_id>/run.json metrics.json events.json
figures/
```

## Frozen hashes (verify after clone)

```bash
sha256sum src/ref_repro/dsp/nr_ssb.py
# 736ba4492a63315717c7fa2164714df8c87c9e0d9551e045ca1875cfc9793966

sha256sum configs/gnb_n78_100mhz_1t1r_x310.yml
# 233cb3783be258bc7fbe430309e36169bbea8e2964802fbead1fd5537daab390
```

Detector call used on the five files: `identify_pss_sss` at 184.32 MS/s, SCS 30 kHz, CP `(432,432,432,432)`, `ssb_offset_hz=-40080000`, thresholds 0.7/0.7, scan 2,000,000 samples, hop 1,843,200, FFT 6144.

## Reproduce derived figures (requires lab IQ)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
# Drive/Seagate: IQ under iq/<run_id>/*.c64
# events.json in this repo: campaign/e1e2_batch1/<run_id>/
export IQ_ROOT=/path/to/ieee_icc2027_e1e2_batch1/iq
export EVENTS_ROOT=campaign/e1e2_batch1
python3 scripts/compare_received.py
```

Without IQ, `campaign/e1e2_batch1/comparison_summary.json` and `figures/fig_pss_median_by_run.pdf` already store the published numbers.

Integrity tests (synthetic, no radio):

```bash
PYTHONPATH=src pytest tests/test_iq_integrity.py
```

## Tower roles (as used for the five runs)

| Host | Role |
|---|---|
| Tower02 | Open5GS + srsRAN gNB, X310 `192.168.40.3` |
| Tower03 | Watchdog X310 `192.168.40.2` receive-only, Telit UE |

The X410 jammer box is drawn dashed in the paper (Case C only) and was idle. `lab_addrs.env` still lists it because it is part of the laboratory inventory.
