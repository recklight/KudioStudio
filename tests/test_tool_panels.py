# -*- coding: utf-8 -*-
"""The tools as the window runs them: the call each one describes, and that
it really makes it -- effects, the analysis views, the levels, metrics,
record, synthesize and batch -- and the two plots, which node builds from
the page's own modules."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import threading

import numpy as np
import pytest

import kudio
from kudiostudio import i18n
from kudiostudio.tools import synth
from kudiostudio.tools.synth import parse_snr
from kudiostudio.web import analysis
from kudiostudio.web.server import STATIC
from kudiostudio.web.studio import Prefs, Studio


@pytest.fixture(autouse=True)
def english_afterwards():
    yield
    i18n.load(i18n.DEFAULT)


@pytest.fixture
def open_studio(tmp_path, monkeypatch):
    """A Studio with *path* open, or with nothing open."""
    monkeypatch.setattr(kudio, "play_audio", lambda y, rate=None, **_: None)
    made = []

    def make(path=None):
        s = Studio(prefs=Prefs(tmp_path / "window.json"), language="en")
        if path is not None:
            s.call("open_files", [[str(path)]])
        made.append(s)
        return s

    yield make
    for s in made:
        s.shutdown()


@pytest.fixture
def studio(open_studio, tone):
    return open_studio(tone)


def _said(studio):
    q = studio.events.subscribe()

    def drain():
        out = []
        while not q.empty():
            e = q.get_nowait()
            if e["type"] == "message":
                out.append(e["text"])
        return out

    return drain


# ------------------------------------------------------------------ effects

def test_effects_apply_gain_to_selection(studio):
    session = studio.session
    assert "kudio.gain(y, db=-6.0)" in studio.call("effects.call_text",
                                                   ["gain", {"db": -6.0}])

    studio.call("set_selection", [0.0, 0.5])
    before = session.y.copy()
    studio.call("effects.apply", ["gain", {"db": -6.0}])

    expected = before[:8000] * 10 ** (-6.0 / 20.0)
    assert np.allclose(session.y[:8000], expected, atol=1e-6)
    assert np.allclose(session.y[8000:], before[8000:])
    assert studio.call("state")["can_undo"]


def test_effects_trim_silence_shortens_clip(studio):
    session = studio.session
    padded = np.concatenate([np.zeros(4000, dtype=np.float32), session.y])
    session.set_audio(padded, session.sr)

    studio.call("effects.apply", ["trim_silence", {}])
    assert len(session.y) < len(padded)


def test_effects_split_reports_without_editing(studio):
    said = _said(studio)
    before = studio.session.y.copy()
    info = studio.call("effects.apply", ["split_on_silence", {}])

    assert np.allclose(studio.session.y, before)
    # the panel's readout says "N segment(s)" from this, and "—" for none
    assert info["segments"] >= 1
    assert any("segment" in m for m in said())


# ----------------------------------------------------------------- analysis

def test_every_view_names_the_kudio_call_it_makes_and_makes_it(studio):
    expected = {"stft": "kudio.STFT",
                "mel": "kudio.melspectrogram",
                "mfcc": "kudio.mfcc"}
    assert set(expected) == set(analysis.MODES)

    for mode, call in expected.items():
        assert call in studio.call("set_analysis", kwargs={"mode": mode})["call"]
        img, _ = studio.image()
        assert img.data.ndim == 2 and img.data.shape[0] > 1     # (frames, bins)
        assert img.y_max > 0


def test_mfcc_of_a_clip_shorter_than_its_coefficients_stays_upright():
    """kudio gives (1, frames, n_mfcc). Guessing the layout from which side
    is longer turns a clip of fewer frames than coefficients on its side."""
    sr = 8000
    y = (0.3 * np.sin(2 * np.pi * 300 * np.arange(sr) / sr)).astype(np.float32)
    img = analysis.image(y, sr, analysis.Settings(mode="mfcc", n_mfcc=20))
    frames = np.shape(kudio.mfcc(y, sr, n_mfcc=20))[1]
    assert frames < 20                      # the case a guess gets wrong
    assert img.data.shape == (frames, 20)
    assert img.y_max == 20


def test_changing_an_analysis_parameter_rescales_colour(studio):
    """Different n_fft is different data, not the same clip seen again."""
    levels = studio.image()[0].levels

    studio.call("set_analysis", kwargs={"n_fft": 2048})
    assert studio.image()[0].levels != pytest.approx(levels)


# -------------------------------------------------------------------- plots

#: Just enough of a browser to build both plots in node: canvases that have a
#: size, keep their listeners and record the rectangles filled on them, and
#: no animation frames, so nothing is drawn unless a test draws it. The two
#: plots share one TimeView over a 1 s clip, as in the window.
PLOTS = """
const sink = new Proxy(() => {}, {
  get: (_, key) => (key === Symbol.toPrimitive ? () => 0 : sink),
  apply: () => sink,
  set: () => true,
});
function canvas() {
  const on = {};
  const el = {
    clientWidth: 800, clientHeight: 200, width: 0, height: 0, style: {}, rects: [],
    addEventListener: (type, fn) => { on[type] = fn; },
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
    setPointerCapture() {},
    fire: (type, event) => on[type](event),
  };
  el.getContext = () => new Proxy({}, {
    get: (_, key) => (key === "fillRect" ? (...args) => el.rects.push(args) : sink),
    set: () => true,
  });
  return el;
}
globalThis.window = { devicePixelRatio: 1 };
globalThis.document = { documentElement: {}, createElement: canvas,
                        querySelector: () => ({ content: "" }) };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.ResizeObserver = class { observe() {} };
globalThis.requestAnimationFrame = () => 0;

const { TimeView } = await import(__VIEW__);
const { WaveformPlot } = await import(__WAVEFORM__);
const { SpectrogramPlot } = await import(__SPECTROGRAM__);

const seen = { select: [], selecting: [], seek: [] };
const hooks = {
  onSelect: (a, b) => seen.select.push([a, b]),
  onSelecting: (s) => seen.selecting.push([...s]),
  onSeek: (t) => seen.seek.push(t),
};
const view = new TimeView();
view.reset(1.0, 16000);
const wave = new WaveformPlot(canvas(), view, hooks);
const spec = new SpectrogramPlot(canvas(), view, hooks);
for (const plot of [wave, spec]) plot.resize();

// the pointer over *time* on *plot*, halfway down it
const at = (plot, time, button = 0) => ({ clientX: plot.timeToX(time), button, pointerId: 1,
                                          clientY: plot.area.y + plot.area.h / 2 });
const click = (plot, time, button = 0) => {
  plot.canvas.fire("pointerdown", at(plot, time, button));
  plot.canvas.fire("pointerup", at(plot, time, button));
};
const drag = (plot, from, to) => {
  plot.canvas.fire("pointerdown", at(plot, from));
  plot.canvas.fire("pointermove", at(plot, to));
  plot.canvas.fire("pointerup", at(plot, to));
};
// the stretch of time each rectangle *plot* fills covers, when it draws
const painted = (plot) => {
  plot.canvas.rects.length = 0;
  plot.draw();
  return plot.canvas.rects.map(([x, , w]) => [plot.xToTime(x), plot.xToTime(x + w)]);
};
// the stretch of time across *plot*'s drawing area
const shown = (plot) => [plot.xToTime(plot.area.x), plot.xToTime(plot.area.x + plot.area.w)];

__STEPS__
console.log(JSON.stringify(seen));
"""


def _plots(steps):
    """Build the waveform and the spectrogram in node and run *steps*, some
    JavaScript, on them. Returns what the steps put in `seen`."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    source = PLOTS.replace("__STEPS__", steps)
    for name in ("view", "waveform", "spectrogram"):
        uri = json.dumps((STATIC / "js" / f"{name}.js").as_uri())
        source = source.replace(f"__{name.upper()}__", uri)
    done = subprocess.run([node, "--input-type=module"], input=source, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_zoom_narrows_both_plots():
    """Both plots read one TimeView, which Zoom to selection and Zoom out set."""
    seen = _plots("""
view.set(0.4, 0.5);
seen.zoomed = [shown(wave), shown(spec)];
view.full();
seen.full = [shown(wave), shown(spec)];
""")
    for zoomed in seen["zoomed"]:
        assert zoomed == pytest.approx([0.4, 0.5])
    for full in seen["full"]:
        assert full == pytest.approx([0.0, 1.0])


def test_both_plots_show_the_selection_and_either_can_drag_it():
    """A zoomed spectrogram that does not show what is selected is useless.
    The page hands both plots every selection; each has to paint it."""
    seen = _plots("""
for (const plot of [wave, spec]) plot.setSelection([0.2, 0.6]);
seen.painted = [painted(wave), painted(spec)];
drag(spec, 0.1, 0.3);
""")
    for rects in seen["painted"]:
        assert len(rects) == 1
        assert rects[0] == pytest.approx([0.2, 0.6])
    assert seen["select"] == [pytest.approx([0.1, 0.3])]


def test_setting_the_selection_does_not_bounce_between_the_plots():
    seen = _plots("for (const plot of [wave, spec]) plot.setSelection([0.2, 0.6]);")
    assert seen["select"] == [] and seen["selecting"] == [], \
        "programmatic updates must stay silent"


def test_clicking_the_plot_area_reports_the_time_under_the_pointer():
    seen = _plots("""
click(wave, 0.3, 2);                  // the right button
wave.setSelection([0.5, 0.7]);
click(wave, 0.5);                     // on the selection's edge, which takes it
seen.before = [...seen.seek];
wave.setSelection(null);
click(wave, 0.3);
""")
    assert seen["before"] == []
    assert seen["seek"] == pytest.approx([0.3], abs=1e-3)


# ------------------------------------------------------------------- levels

def test_the_levels_are_measured_and_name_what_is_wrong(studio):
    levels = studio.call("levels")
    assert np.isfinite(levels["peak_dbfs"])
    assert np.isfinite(levels["lufs"])
    # a pure tone has nothing above 440 Hz, which is worth saying
    assert levels["problems"]


def test_with_no_audio_there_are_no_levels(open_studio):
    levels = open_studio().call("levels")
    assert levels["empty"] is True
    assert "peak_dbfs" not in levels


# ------------------------------------------------------------------ metrics

def test_metrics_scores_against_the_original(studio, monkeypatch):
    # PESQ, STOI and SDR run as a job of their own afterwards
    monkeypatch.setattr(kudio, "eval_metrics",
                        lambda sr, ref, deg, pesq_scale="lqo": (None, None, None))
    session = studio.session
    session.set_audio(session.y * 0.5, session.sr,
                      undo_label="gain", keep_original=True)
    info = studio.call("metrics.evaluate", ["Original (before edits)"])
    assert studio.jobs.wait(30)

    labels = [label for label, _ in info["rows"]]
    # the alignment is reported first, because a bad score from a misalignment
    # and a bad score from bad processing look identical and mean the opposite
    assert labels[0] == "Alignment"
    assert labels[1:4] == ["SNR", "SI-SDR", "Segmental SNR"]


def test_metrics_can_inspect_without_any_reference(open_studio, recording):
    info = open_studio(recording).call("metrics.inspect")

    rows = dict(map(tuple, info["rows"]))
    assert "Peak" in rows and "dBFS" in rows["Peak"]
    assert "Loudness (BS.1770)" in rows
    assert "Bandwidth" in rows
    assert info["inspect"] is not None
    assert "Nothing wrong" in info["inspect"][0]


def test_inspecting_an_upsampled_clip_names_the_problem(open_studio, recording):
    """The finding that quietly wastes half a dataset, surfaced in the panel."""
    studio = open_studio(recording)
    session = studio.session
    upsampled = kudio.resample(kudio.resample(session.y, session.sr, 8000), 8000,
                               session.sr)
    session.set_audio(upsampled, session.sr)

    text, _ = studio.call("metrics.inspect")["inspect"]
    assert "upsampled from 8000 Hz" in text


# ------------------------------------------------------------------- record

def _take(studio, monkeypatch, y, sr):
    """A fixed-length take that comes back as *y*."""
    monkeypatch.setattr(kudio, "record", lambda seconds, sr, channels, device: y)
    studio.call("record.record", ["fixed length", len(y) / sr, sr, 1, None])
    assert studio.jobs.wait(10)
    return studio.call("record.describe")["take"][0]


def test_a_clipped_take_is_reported_rather_than_accepted_quietly(studio, monkeypatch):
    said = _said(studio)
    clipped = np.clip(studio.session.y * 8.0, -1.0, 1.0).astype(np.float32)
    text = _take(studio, monkeypatch, clipped, studio.session.sr)

    assert "clipped" in text
    assert any("clipped" in m for m in said())


def test_a_clean_take_says_so(open_studio, recording, monkeypatch):
    studio = open_studio()
    y, sr = kudio.file_load(recording)
    text = _take(studio, monkeypatch, y, sr)
    assert "clean" in text
    assert "dBFS" in text


# --------------------------------------------------------------- synthesize

@pytest.mark.parametrize("text, expected", [
    ("-5, 0, 5", [-5, 0, 5]),
    ("0", [0]),
    ("3;6;9", [3, 6, 9]),
])
def test_parse_snr(text, expected):
    assert parse_snr(text) == expected


def test_parse_snr_rejects_empty():
    with pytest.raises(ValueError):
        parse_snr("  ")


@pytest.mark.parametrize("text, expected", [
    ("2.5", [2.5]),
    ("2.5, 2.7", [2.5, 2.7]),
    ("-2.5", [-2.5]),
    ("0.5;1.5", [0.5, 1.5]),
])
def test_parse_snr_keeps_a_fractional_snr(text, expected):
    """kudio 3.7.0 names 2.5 as `_2p5dB` and records 2.5 in the manifest, so
    there is nothing left to round. `int(float(chunk))` would make 2.5 into
    2, and "2.5, 2.7" into [2, 2] -- two SNRs that write one file name and
    overwrite each other."""
    assert parse_snr(text) == expected


@pytest.mark.parametrize("text", ["-5, 0, 5", "0", "3;6;9", "2.0", "-0.0"])
def test_a_whole_snr_stays_an_int_so_output_names_do_not_move(text):
    """A compatibility guard, not a style preference. kudio writes `_5dB` for
    5 and `_5dB` for 5.0 alike, but the call shown under the form is meant to
    be copy-pasteable, and `[-5.0, 0.0, 5.0]` is not what anyone typed."""
    assert all(isinstance(v, int) for v in parse_snr(text))


@pytest.mark.parametrize("text", ["nan", "inf", "-inf"])
def test_parse_snr_refuses_what_float_would_accept(text):
    """`float("nan")` succeeds, and an SNR of NaN would reach kudio and come
    back as a file called `_nandB`."""
    with pytest.raises(ValueError):
        parse_snr(text)


def _tiny_corpus(root, n_clean=3):
    sr = 16000
    clean, noise = root / "clean", root / "noise"
    clean.mkdir()
    noise.mkdir()
    for i in range(n_clean):
        t = np.arange(sr) / sr
        kudio.save_wave(clean / f"c{i}.wav",
                        (0.2 * np.sin(2 * np.pi * (180 + 40 * i) * t)
                         ).astype(np.float32), sr)
    kudio.save_wave(noise / "n0.wav",
                    (0.02 * np.random.default_rng(0).standard_normal(sr * 2)
                     ).astype(np.float32), sr)
    return clean, noise


def _synthesize(studio, clean, noise, out, snr, **settings):
    studio.call("synthesize.run", [str(clean), str(noise), str(out), snr], settings)
    assert studio.jobs.wait(60)
    return studio.call("synthesize.describe")["result"]


def test_two_fractional_snrs_reach_the_disk_as_two_files(open_studio, tmp_path):
    """Measured where it matters: on disk."""
    studio = open_studio()
    clean, noise = _tiny_corpus(tmp_path)
    out = tmp_path / "mix"
    said = _said(studio)
    line = _synthesize(studio, clean, noise, out, "2.5, 2.7", mode="inc")

    # six asked for and six on disk, or the status line would say otherwise
    assert line.startswith("6 file(s) written")
    assert said()[-1] == "Synthesis complete — 6 file(s)."

    names = sorted(p.name for p in out.glob("*.wav"))
    assert names == ["c0_n0_2p5dB.wav", "c0_n0_2p7dB.wav",
                     "c1_n0_2p5dB.wav", "c1_n0_2p7dB.wav",
                     "c2_n0_2p5dB.wav", "c2_n0_2p7dB.wav"]
    manifest = kudio.load_manifest(out / "manifest.json")
    assert sorted({p.snr_db for p in manifest}) == [2.5, 2.7]
    assert all(pathlib.Path(p.noisy).is_file() for p in manifest)


def test_the_panel_reports_the_disk_and_not_the_request(open_studio, tmp_path):
    """Asking for the same SNR twice is a user error, and the honest answer is
    the number of files that exist. Reporting the request would make a
    half-built corpus look complete -- and the manifest beside it would agree
    with the wrong number."""
    studio = open_studio()
    clean, noise = _tiny_corpus(tmp_path)
    said = _said(studio)
    line = _synthesize(studio, clean, noise, tmp_path / "mix", "2.5, 2.5", mode="inc")

    last = said()[-1]
    assert "3 file(s) written" in line
    assert "3 file(s), but 6 were asked for" in last
    assert "shared a name" in last


def test_the_run_uses_the_settings_it_started_with(open_studio, tmp_path, monkeypatch):
    """Through the real job, with the output folder changed mid-run. A run
    that read its settings as it went would put the mixtures in the first
    folder, manifest.json in the second, and name the second in its result."""
    studio = open_studio()
    clean, noise = _tiny_corpus(tmp_path)
    first, second = tmp_path / "run_A", tmp_path / "run_B"

    started, release = threading.Event(), threading.Event()
    real = kudio.Synthesizer.syn

    def held(self, *args, **kwargs):
        started.set()
        release.wait(30)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(kudio.Synthesizer, "syn", held)
    said = _said(studio)
    studio.call("synthesize.run", [str(clean), str(noise), str(first), "0"])
    assert started.wait(30)
    # what the page sends as the form changes, and Run pressed again
    studio.call("synthesize.call_text", [str(clean), str(noise), str(second), "0", "",
                                         0.0, "FLOAT", "none", False, "regular", None])
    studio.call("synthesize.run", [str(clean), str(noise), str(second), "0"],
                {"subtype": "FLOAT"})
    release.set()
    assert studio.jobs.wait(60)

    assert "A synthesis job is already running." in said()
    assert (first / "manifest.json").is_file()
    assert len(list(first.glob("*.wav"))) == 3
    assert not second.exists()
    assert f"written to {first}" in studio.call("synthesize.describe")["result"]
    assert all(pathlib.Path(p.noisy).parent == first
               for p in kudio.load_manifest(first / "manifest.json"))


def test_a_clean_run_does_not_mention_collisions(tmp_path):
    """The warning has to stay rare, or it stops being read."""
    clean, noise = _tiny_corpus(tmp_path)
    out = tmp_path / "mix"
    result = synth.synthesize(synth.SynthPlan(
        clean=str(clean), noise=str(noise), out=str(out),
        snr=tuple(parse_snr("-5, 0, 5")), mode="inc"))
    _, said = synth.result_text(result)

    assert result.wanted == result.on_disk == 9
    assert "asked for" not in said
    # and the integer names are the ones every earlier corpus already uses
    assert sorted(p.name for p in out.glob("*.wav"))[:3] == [
        "c0_n0_0dB.wav", "c0_n0_5dB.wav", "c0_n0_n5.wav"]


# -------------------------------------------------------------------- batch

def _batch(src="", dst="", **changes):
    settings = {"src": str(src), "dst": str(dst), "rate": "keep", "subtype": "PCM_16",
                "mono": True, "trim": False, "top_db": 30.0, "normalize": "none",
                "target": 0.0, "tolerance": 1.0, "ceiling": -1.0, "denoise": None,
                "noise": "mcra", "room": False, "rt60": 0.5, "drr_db": 0.0,
                "seed": 0, "room_dst": ""}
    settings.update(changes)
    return settings


def _run_batch(studio, settings):
    said = _said(studio)
    studio.call("batch.run", [settings])
    assert studio.jobs.wait(60)
    return said()[-1]


def test_batch_converts_a_folder(open_studio, tmp_path, tone):
    src = tmp_path / "in"
    src.mkdir()
    for i in range(3):
        y, sr = kudio.file_load(tone)
        kudio.save_wave(src / f"clip{i}.wav", y, sr)
    out = tmp_path / "out"

    said = _run_batch(open_studio(), _batch(src, out, rate="8000"))
    assert said == "Batch complete — 3/3 written, 0 failed."
    y, sr = kudio.file_load(next(out.glob("*.wav")))
    assert sr == 8000


def test_batch_can_normalize_loudness(open_studio, tmp_path, tone):
    """Peak normalisation says nothing about how loud a file sounds."""
    src = tmp_path / "in"
    src.mkdir()
    y, sr = kudio.file_load(tone)
    for i, gain in enumerate((0.0, -12.0)):          # two very different levels
        kudio.save_wave(src / f"clip{i}.wav", kudio.gain(y, gain), sr)
    out = tmp_path / "out"

    studio = open_studio()
    target = studio.call("batch.describe")["normalize"]["LUFS"][1]
    assert target == -23.0                            # EBU R 128 default
    settings = _batch(src, out, normalize="LUFS", target=target)
    assert "lufs=-23.0" in studio.call("batch.call_text", [settings])

    assert _run_batch(studio, settings).startswith("Batch complete — 2/2 written")
    for path in out.glob("*.wav"):
        clip, rate = kudio.file_load(path)
        assert kudio.loudness(clip, rate) == pytest.approx(-23.0, abs=0.5)


def test_batch_mirrors_subfolders(open_studio, tmp_path, tone):
    """Two files with the same name in different folders must not collide."""
    src = tmp_path / "in"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir(parents=True)
    y, sr = kudio.file_load(tone)
    kudio.save_wave(src / "a" / "clip.wav", y, sr)
    kudio.save_wave(src / "b" / "clip.wav", y, sr)
    out = tmp_path / "out"

    said = _run_batch(open_studio(), _batch(src, out))
    assert said == "Batch complete — 2/2 written, 0 failed."
    written = {p.relative_to(out).as_posix() for p in out.rglob("*.wav")}
    assert written == {"a/clip.wav", "b/clip.wav"}
