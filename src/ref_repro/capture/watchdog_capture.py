#!/usr/bin/env python3
"""Captura IQ passiva (watchdog) via UHD — parâmetros do artigo / config.

Defaults vêm de ``configs/default.yaml`` conforme ``--defaults-from``:
  ml   → ml_capture.*  (fluxo espectrogramas A/B/C)
  fig1 → fig1.*        (reprodução Fig. 1)

Requer: uhd, numpy. Exemplo:
  python -m ref_repro.capture.watchdog_capture --case A --defaults-from ml \\
      --out data/raw/train/casoA --addr 192.168.40.2
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ref_repro.config import ROOT, load_config
from ref_repro.capture.integrity import (ContinuityTracker, atomic_json, metadata_tick,
                                          positive_rate, sha256_file)


def infer_master_clock_hz(fs_hz: float) -> float:
    """X310: MCR ∈ {184.32e6, 200e6}; taxa RX = MCR / inteiro.

    Sem ``master_clock_rate`` no device arg, o default é 200 MHz — pedir
    184.32 MS/s é silenciosamente arredondado para 200 MS/s.
    """
    fs = float(fs_hz)
    for mcr in (184.32e6, 200e6):
        ratio = mcr / fs
        nearest = round(ratio)
        if nearest >= 1 and abs(ratio - nearest) < 0.02:
            return float(mcr)
    if abs(fs - 184.32e6) / 184.32e6 < 0.05:
        return 184.32e6
    return 200e6



class CaptureIntegrityError(RuntimeError):
    """An invalid or uncertifiable receive stream; inspect its retained manifest."""


def require_block_sequence(expected: int, actual: int) -> None:
    """Fail closed when a handed-off block skips or repeats a sequence number."""
    if actual != expected:
        raise CaptureIntegrityError(
            f"Dropped or reordered block: expected seq {expected}, got {actual}")


def write_cf32le(stream, samples: np.ndarray) -> int:
    """Write contiguous little-endian complex64 without an intermediate copy."""
    raw = samples.view(np.uint8)
    if raw.ndim != 1 or raw.shape[0] != samples.shape[0] * 8 or not raw.flags.c_contiguous:
        raise CaptureIntegrityError("IQ buffer is not contiguous cf32_le")
    written = stream.write(raw)
    if written != raw.shape[0]:
        raise CaptureIntegrityError(f"Short file write: {written} of {raw.shape[0]} bytes")
    return written


class BlockPool:
    """Fixed buffers. Each buffer is free, in recv, queued, or being written."""

    def __init__(self, count: int, samples: int):
        if count < 1 or samples < 1:
            raise ValueError("Buffer pool must be positive")
        self.buffers = [np.empty(samples, dtype="<c8") for _ in range(count)]
        self._free = list(range(count))
        self._state = ["free"] * count
        self._lock = threading.Lock()
        self._ready = queue.Queue()
        self._waiting = 0
        self._closed = False
        self.max_depth = 0
        self.saturated = 0

    def acquire(self) -> int | None:
        with self._lock:
            if not self._free:
                self.saturated += 1
                return None
            idx = self._free.pop()
            if self._state[idx] != "free":
                raise RuntimeError("Buffer ownership conflict")
            self._state[idx] = "producer"
            return idx

    def abort_producer(self, idx: int | None) -> None:
        if idx is None:
            return
        with self._lock:
            if self._state[idx] == "producer":
                self._state[idx] = "free"
                self._free.append(idx)

    def publish(self, message: dict | None) -> None:
        with self._lock:
            idx = None if message is None else message.get("buf")
            if idx is not None:
                if self._state[idx] != "producer":
                    raise RuntimeError("Buffer ownership conflict")
                self._state[idx] = "queued"
            self._waiting += 1
            if self._waiting > self.max_depth:
                self.max_depth = self._waiting
            if message is not None:
                message["queue_depth"] = self._waiting
        self._ready.put(message)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._waiting += 1
            if self._waiting > self.max_depth:
                self.max_depth = self._waiting
        self._ready.put(None)

    def take(self):
        message = self._ready.get()
        with self._lock:
            self._waiting -= 1
            idx = None if message is None else message.get("buf")
            if idx is not None:
                if self._state[idx] != "queued":
                    raise RuntimeError("Buffer ownership conflict")
                self._state[idx] = "consumer"
        return message

    def release_consumer(self, idx: int) -> None:
        with self._lock:
            if self._state[idx] != "consumer":
                raise RuntimeError("Buffer ownership conflict")
            self._state[idx] = "free"
            self._free.append(idx)

    def discard_pending(self) -> None:
        while True:
            try:
                message = self._ready.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._waiting = max(0, self._waiting - 1)
                idx = None if message is None else message.get("buf")
                if idx is not None and self._state[idx] in {"queued", "producer", "consumer"}:
                    self._state[idx] = "free"
                    self._free.append(idx)


_EVENT_KEYS = ("stored_index", "received_samples", "stored_samples", "hardware_time_ticks",
               "has_time_spec", "error", "continuity", "fragment_offset", "more_fragments",
               "seq", "queue_depth")


class _IqWriter(threading.Thread):
    """Ordered consumer. The receive thread never writes, hashes, or emits JSON."""

    def __init__(self, pool: BlockPool, payload, events, on_block_write):
        super().__init__(name="iq-writer", daemon=False)
        self.pool = pool
        self.payload = payload
        self.events = events
        self.on_block_write = on_block_write
        self.failure = None
        self.written = 0
        self.nbytes = 0
        self.write_s: list[float] = []

    def _event(self, message: dict) -> None:
        self.events.write(json.dumps({key: message[key] for key in _EVENT_KEYS}, allow_nan=False) + "\n")

    def _handle(self, message: dict, expected: int) -> None:
        require_block_sequence(expected, message["seq"])
        if message["stored_samples"]:
            samples = self.pool.buffers[message["buf"]][:message["stored_samples"]]
            if self.on_block_write is not None:
                self.on_block_write({"seq": message["seq"], "buf": message["buf"], "view": samples})
            if not np.isfinite(samples).all():
                message["stored_samples"] = 0
                message["error"] = "non_finite"
                message["continuity"] = "FAIL"
                self._event(message)
                raise CaptureIntegrityError("Non-finite received samples")
            started = time.perf_counter()
            self.nbytes += write_cf32le(self.payload, samples)
            self.write_s.append(time.perf_counter() - started)
            self.written += message["stored_samples"]
        self._event(message)

    def run(self) -> None:
        expected = 0
        try:
            while True:
                message = self.pool.take()
                if message is None:
                    break
                idx = message.get("buf")
                try:
                    self._handle(message, expected)
                    expected += 1
                finally:
                    if idx is not None and self.pool._state[idx] == "consumer":
                        self.pool.release_consumer(idx)
                if self.failure:
                    break
        except BaseException as exc:
            self.failure = exc
        finally:
            self.pool.discard_pending()


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _readback(usrp, name, *args):
    method = getattr(usrp, name, None)
    try:
        return method(*args) if method else None
    except (RuntimeError, TypeError):
        return None


def capture_iq(usrp, uhd, *, out_dir: Path, case: str, fs_requested: float,
               seconds: float, channel: int = 0, config_path: Path | None = None,
               run_id: str | None = None, write_chunk_mib: float = 64,
               max_empty_receives: int = 3, max_wall_s: float | None = None,
               metadata_extra: dict | None = None, buffer_count: int = 32,
               on_block_write=None):
    """Acquire RX only. Injectable USRP/UHD interfaces permit entirely offline tests.

    ``recv`` stays on the caller. A bounded pool hands each block to one writer
    thread. Queue exhaustion, gaps, overflow, and write errors fail closed.

    A UUID plus exclusive file creation prevents overwrites. Every recv records
    its hardware time/counters; invalid blocks are not concatenated. Missing
    timestamps cannot certify continuity. Final manifests also describe aborted
    captures, preserving data already written. No transmitter is used here.
    """
    if case not in {"A", "B", "C"}:
        raise ValueError("Unknown capture case")
    fs = positive_rate(usrp.get_rx_rate(channel))
    fs_requested = positive_rate(fs_requested)
    seconds = positive_rate(seconds)
    if max_empty_receives < 1:
        raise ValueError("max_empty_receives must be positive")
    if buffer_count < 1:
        raise ValueError("buffer_count must be positive")
    limit = positive_rate(max_wall_s if max_wall_s is not None else max(30, seconds * 5))
    n = int(round(fs * seconds))
    if n < 1:
        raise ValueError("Duration yields no samples")
    run_id = run_id or uuid.uuid4().hex
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in run_id):
        raise ValueError("Invalid run identifier")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    path = out_dir / f"caso{case}_{stamp}_{run_id}.c64"
    sidecar = Path(str(path) + ".json")
    events_path = Path(str(path) + ".events.jsonl")
    if path.exists() or events_path.exists():
        raise FileExistsError(path)
    # Reserve the sidecar exclusively before any payload file. A collision fails
    # without touching the already owned data or manifest.
    meta = dict(metadata_extra or {})
    meta.update({"run_id": run_id, "case": case, "stamp": stamp, "started_utc": _utc_now(),
                 "status": "running", "valid": False, "fs_requested": fs_requested,
                 "fs_effective": fs, "duration_s": seconds, "requested_samples": n,
                 "n_samples": 0, "datatype": "cf32_le", "bytes_per_complex_sample": 8,
                 "channel": channel, "role": "watchdog", "overflows": 0,
                 "wire_datatype": "sc16", "stream_now": True,
                 "pipeline": "bounded_producer_consumer",
                 "buffer_count": buffer_count,
                 "write_chunk_mib": write_chunk_mib,
                 "max_empty_receives": max_empty_receives, "max_wall_s": limit,
                 "sequence_errors": 0, "timeouts": 0,
                 "time_reference": "UHD device time; UTC alignment NOT_VERIFIED",
                 "clock_accuracy": "NOT_VERIFIED",
                 "configuration_sha256": sha256_file(config_path) if config_path else None,
                 "recorder_sha256": sha256_file(__file__),
                 "integrity_checker_sha256": sha256_file(Path(__file__).with_name("integrity.py")),
                 "numpy_version": np.__version__,
                 "uhd_version": getattr(uhd, "get_version_string", lambda: None)(),
                 "fc": _readback(usrp, "get_rx_freq", channel),
                 "gain": _readback(usrp, "get_rx_gain", channel),
                 "analog_bandwidth_hz": _readback(usrp, "get_rx_bandwidth", channel),
                 "master_clock_rate_effective": _readback(usrp, "get_master_clock_rate"),
                 "clock_source": _readback(usrp, "get_clock_source", 0),
                 "time_source": _readback(usrp, "get_time_source", 0),
                 "radio_info": _readback(usrp, "get_usrp_rx_info", channel)})
    with sidecar.open("x", encoding="utf-8") as stream:
        json.dump(meta, stream, indent=2, allow_nan=False)
    tracker = ContinuityTracker(fs)
    accepted = 0
    empty = 0
    t0 = time.monotonic()
    streamer = None
    started = False
    failure = None
    owns_payload = False
    writer = None
    pool = None
    try:
        with path.open("xb", buffering=8 * 1024 * 1024) as payload:
            owns_payload = True
            with events_path.open("x", encoding="utf-8", buffering=1024 * 1024) as events:
              try:
                args = uhd.usrp.StreamArgs("fc32", "sc16")
                args.channels = [channel]
                streamer = usrp.get_rx_stream(args)
                size = max(streamer.get_max_num_samps(), min(262144, int(positive_rate(write_chunk_mib) * 1024**2 / 8)))
                pool = BlockPool(buffer_count, size)
                writer = _IqWriter(pool, payload, events, on_block_write)
                writer.start()
                rxmeta = uhd.types.RXMetadata()
                cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
                cmd.stream_now = True
                streamer.issue_stream_cmd(cmd)
                started = True
                seq = 0
                while accepted < n:
                    if writer.failure is not None:
                        raise writer.failure
                    if time.monotonic() - t0 >= limit:
                        raise CaptureIntegrityError("Acquisition wall deadline exceeded")
                    idx = pool.acquire()
                    if idx is None:
                        raise CaptureIntegrityError("Receive queue exhausted")
                    published = False
                    try:
                        count = int(streamer.recv(pool.buffers[idx], rxmeta, timeout=3.0))
                        if not 0 <= count <= size:
                            raise CaptureIntegrityError("Invalid receive count")
                        code = rxmeta.error_code
                        none = uhd.types.RXMetadataErrorCode.none
                        overflow = uhd.types.RXMetadataErrorCode.overflow
                        timeout = getattr(uhd.types.RXMetadataErrorCode, "timeout", None)
                        is_error = code != none
                        name = "none" if not is_error else ("overflow" if code == overflow else ("timeout" if code == timeout else "receiver_error"))
                        if code == overflow:
                            meta["overflows"] += 1
                        if getattr(rxmeta, "out_of_sequence", False):
                            meta["sequence_errors"] += 1
                            is_error = True
                            name = "sequence_error"
                        if code == timeout:
                            meta["timeouts"] += 1
                        tick = metadata_tick(rxmeta, fs)
                        state = tracker.observe(count, tick, error=is_error)
                        take = min(count, n - accepted) if not is_error and state != "FAIL" else 0
                        held = idx
                        if take == 0:
                            pool.abort_producer(idx)
                            held = None
                        message = {"stored_index": accepted, "received_samples": count, "stored_samples": take,
                                   "hardware_time_ticks": tick, "has_time_spec": tick is not None,
                                   "error": name, "continuity": state,
                                   "fragment_offset": int(getattr(rxmeta, "fragment_offset", 0)),
                                   "more_fragments": bool(getattr(rxmeta, "more_fragments", False)),
                                   "seq": seq, "buf": held, "queue_depth": 0}
                        pool.publish(message)
                        published = True
                        seq += 1
                        if state == "FAIL":
                            raise CaptureIntegrityError(f"Invalid RX stream: {name}, discontinuities={tracker.discontinuities}")
                        if not count:
                            empty += 1
                            if empty >= max_empty_receives:
                                raise CaptureIntegrityError("No samples within bounded receive attempts")
                            continue
                        empty = 0
                        accepted += take
                    except BaseException:
                        if not published:
                            pool.abort_producer(idx)
                        raise
                if tracker.status != "PASS":
                    raise CaptureIntegrityError("Hardware timestamps insufficient to certify continuity")
              finally:
                if started and streamer is not None:
                    try:
                        streamer.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))
                    except Exception as exc:
                        failure = failure or exc
                if pool is not None:
                    pool.close()
                if writer is not None:
                    writer.join()
                payload.flush()
                os.fsync(payload.fileno())
                events.flush()
                os.fsync(events.fileno())
    except BaseException as exc:
        failure = exc
    finally:
        if writer is not None and writer.failure is not None and failure is None:
            failure = writer.failure
        if writer is not None and isinstance(writer.failure, CaptureIntegrityError) and "Non-finite" in str(writer.failure):
            tracker.observe(0, None, error=True)
        if pool is not None:
            pool.discard_pending()
        for owned in ([path, events_path] if owns_payload else []):
            if owned.exists():
                try:
                    with owned.open("rb") as stream:
                        os.fsync(stream.fileno())
                except OSError as exc:
                    failure = failure or exc
        written = writer.written if writer is not None else 0
        if failure is None and written != accepted:
            failure = CaptureIntegrityError("Writer sample count diverged from the receive count")
        if failure is None and written != n:
            failure = failure or CaptureIntegrityError("Writer stored a different sample count")
        write_stats = {}
        if writer is not None and writer.write_s:
            latencies = np.asarray(writer.write_s, dtype=np.float64)
            quantiles = np.quantile(latencies, (0.5, 0.99))
            elapsed_write = float(latencies.sum())
            write_stats = {"write_latency_mean_s": float(latencies.mean()),
                           "write_latency_p50_s": float(quantiles[0]),
                           "write_latency_p99_s": float(quantiles[1]),
                           "write_latency_max_s": float(latencies.max()),
                           "writer_bytes": writer.nbytes,
                           "writer_throughput_Bps": (writer.nbytes / elapsed_write) if elapsed_write > 0 else None}
        meta.update({"finished_utc": _utc_now(), "elapsed_wall_s": time.monotonic() - t0,
                     "status": "complete" if failure is None else ("invalid" if isinstance(failure, CaptureIntegrityError) else "aborted"),
                     "valid": failure is None and tracker.status == "PASS" and written == n,
                     "n_samples": written, "retained_duration_s": written / fs,
                     "duration_rf_s": written / fs, "duration_rf_s_definition": "retained samples / driver rate, not acquisition span",
                     "timestamp_continuity": tracker.status,
                     "unknown_timestamp_blocks": tracker.unknown_blocks,
                     "discontinuities": tracker.discontinuities,
                     "queue_max_depth": None if pool is None else pool.max_depth,
                     "queue_saturated": None if pool is None else pool.saturated,
                     "block_samples": None if pool is None else len(pool.buffers[0]),
                     "error": str(failure) if failure else None,
                     "file_size_bytes": path.stat().st_size if owns_payload else None,
                     "sha256": sha256_file(path) if owns_payload else None,
                     "events_sha256": sha256_file(events_path) if events_path.exists() else None,
                     **write_stats})
        atomic_json(sidecar, meta)
    if failure is not None:
        raise failure
    print(f"[OK] {path} samples={written} timestamps=PASS (device time only)")
    return path, meta



def main():
    # Pre-parse --config / --defaults-from para aplicar defaults do YAML
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=str(ROOT / "configs" / "default.yaml"))
    pre.add_argument(
        "--defaults-from",
        choices=["ml", "fig1"],
        default="ml",
        help="Seção YAML para defaults de fs/gain/seconds (ml_capture | fig1)",
    )
    pre_args, _ = pre.parse_known_args()
    cfg = load_config(pre_args.config)
    radio = cfg.get("radio", {})
    section_key = "ml_capture" if pre_args.defaults_from == "ml" else "fig1"
    section = cfg.get(section_key, {})

    def_fc = float(radio.get("fc_hz", 3.750e9))
    def_fs = float(section.get("capture_fs_hz", 200e6))
    def_gain = float(section.get("capture_gain", 20.0))
    def_seconds = float(section.get("capture_seconds", 20.0))
    def_addr = str(section.get("addr", "") or "")
    def_mcr = section.get("master_clock_rate")
    if def_mcr is None:
        def_mcr = radio.get("master_clock_rate")

    ap = argparse.ArgumentParser(description=__doc__, parents=[pre])
    ap.add_argument("--case", choices=["A", "B", "C"], required=True)
    ap.add_argument("--seconds", type=float, default=def_seconds)
    ap.add_argument("--fc", type=float, default=def_fc, help="Freq. central watchdog (Hz)")
    ap.add_argument("--fs", type=float, default=def_fs, help="Taxa de amostragem (Hz)")
    ap.add_argument(
        "--master-clock-rate",
        type=float,
        default=None,
        help="X310 MCR: 184.32e6 ou 200e6 (default: YAML ou inferido de --fs)",
    )
    ap.add_argument("--gain", type=float, default=def_gain)
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--addr", type=str, default=def_addr, help="ex.: 192.168.40.2")
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument(
        "--split",
        choices=["train", "val", "test", ""],
        default="",
        help="Split pretendido (gravado no JSON; pastas train/val/test)",
    )
    ap.add_argument(
        "--purpose",
        type=str,
        default="",
        help="Override purpose no JSON (default: ml|fig1 conforme --defaults-from)",
    )
    ap.add_argument(
        "--recv-buff-size",
        type=int,
        default=230_000_000,
        help="Socket recv buffer (bytes) passado ao UHD",
    )
    ap.add_argument(
        "--num-recv-frames",
        type=int,
        default=1024,
        help="Número de frames RX no UHD (host ring)",
    )
    ap.add_argument(
        "--write-chunk-mib",
        type=float,
        default=64.0,
        help="Limite do bloco de recv, em MiB, com teto de 262144 amostras",
    )
    ap.add_argument(
        "--buffer-count",
        type=int,
        default=32,
        help="Blocos pré-alocados na fila entre recv e a escrita",
    )
    args = ap.parse_args()
    if args.master_clock_rate is None:
        if def_mcr is not None:
            args.master_clock_rate = float(def_mcr)
        else:
            args.master_clock_rate = infer_master_clock_hz(args.fs)

    try:
        import uhd
    except ImportError as e:
        raise SystemExit(
            "Pacote 'uhd' não encontrado. Instale drivers Ettus/UHD e binding Python."
        ) from e

    usrp = uhd.usrp.MultiUSRP(
        f"type=x300,addr={args.addr},master_clock_rate={args.master_clock_rate:.0f},"
        f"recv_buff_size={args.recv_buff_size},num_recv_frames={args.num_recv_frames},recv_frame_size=8000")
    usrp.set_rx_rate(args.fs, args.channel)
    usrp.set_rx_freq(uhd.types.TuneRequest(args.fc), args.channel)
    usrp.set_rx_gain(args.gain, args.channel)
    capture_iq(usrp, uhd, out_dir=Path(args.out), case=args.case,
               fs_requested=args.fs, seconds=args.seconds, channel=args.channel,
               config_path=Path(pre_args.config), write_chunk_mib=args.write_chunk_mib,
               buffer_count=args.buffer_count,
               metadata_extra={"fc_requested": args.fc, "gain_requested": args.gain,
                               "addr": args.addr, "recv_buff_size": args.recv_buff_size,
                               "num_recv_frames": args.num_recv_frames,
                               "master_clock_rate_requested": args.master_clock_rate,
                               "defaults_from": pre_args.defaults_from,
                               "split_intended": args.split or None,
                               "purpose": args.purpose or pre_args.defaults_from})


if __name__ == "__main__":
    main()
