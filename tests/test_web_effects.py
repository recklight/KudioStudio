# -*- coding: utf-8 -*-
"""Effects in the window, and the parameter list the page builds its controls
from."""
from __future__ import annotations

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import effects
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)


@pytest.fixture
def studio(tmp_path, quiet, tone):
    s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
    s.call("open_files", [[str(tone)]])
    yield s
    s.shutdown()


def _messages(studio):
    q = studio.events.subscribe()

    def drain():
        out = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "message":
                out.append(e["text"])
        return out

    return drain


# ------------------------------------------------------ the parameter list

def _capped(p, sr):
    """A cutoff's default as the page offers it on a clip at *sr*: below
    Nyquist, which the 8 kHz low-pass would sit on at 16 kHz."""
    return min(p.default, sr / 2 - 1) if p.nyquist else p.default


def test_every_control_starts_where_the_list_says(studio):
    """The page builds each control from what `describe` sends, at its
    default and inside its bounds. A default outside them, or finer than the
    control's decimals, would be moved by the control before the first call,
    and the call on screen would not be the list's."""
    sr = studio.session.sr
    sent = studio.call("effects.describe")["params"]
    assert set(sent) == set(effects.EFFECTS)
    for name, listed in sent.items():
        assert listed == [p.as_dict() for p in effects.PARAMS[name]], name
        for p in effects.PARAMS[name]:
            if p.kind in ("float", "int"):
                top = min(p.max, sr / 2 - 1) if p.nyquist else p.max
                start = _capped(p, sr)
                assert p.min <= start <= top, (name, p.name)
                assert p.coerce(start) == start, (name, p.name)
            elif p.kind == "choice":
                assert p.default in p.choices, (name, p.name)
            elif p.kind == "bool":
                assert p.default in (True, False), (name, p.name)


#: what every effect's controls make before anything is touched, on a 16 kHz
#: clip with no noise file and no measured room picked
DEFAULT_CALLS = {
    "gain": "kudio.gain(y, db=0.0)",
    "random_gain": "kudio.random_gain(y, db_range=(-6.0, 6.0), seed=None)",
    "fade_in": "kudio.fade_in(y, sr, duration=0.01, shape='linear')",
    "fade_out": "kudio.fade_out(y, sr, duration=0.01, shape='linear')",
    "fade": "kudio.fade(y, sr, fade_in_s=0.01, fade_out_s=0.01, shape='linear')",
    "reverse": "kudio.reverse(y)",
    "remove_dc": "kudio.remove_dc(y)",
    "highpass": "kudio.highpass(y, sr, cutoff=80.0, order=4, zero_phase=True)",
    "lowpass": "kudio.lowpass(y, sr, cutoff=7999.0, order=4, zero_phase=True)",
    "bandpass": "kudio.bandpass(y, sr, low=300.0, high=3400.0, order=4)",
    "bandstop": "kudio.bandstop(y, sr, low=45.0, high=55.0, order=4)",
    "pitch_shift": "kudio.pitch_shift(y, sr, n_steps=2.0)",
    "time_stretch": "kudio.time_stretch(y, rate=1.0)",
    "reverb": "kudio.reverb(y, sr, decay=0.3, delay_ms=50.0)",
    "room": "kudio.apply_rir(y, kudio.rir(sr, rt60=0.5, drr_db=0.0, seed=0))",
    "measured_room": "kudio.apply_rir(y, kudio.file_load('…', sr=sr)[0])",
    "add_noise_snr": "kudio.add_noise_snr(y, noise='…', snr_db=5.0, seed=None)",
    "telephone": "kudio.telephone(y, sr, codec='mu_law')",
    "bit_depth": "kudio.bit_depth(y, bits=8, dither=False, seed=0)",
    "dropouts": ("kudio.dropouts(y, sr, loss=0.05, packet_ms=20.0, burst=None, "
                 "conceal='silence', seed=0)"),
    "trim_silence": ("kudio.trim_silence(y, top_db=30.0, frame_length=2048, "
                     "hop_length=512)[0]"),
    "vad_trim": "kudio.vad_trim(y, sr, threshold_db=8.0, pad=0.05)[0]",
    "split_on_silence": ("kudio.split_on_silence(y, top_db=30.0, frame_length=2048, "
                         "hop_length=512)"),
}


@pytest.mark.parametrize("name", effects.EFFECTS)
def test_each_effect_starts_from_its_default_call(studio, name):
    page = {p.name: _capped(p, studio.session.sr) for p in effects.PARAMS[name]
            if p.kind != "note"}
    assert studio.call("effects.call_text", [name, page]) == DEFAULT_CALLS[name]


def test_json_numbers_read_as_the_spin_boxes_hold_them():
    # a page sends 0 and -6 as integers; the call says 0.0 and -6.0, as the
    # control shows them
    assert effects.operation("gain", {"db": -6})[0] == "kudio.gain(y, db=-6.0)"
    assert effects.operation("gain", {"db": -6.04})[0] == "kudio.gain(y, db=-6.0)"
    assert effects.operation("highpass", {"order": 3.0})[0].count("order=3,") == 1
    assert "loss=0.123" in effects.operation("dropouts", {"loss": 12.3})[0]
    assert "burst=None" in effects.operation("dropouts", {"burst": 0})[0]
    with pytest.raises(ValueError, match="shape"):
        effects.operation("fade_in", {"shape": "sideways"})
    with pytest.raises(ValueError, match="unknown effect"):
        effects.operation("chorus", {})


def test_a_random_seed_is_pinned_only_when_applied():
    assert "seed=None" in effects.operation("random_gain", {})[0]
    pinned = effects.operation("random_gain", {}, exact=True)[0]
    assert "seed=None" not in pinned and "seed=" in pinned


def test_the_schema_is_what_the_page_builds_from():
    schema = effects.schema()
    assert schema["effects"] == list(effects.EFFECTS)
    cutoff = schema["params"]["lowpass"][0]
    assert cutoff == {**cutoff, "name": "cutoff", "kind": "float", "default": 8000.0,
                      "nyquist": True, "suffix": " Hz"}
    assert schema["not_previewable"] == ["split_on_silence"]


# ------------------------------------------------------------ the panel

def test_apply_changes_the_selection_only(studio):
    s = studio
    s.call("set_selection", [0.0, 0.5])
    before = s.session.y.copy()
    s.call("effects.apply", ["gain", {"db": -6}])
    expected = before[:8000] * 10 ** (-6.0 / 20.0)
    assert np.allclose(s.session.y[:8000], expected, atol=1e-6)
    assert np.allclose(s.session.y[8000:], before[8000:])
    assert "y = kudio.gain(y, db=-6.0)" in s.session.history[-1].code


def test_a_failure_is_reported_and_changes_nothing(studio):
    drain = _messages(studio)
    before = studio.session.y.copy()
    studio.call("effects.apply", ["add_noise_snr", {}])
    assert np.array_equal(studio.session.y, before)
    assert any("add_noise_snr failed" in m and "no noise file" in m for m in drain())


def test_noise_from_a_file_names_it_in_the_recorded_call(studio, tmp_path):
    noise = tmp_path / "noise.wav"
    kudio.save_wave(noise, (0.1 * np.random.default_rng(0).standard_normal(16000))
                    .astype(np.float32), 16000)
    studio.call("effects.apply", ["add_noise_snr", {"noise": str(noise), "seed": 3}])
    code = studio.session.history[-1].code
    assert f"kudio.file_load({str(noise.absolute())!r}, sr=sr)[0]" in code
    assert "seed=3" in code


def test_split_counts_the_segments_and_writes_them_out(studio, tmp_path):
    padded = np.concatenate([np.zeros(8000, np.float32), studio.session.y,
                             np.zeros(8000, np.float32), studio.session.y])
    studio.session.set_audio(padded, 16000, name="two")
    before = studio.session.y.copy()
    info = studio.call("effects.apply", ["split_on_silence", {}])
    assert info["segments"] == 2
    assert np.array_equal(studio.session.y, before)     # nothing modified
    out = tmp_path / "segments"
    out.mkdir()
    studio.call("effects.save_segments", [str(out)])
    assert sorted(p.name for p in out.glob("*.wav")) == ["two_001.wav", "two_002.wav"]


def test_new_audio_forgets_the_segments(studio):
    studio.call("effects.apply", ["split_on_silence", {}])
    q = studio.events.subscribe()
    studio.session.set_audio(np.zeros(16000, np.float32), 16000, name="new")
    assert studio.call("effects.measure_ir")["segments"] == 0
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert {"type": "panel", "panel": "effects",
            "info": studio.call("effects.measure_ir")} in events


def test_a_preview_renders_again_with_new_values(studio):
    s = studio
    assert s.call("effects.preview", ["gain", {"db": -6}]) == {"on": True}
    first = s._preview[0].copy()
    assert s.call("effects.preview", ["gain", {"db": -12}]) == {"on": True}
    assert s._preview[0].max() < first.max()
    assert s.call("state")["previewing"] == "effects"
    assert s.call("effects.preview", ["split_on_silence", {}]) == {"on": False}


def test_a_failed_preview_stops_instead_of_playing_the_old_one(studio):
    s = studio
    s.call("effects.preview", ["gain", {"db": -6}])
    assert s.call("effects.preview", ["add_noise_snr", {}]) == {"on": False}
    assert s.call("state")["previewing"] == ""


def _room(path, sr, delay=200):
    ir = np.zeros(sr // 2, np.float32)
    ir[delay] = 1.0
    ir[delay + 1:] = 0.3 * np.exp(-np.arange(len(ir) - delay - 1) / (0.05 * sr))
    kudio.save_wave(path, ir, sr)
    return path


def test_a_measured_room_is_read_at_the_clips_rate(studio, tmp_path):
    path = _room(tmp_path / "hall.wav", 48000)
    info = studio.call("effects.load_ir", [str(path)])
    assert info["ir"]["name"] == "hall.wav"
    assert "at 16000 Hz" in info["ir"]["measured"]
    assert "applied from there" in info["ir"]["measured"]
    text = studio.call("effects.call_text", ["measured_room", {"ir": str(path)}])
    assert text.startswith("kudio.apply_rir(y, kudio.file_load('hall.wav', sr=sr)[0][")
    unaligned = studio.call("effects.call_text", ["measured_room", {"align": False}])
    assert unaligned == "kudio.apply_rir(y, kudio.file_load('hall.wav', sr=sr)[0])"


def test_a_silent_response_leaves_the_loaded_one(studio, tmp_path):
    good = _room(tmp_path / "room.wav", 16000)
    studio.call("effects.load_ir", [str(good)])
    silent = tmp_path / "silent.wav"
    kudio.save_wave(silent, np.zeros(1000, np.float32), 16000)
    drain = _messages(studio)
    info = studio.call("effects.load_ir", [str(silent)])
    assert info["ir"]["name"] == "room.wav"
    assert drain() == ["silent.wav is silent — there is no room in it to apply."]


def test_the_response_is_read_again_when_the_rate_changes(studio, tmp_path):
    path = _room(tmp_path / "room.wav", 48000)
    studio.call("effects.load_ir", [str(path)])
    studio.session.set_audio(np.zeros(8000, np.float32), 8000, name="8k")
    assert "at 8000 Hz" in studio.call("effects.measure_ir")["ir"]["measured"]
