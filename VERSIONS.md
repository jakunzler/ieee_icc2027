# Software and hardware versions

Values below were read on the CERISE/UFG hosts during the 2026-10-26
E1–E2 batch. Fields not measured are marked `NOT_RECORDED`.

## Hosts

| Item | Record |
|---|---|
| Workstations | two Dell Precision 5860, Ubuntu 24.04 |
| Tower02 hostname | `cerise-Precision-5860-Tower02` |
| Tower02 kernel (audit) | `6.17.0-38-generic` |
| Switch | D-Link DGS-1510-28X |

## Radio stack (executed profile)

| Item | Record |
|---|---|
| Core | Open5GS (lab README: 2.8; pin the installed package if redistributing) |
| gNB | srsRAN Project 25.10, `Built in Release` commit `d2f4b70dda` |
| gNB binary | `/usr/local/bin/gnb` SHA-256 `0bb7e96b063be232996679d4c10c374ddc51123d69c273d6c6bbad3f186036b4` |
| YAML | `configs/gnb_n78_100mhz_1t1r_x310.yml` SHA-256 `233cb3783be258bc7fbe430309e36169bbea8e2964802fbead1fd5537daab390` |
| UHD (host CLI) | `4.8.0.HEAD-0-g308126a4` |
| UE | Telit FN990A40 |
| gNB USRP | X310 `192.168.40.3`, serial `33A356E` |
| Watchdog USRP | X310 `192.168.40.2`, motherboard `338F075`, RX `33C9ABE`, antenna RX2 |
| Jammer USRP | X410 present in the lab; **not used** in this manuscript |
| FPGA / firmware | `NOT_RECORDED` |
| Analog daughterboard (watchdog) | UBX-160 v2 `33C9ABE` (G2 preflight); physical jack not inspected |

## Frozen analysis

| Item | Record |
|---|---|
| Detector file | `src/ref_repro/dsp/nr_ssb.py` |
| Detector SHA-256 | `736ba4492a63315717c7fa2164714df8c87c9e0d9551e045ca1875cfc9793966` |
| Thresholds | PSS 0.7 / SSS 0.7 |
| SSB frequency | 3709.92 MHz supplied (offset −40.080 MHz); YAML has no SSB field |
| Python | 3.10+; NumPy 1.x required if the Ubuntu UHD binding is used |

## Licenses

- This bundle: MIT (`LICENSE`)
- Open5GS, srsRAN Project, UHD: upstream licenses, not duplicated here
- Photographs and IQ: CERISE/UFG permission required before public redistribution
