# -*- coding: utf-8 -*-
"""The Record panel's open-ended mode, against a fake StreamRecorder.

There is no microphone in CI, so the recorder is replaced by one that hands
over whatever the test feeds it. What is under test is the panel: the two
device index spaces, the meter, the rolling monitor and the stop path.
"""
import numpy as np
import pytest

from kudiostudio.panels.record import MODE_FIXED, MODE_STREAM, RecordPanel
from kudiostudio.session import AudioSession


class FakeRecorder:
    instances = []

    def __init__(self, device=None, sr=16000, channels=1, **kwargs):
        self.sr = sr or 16000
        self.channels = channels
        self.device = device
        self.recording = False
        self.capped = False
        self._level = 0.0
        self._audio = np.zeros(0, dtype=np.float32)
        self._drained = 0
        FakeRecorder.instances.append(self)

    def start(self):
        self.recording = True
        return self

    def stop(self):
        self.recording = False
        return self._audio

    def take(self):
        return self._audio

    def level(self):
        return self._level

    def level_db(self):
        return 20 * np.log10(self._level) if self._level > 0 else float("-inf")

    def elapsed(self):
        return len(self._audio) / self.sr

    def tail(self, seconds):
        return self._audio[-int(seconds * self.sr):]

    def drain(self):
        new, self._drained = self._audio[self._drained:], len(self._audio)
        return new

    # -- test helper
    def feed(self, y, level=0.5):
        self._audio = np.concatenate([self._audio,
                                      np.asarray(y, dtype=np.float32)])
        self._level = level


@pytest.fixture
def panel(qapp, monkeypatch):
    import kudio
    FakeRecorder.instances = []
    monkeypatch.setattr(kudio, "StreamRecorder", FakeRecorder)
    return RecordPanel(AudioSession())


def test_fixed_mode_is_the_default(panel):
    assert panel.mode.currentText() == MODE_FIXED
    assert panel.seconds.isEnabled() is True
    assert "kudio.record(" in panel.call.text()


def test_switching_to_stream_mode_changes_the_call_and_the_index_space(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    assert panel.seconds.isEnabled() is False
    assert "kudio.StreamRecorder(" in panel.call.text()
    # the two backends number devices independently; the label has to say which
    assert "PyAudio" in panel.device_space.text()
    panel.mode.setCurrentText(MODE_FIXED)
    assert "sounddevice" in panel.device_space.text()


def test_start_opens_a_recorder_and_the_button_becomes_stop(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    assert panel._recorder is not None
    assert panel.record_button.text().endswith("Stop")
    assert panel.mode.isEnabled() is False


def test_stop_commits_the_take_to_the_session(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    panel._recorder.feed(np.full(8000, 0.2, dtype=np.float32))
    panel._record()                          # the same button, now Stop

    assert panel._recorder is None
    assert panel.session.has_audio is True
    assert len(panel.session.y) == 8000
    assert panel.record_button.text().endswith("Start")
    assert panel.mode.isEnabled() is True


def test_an_empty_take_is_not_committed(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    messages = []
    panel.status.connect(messages.append)
    panel._record()
    panel._record()
    assert panel.session.has_audio is False
    assert "Nothing was captured" in messages[-1]


def test_the_meter_follows_the_level(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    panel._recorder.feed(np.zeros(1600, dtype=np.float32), level=0.5)
    panel._on_meter_tick()
    assert panel.meter.value() == pytest.approx(-6, abs=1)
    assert panel.elapsed.text().startswith("0.10")


def test_silence_pins_the_meter_at_the_floor(panel):
    from kudiostudio.panels.record import METER_FLOOR_DB
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    panel._recorder.feed(np.zeros(160, dtype=np.float32), level=0.0)
    panel._on_meter_tick()
    assert panel.meter.value() == int(METER_FLOOR_DB)


def test_arriving_audio_is_handed_over_once_and_only_once(panel):
    """The display accumulates what it is given, so a sample sent twice is
    drawn twice at two different times -- and anything doing per-column
    analysis downstream computes it twice as well."""
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()

    seen = []
    panel.monitor_chunk.connect(lambda y, sr: seen.append((y, sr)))
    panel._recorder.feed(np.full(1600, 0.3, dtype=np.float32))
    panel._on_meter_tick()
    panel._recorder.feed(np.full(800, 0.3, dtype=np.float32))
    panel._on_meter_tick()
    panel._on_meter_tick()                    # nothing new arrived

    assert [len(y) for y, _ in seen] == [1600, 800]
    assert {sr for _, sr in seen} == {16000}


def test_a_take_announces_its_rate_before_any_audio(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    started, stopped = [], []
    panel.monitor_started.connect(started.append)
    panel.monitor_stopped.connect(lambda: stopped.append(True))

    panel._record()
    assert started == [16000]
    assert stopped == []

    panel._recorder.feed(np.full(1600, 0.2, dtype=np.float32))
    panel._record()                           # stop
    assert stopped == [True]


def test_hitting_the_length_cap_stops_the_take(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    messages = []
    panel.status.connect(messages.append)
    panel._record()
    panel._recorder.feed(np.full(4000, 0.1, dtype=np.float32))
    panel._recorder.capped = True
    panel._on_meter_tick()
    assert panel._recorder is None
    assert any("cap" in m for m in messages)


def test_a_device_that_will_not_open_is_reported(panel, monkeypatch):
    import kudio

    def explode(**kwargs):
        raise RuntimeError("no input device")

    monkeypatch.setattr(kudio, "StreamRecorder", explode)
    panel.mode.setCurrentText(MODE_STREAM)
    messages = []
    panel.status.connect(messages.append)
    panel._record()
    assert panel._recorder is None
    assert "no input device" in messages[-1]


def test_shutdown_closes_an_open_take(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    recorder = panel._recorder
    panel.shutdown()
    assert recorder.recording is False
    assert panel._recorder is None


def test_a_stereo_take_is_mixed_down_for_the_session(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel._record()
    panel._recorder.channels = 2
    panel._recorder._audio = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    panel._record()
    assert panel.session.y.ndim == 1
    assert np.allclose(panel.session.y, [0.5, 0.5])


# ------------------------------------------------- live monitoring (kudio 3.5)

def test_drain_never_returns_the_same_audio_twice(panel):
    """tail() overlaps between calls; feeding that to a recursive denoiser
    processes the same samples repeatedly and wrecks its state."""
    import kudio
    from kudio.core.stream import StreamRecorder

    # the real class, driven through the fake reader the fixture installs
    rec = StreamRecorder.__new__(StreamRecorder)
    rec._chunks, rec._frames, rec._drained = [], 0, 0
    rec._channels, rec._sr, rec._capped, rec._level = 1, 16000, False, 0.0
    import threading
    rec._lock = threading.Lock()

    assert rec.drain().size == 0
    rec._chunks.append(np.arange(10, dtype=np.float32))
    assert np.array_equal(rec.drain(), np.arange(10))
    assert rec.drain().size == 0                     # nothing new
    rec._chunks.append(np.arange(10, 20, dtype=np.float32))
    assert np.array_equal(rec.drain(), np.arange(10, 20))


def test_monitor_off_by_default(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    assert panel.monitor_method.currentData() is None
    assert panel._enhancer is None


def test_turning_on_monitoring_builds_a_stream_enhancer(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("logmmse"))
    panel._record()
    assert panel._enhancer is not None
    assert panel._enhancer.method == "logmmse"
    assert panel._enhancer.sr == panel._recorder.sr


def test_the_monitor_shows_the_enhanced_stream(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("wiener"))
    panel._record()

    rng = np.random.default_rng(0)
    noise = (0.2 * rng.standard_normal(16000)).astype(np.float32)
    panel._recorder.feed(noise)
    seen = []
    panel.monitor_chunk.connect(lambda y, sr: seen.append(y))
    for _ in range(8):
        panel._on_meter_tick()

    assert seen, "nothing was drawn"
    assert all(np.all(np.isfinite(y)) for y in seen)
    drawn = np.concatenate(seen)
    # what reaches the display is the denoised stream, not the microphone
    assert float(np.sqrt(np.mean(drawn ** 2))) < float(np.sqrt(np.mean(noise ** 2)))


def test_a_denoiser_that_gives_up_keeps_the_display_alive(panel):
    """A monitor that stops updating looks like a dead microphone."""
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("wiener"))
    panel._record()

    def explode(block):
        raise RuntimeError("nope")

    panel._enhancer.process = explode
    seen = []
    panel.monitor_chunk.connect(lambda y, sr: seen.append(y))
    panel._recorder.feed(np.full(1600, 0.2, dtype=np.float32))
    panel._on_meter_tick()

    assert panel._enhancer is None
    assert len(seen) == 1 and len(seen[0]) == 1600      # the raw block got through
    assert "see the log" in panel.monitor_result.text()


def test_monitoring_reports_how_much_it_is_removing(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("logmmse"))
    panel._record()
    rng = np.random.default_rng(1)
    panel._recorder.feed((0.2 * rng.standard_normal(16000)).astype(np.float32))
    for _ in range(8):
        panel._on_meter_tick()
    assert "dB" in panel.monitor_result.text()


def test_the_take_is_committed_raw_even_while_monitoring(panel):
    """Monitoring is monitoring — the same rule the A/B follows."""
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("logmmse"))
    panel._record()
    raw = (0.2 * np.random.default_rng(2).standard_normal(8000)).astype(np.float32)
    panel._recorder.feed(raw)
    panel._on_meter_tick()
    panel._record()                                   # stop

    assert np.allclose(panel.session.y, raw, atol=1e-6)


def test_stopping_releases_the_enhancer(panel):
    panel.mode.setCurrentText(MODE_STREAM)
    panel.monitor_method.setCurrentIndex(panel.monitor_method.findData("wiener"))
    panel._record()
    panel._recorder.feed(np.zeros(4000, dtype=np.float32))
    panel._record()
    assert panel._enhancer is None


def test_only_streamable_methods_are_offered(panel):
    import kudio
    offered = {panel.monitor_method.itemData(i)
               for i in range(panel.monitor_method.count())} - {None}
    assert offered == set(kudio.STREAMABLE_METHODS)
    assert "specsub" not in offered              # cannot stream
