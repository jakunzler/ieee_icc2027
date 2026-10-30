# Data that is not in this bundle

The manuscript keeps immutable IQ, configuration hashes, and a frozen detector.
This directory holds the **hashes, metadata, scripts, and derived figures**.
The original recordings stay off GitHub. A restricted Google Drive copy lives under `research/publications_artifacts/ieee_icc2027_e1e2_batch1/` (UFG account; not a DOI). The five `.c64` files on the Seagate archive were re-hashed on 2026-10-29 and match the table below. Zenodo/OSF remain the intended public archive when a data permission exists.

SigMF is a proposed interoperability layer. The historical `.c64` files are **not** claimed to already conform to it.

## Five hashed 2 s recordings (E1 5/5, E2 3/5)

Each file: 368,640,000 complex samples, 2,949,120,000 B, `cf32_le`, 184.32 MS/s, requested centre 3750 MHz, gain 20, antenna RX2. NVMe SHA-256 equals the Seagate copy (direct I/O). Queue saturation 0.


| Attempt | Run ID                             | E1   | E2   | Events | SHA-256                                                            | Lab filename (when known)                                    |
| ------- | ---------------------------------- | ---- | ---- | ------ | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| 1       | `8e04773dedd340dba84890a01269509b` | PASS | PASS | 199    | `3e1041d51eb4b321a96b06bc7f0be4168767cc41e85eda5c7bc1182bfc18682c` | `casoA_20261026_170434_8e04773dedd340dba84890a01269509b.c64` |
| 2       | `8bce6c8770324333867ce4234e0a78df` | PASS | PASS | 200    | `3ef9c00927d47cce348882129f49b87e5cf3993ad20578475d2bca6cb83d5296` | `casoA_20261026_170845_8bce6c8770324333867ce4234e0a78df.c64` |
| 3       | `ec169bbf882548549bfb5ddb6923f1a0` | PASS | PASS | 199    | `e8ab967c10f2eb2296cdc0a5d172a955e3acbe36d4a59e65d53b6fc32fc095af` | `casoA_20261026_171223_ec169bbf882548549bfb5ddb6923f1a0.c64` |
| 4       | `7e9ca907a8c44abb845a760cc4d3c4d5` | PASS | FAIL | 200    | `f7a27348f57f39748d9482b703d7614f356690895acc24b0d637ba206194c90a` | `casoA_20261026_171616_7e9ca907a8c44abb845a760cc4d3c4d5.c64` |
| 5       | `b0b1ab6145304c6aa0fceed774c5e360` | PASS | FAIL | 199    | `c8909fbeac51c3b5cabd96e735a0c7f30df1029f6636332838d8b958f43d339e` | `casoA_20261026_180953_b0b1ab6145304c6aa0fceed774c5e360.c64` |


Lab paths (Tower03):

- NVMe: `icc2027_caif_pilot/nvme_e1e2_batch1/`
- Metadata / events: `icc2027_caif_pilot/e1e2_batch1/<run_id>/`
- Lab ledger SHA-256 after attempt 5: `253d203100ce09baad31e3dbddc9eaeac027a4620309548b6688b5f9bfc68361`

Restricted Drive layout:

```text
ieee_icc2027_e1e2_batch1/
  README.txt  ledger.json          # lab ledger, SHA-256 253d2031…
  attempts/                        # attempt0N pre/post JSON and capture logs
  metadata/<run_id>/               # sidecar, watchdog jsonl, events.json
  iq/<run_id>/                     # original .c64 (2,949,120,000 B)
```

This GitHub tree includes the frozen-detector `events.json` for each run (997 accepted PSS/SSS events in total). It does not include `.c64` files or watchdog sidecars. `campaign/e1e2_batch1/ledger.json` here is a public reconstruction; the lab ledger on Drive is the byte-identical file. gNB-log brackets remain on Tower02 if they were not copied into `attempts/*_post.json`.

Attempts 4 and 5 stay in the ten-run denominator.

## 20 s spectrogram (Fig. 1)

`figures/capture_20s_120MHz.png` is a qualitative overview (120 MHz span, gain 20).
It is **not** one of the five hashed 2 s files. A complete original-IQ / session / processing linkage for that capture has not been established.

## Not part of this manuscript

- G0/pilot runs `3fa8c03bf18d412dafbdcdf8f3adb285` and `b692d7222244483c932c4bc50b100f08`
- ML training notebooks, CNN/KNN/SVM results
- terminated-input negative control (not collected)
- E3, E4, E5 (not run)

