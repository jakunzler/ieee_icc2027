"""Regression fixtures are synthetic; fake UHD never opens a radio."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import threading
import time
import numpy as np
import pytest

from ref_repro.capture.integrity import ContinuityTracker, audit_capture, metadata_tick, sha256_file
from ref_repro.capture.watchdog_capture import (BlockPool, CaptureIntegrityError, capture_iq,
                                                 require_block_sequence, write_cf32le)
from ref_repro.dsp.iq_io import load_iq, resolve_fs


class FakeStreamer:
    def __init__(self, trace):
        self.trace = iter(trace)
        self.commands = []

    def get_max_num_samps(self):
        return 4

    def issue_stream_cmd(self, command):
        self.commands.append(command.mode)

    def recv(self, buffer, metadata, timeout):
        item = next(self.trace)
        if isinstance(item, BaseException):
            raise item
        count, tick, code = item
        metadata.error_code = code
        metadata.has_time_spec = tick is not None
        metadata.time_spec = NS(to_ticks=lambda rate: tick)
        metadata.out_of_sequence = False
        buffer[:count] = np.arange(count) + 1 + 1j
        return count


def fixture_capture(tmp_path, trace, **kwargs):
    stream = FakeStreamer(trace)
    radio = NS(get_rx_rate=lambda channel: 1024, get_rx_stream=lambda args: stream)
    fake = NS(usrp=NS(StreamArgs=lambda *args: NS()),
              types=NS(RXMetadata=lambda: NS(), RXMetadataErrorCode=NS(none=0, overflow=1, timeout=2),
                       StreamCMD=lambda mode: NS(mode=mode),
                       StreamMode=NS(start_cont="start", stop_cont="stop")))
    return stream, lambda: capture_iq(radio, fake, out_dir=tmp_path, case="A",
                                      fs_requested=1024, seconds=12/1024, **kwargs)


def manifest(tmp_path):
    paths = list(tmp_path.glob("*.c64.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text()), Path(str(paths[0])[:-5])


def test_contiguous_bytes_samples_and_device_time(tmp_path):
    stream, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    path, meta = run()
    assert path.stat().st_size == 96 and len(load_iq(path)) == meta["n_samples"] == 12
    assert meta["status"] == "complete" and meta["valid"] is True
    assert meta["timestamp_continuity"] == "PASS"
    assert meta["clock_accuracy"] == "NOT_VERIFIED"
    assert meta["sha256"] == sha256_file(path)
    assert audit_capture(path)["eligible"]
    assert stream.commands == ["start", "stop"]


def test_timestamp_gap_rejected_not_concatenated(tmp_path):
    stream, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 8, 0), (4, 12, 0)])
    with pytest.raises(CaptureIntegrityError):
        run()
    meta, path = manifest(tmp_path)
    assert meta["discontinuities"] == 1 and meta["timestamp_continuity"] == "FAIL"
    assert path.stat().st_size == 32 and meta["n_samples"] == 4
    assert not audit_capture(path)["eligible"]
    assert stream.commands[-1] == "stop"


def test_overflow_marks_invalid_and_preserves_counter(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (0, 4, 1), (4, 8, 0)])
    with pytest.raises(CaptureIntegrityError):
        run()
    meta, path = manifest(tmp_path)
    assert meta["status"] == "invalid" and meta["valid"] is False
    assert meta["overflows"] == 1 and meta["n_samples"] == 4
    assert audit_capture(path)["time_continuity"] == "FAIL"


@pytest.mark.parametrize("error", [RuntimeError("SYNTHETIC_ABORT"), KeyboardInterrupt()])
def test_abort_retains_payload_and_manifest(tmp_path, error):
    stream, run = fixture_capture(tmp_path, [(4, 0, 0), error])
    with pytest.raises(type(error)):
        run()
    meta, path = manifest(tmp_path)
    assert meta["status"] == "aborted" and not meta["valid"]
    assert path.stat().st_size == 32 and meta["n_samples"] == 4
    assert meta["sha256"] == sha256_file(path)
    assert stream.commands[-1] == "stop"


def test_same_second_creates_unique_files(tmp_path, monkeypatch):
    monkeypatch.setattr("ref_repro.capture.watchdog_capture.time.strftime", lambda *a: "FIXED")
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    first, _ = run()
    original = first.read_bytes()
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    second, _ = run()
    assert first != second and first.read_bytes() == original
    _, run = fixture_capture(tmp_path, [], run_id=first.stem.rsplit("_", 1)[-1])
    with pytest.raises(FileExistsError):
        run()
    assert first.read_bytes() == original


def test_missing_timestamps_do_not_claim_continuity(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, None, 0), (4, None, 0), (4, None, 0)])
    with pytest.raises(CaptureIntegrityError, match="timestamps insufficient"):
        run()
    meta, path = manifest(tmp_path)
    assert meta["timestamp_continuity"] == "NOT_VERIFIED" and not meta["valid"]
    assert audit_capture(path)["time_continuity"] == "NOT_VERIFIED"


def test_empty_receive_bound(tmp_path):
    _, run = fixture_capture(tmp_path, [(0, None, 0)] * 3)
    with pytest.raises(CaptureIntegrityError, match="bounded"):
        run()
    meta, _ = manifest(tmp_path)
    assert meta["status"] == "invalid"


@pytest.mark.parametrize("tick", [-4, 0, 8])
def test_nonconsecutive_ticks_fail(tick):
    tracker = ContinuityTracker(1024)
    assert tracker.observe(4, 0) == "PASS"
    assert tracker.observe(4, tick) == "FAIL"


def test_float_timestamp_cannot_resolve_epoch_sample():
    meta = NS(has_time_spec=True, time_spec=NS(get_real_secs=lambda: 1_700_000_000.0))
    assert metadata_tick(meta, 200e6) is None


def test_incomplete_complex_sample_rejected(tmp_path):
    path = tmp_path / "synthetic.c64"
    path.write_bytes(np.array([1+2j], dtype="<c8").tobytes() + b"x")
    with pytest.raises(ValueError, match="Incomplete"):
        load_iq(path)


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), -1, 0])
def test_invalid_rate_rejected(rate):
    with pytest.raises(ValueError, match="positive and finite"):
        resolve_fs({"fs_effective": rate}, 120e6)
    with pytest.raises(ValueError):
        resolve_fs(None, rate)


def test_legacy_iq_time_not_invented(tmp_path):
    path = tmp_path / "synthetic.c64"
    np.ones(12, dtype="<c8").tofile(path)
    Path(str(path)+".json").write_text(json.dumps({"n_samples": 12, "fs_effective": 1024, "synthetic": True}))
    audit = audit_capture(path)
    assert audit["byte_integrity"] == audit["sample_integrity"] == "PASS"
    assert audit["time_continuity"] == audit["hash_integrity"] == "NOT_VERIFIED"
    assert not audit["eligible"]


def test_hash_detects_same_length_tampering(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    path, _ = run()
    data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(data)
    audit = audit_capture(path)
    assert audit["byte_integrity"] == "PASS" and audit["hash_integrity"] == "FAIL"
    assert not audit["eligible"]


def test_metadata_sample_counter_tampering_is_detected(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    path, meta = run()
    meta["n_samples"] = 999
    Path(str(path)+".json").write_text(json.dumps(meta))
    assert audit_capture(path)["sample_integrity"] == "FAIL"


def test_events_hash_tampering_is_detected(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    path, _ = run()
    events = Path(str(path)+".events.jsonl")
    events.write_text(events.read_text()+"\n")
    # A changed ledger cannot certify continuity, even before parsing it.
    result = audit_capture(path)
    assert result["hash_integrity"] == "FAIL" and not result["eligible"]


def test_ordered_blocks_keep_sample_boundaries(tmp_path):
    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)])
    path, meta = run()
    expected = np.concatenate([np.arange(4) + 1 + 1j] * 3).astype(np.complex64)
    np.testing.assert_array_equal(load_iq(path), expected)
    assert path.stat().st_size == 96
    assert meta["sha256"] == sha256_file(path)
    assert meta["pipeline"] == "bounded_producer_consumer"
    assert meta["queue_saturated"] == 0
    assert meta["queue_max_depth"] >= 1
    assert meta["writer_bytes"] == 96
    assert meta["writer_throughput_Bps"] > 0
    assert meta["write_latency_max_s"] >= meta["write_latency_mean_s"] >= 0
    events = [json.loads(line) for line in Path(str(path) + ".events.jsonl").read_text().splitlines()]
    assert [event["seq"] for event in events] == [0, 1, 2]
    assert [event["stored_index"] for event in events] == [0, 4, 8]


def test_buffer_cannot_be_reacquired_until_released():
    pool = BlockPool(1, 4)
    idx = pool.acquire()
    pool.buffers[idx][:] = 1
    pool.publish({"buf": idx, "seq": 0})
    assert pool.acquire() is None
    message = pool.take()
    assert pool.acquire() is None
    np.testing.assert_array_equal(pool.buffers[message["buf"]], np.ones(4, dtype="<c8"))
    pool.release_consumer(message["buf"])
    assert pool.acquire() == idx


def test_backpressure_fails_closed_without_dropping_silently(tmp_path):
    release = threading.Event()

    def unlock():
        time.sleep(0.2)
        release.set()

    threading.Thread(target=unlock, daemon=True).start()

    def on_block_write(info):
        if info["seq"] == 0:
            assert release.wait(2)

    _, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)],
                             buffer_count=1, on_block_write=on_block_write)
    with pytest.raises(CaptureIntegrityError, match="queue exhausted"):
        run()
    meta, path = manifest(tmp_path)
    assert meta["status"] == "invalid" and meta["queue_saturated"] >= 1
    assert path.stat().st_size == 32 and meta["n_samples"] == 4
    assert not audit_capture(path)["eligible"]
    assert not any(thread.name == "iq-writer" for thread in threading.enumerate())


def test_interrupted_write_keeps_prefix_and_stops(tmp_path):
    def on_block_write(info):
        if info["seq"] == 1:
            raise OSError("SYNTHETIC_DISK")

    stream, run = fixture_capture(tmp_path, [(4, 0, 0), (4, 4, 0), (4, 8, 0)],
                                  on_block_write=on_block_write)
    with pytest.raises(OSError, match="SYNTHETIC_DISK"):
        run()
    meta, path = manifest(tmp_path)
    assert meta["status"] == "aborted" and not meta["valid"]
    assert path.stat().st_size == 32 and meta["n_samples"] == 4
    assert meta["sha256"] == sha256_file(path)
    assert stream.commands[-1] == "stop"
    assert not any(thread.name == "iq-writer" for thread in threading.enumerate())


def test_pool_ownership_and_saturation():
    pool = BlockPool(1, 4)
    first = pool.acquire()
    assert first == 0 and pool.acquire() is None and pool.saturated == 1
    pool.abort_producer(first)
    second = pool.acquire()
    pool.publish({"buf": second, "seq": 0})
    message = pool.take()
    pool.release_consumer(message["buf"])
    with pytest.raises(RuntimeError, match="ownership"):
        pool.release_consumer(message["buf"])


def test_dropped_sequence_is_rejected():
    require_block_sequence(0, 0)
    with pytest.raises(CaptureIntegrityError, match="Dropped or reordered"):
        require_block_sequence(1, 3)


def test_non_finite_samples_are_not_stored(tmp_path):
    class NanStreamer(FakeStreamer):
        def recv(self, buffer, metadata, timeout):
            count = super().recv(buffer, metadata, timeout)
            if count:
                buffer[0] = np.nan
            return count

    stream = NanStreamer([(4, 0, 0)])
    radio = NS(get_rx_rate=lambda channel: 1024, get_rx_stream=lambda args: stream)
    fake = NS(usrp=NS(StreamArgs=lambda *args: NS()),
              types=NS(RXMetadata=lambda: NS(), RXMetadataErrorCode=NS(none=0, overflow=1, timeout=2),
                       StreamCMD=lambda mode: NS(mode=mode),
                       StreamMode=NS(start_cont="start", stop_cont="stop")))
    with pytest.raises(CaptureIntegrityError, match="Non-finite"):
        capture_iq(radio, fake, out_dir=tmp_path, case="A", fs_requested=1024, seconds=4/1024)
    meta, path = manifest(tmp_path)
    assert meta["status"] == "invalid" and meta["n_samples"] == 0
    assert path.stat().st_size == 0
    assert not audit_capture(path)["eligible"]
    assert stream.commands[-1] == "stop"


def test_short_write_is_rejected():
    class Short:
        def write(self, data):
            return 1

    with pytest.raises(CaptureIntegrityError, match="Short file write"):
        write_cf32le(Short(), np.ones(2, dtype="<c8"))


def test_explicit_byte_order_is_honored(tmp_path):
    path = tmp_path/"big_endian.c64"
    expected = np.array([1+2j, -3+.5j], dtype=np.complex64)
    expected.astype(">c8").tofile(path)
    Path(str(path)+".json").write_text(json.dumps({"datatype": "cf32_be"}))
    np.testing.assert_array_equal(load_iq(path), expected)
