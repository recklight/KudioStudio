# -*- coding: utf-8 -*-
"""End-to-end checks across kudio and KudioStudio, from one process.

Not a substitute for either unit suite -- this runs the combinations they do
not: real files on disk, non-default sample rates, minute-long feeds, the
`kudio` command line, and the whole window driving a real worker thread.

Two of this project's worst bugs were invisible to the unit tests and obvious
here: an `AudioConfig` field that stopped being true once `n_fft` changed, and
a windowed overlap-add whose tail divided by a near-zero window sum. Both were
end-to-end failures of things that were individually correct.

    ./.venv/Scripts/python.exe tools/smoke.py

Exits non-zero if any check fails, so it drops into a shell test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# the CLI prints middle dots and arrows; a cp950 console cannot show them
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np                                              # noqa: E402

import kudio                                                    # noqa: E402

CHECKS = {"ok": 0, "fail": 0}


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    CHECKS["ok" if condition else "fail"] += 1
    print(f"  [{mark}] {name}" + (f"   {detail}" if detail else ""))


def voice(sr: int, seconds: float, f0_hz: float, seed: int = 0) -> np.ndarray:
    """Harmonic bursts with pauses, over a noise floor."""
    rng = np.random.default_rng(seed)
    n = int(sr * seconds)
    t = np.arange(n) / sr
    harmonics = sum(np.sin(2 * np.pi * f0_hz * k * t) / k for k in range(1, 12))
    gate = ((t % 1.0) < 0.6).astype(np.float64)          # 0.6 s on, 0.4 s off
    y = harmonics / 6 * gate + 0.005 * rng.standard_normal(n)
    return (y / np.max(np.abs(y)) * 0.7).astype(np.float32)


# ---------------------------------------------------------------- streaming

def stream_checks(tmp: Path) -> None:
    print("\nSpectrogramStream")
    for sr in (8000, 16000, 44100):
        y = voice(sr, 2.0, 150.0)
        spec = kudio.SpectrogramStream(sr, seconds=10.0)
        whole = spec.push(y)

        piecemeal = kudio.SpectrogramStream(sr, seconds=10.0)
        rng = np.random.default_rng(1)
        i, parts = 0, []
        while i < len(y):                       # ragged blocks, like a device
            step = int(rng.integers(1, 5000))
            got = piecemeal.push(y[i:i + step])
            if len(got):
                parts.append(got)
            i += step
        check(f"sr={sr}: ragged blocks match one push",
              np.array_equal(np.concatenate(parts), whole),
              f"{len(whole)} columns")

    print("\n  against the offline transform")
    sr = 16000
    y = voice(sr, 1.5, 200.0)
    spec = kudio.SpectrogramStream(sr, n_fft=512, hop_length=256, seconds=10.0)
    live = spec.push(y)
    import librosa
    offline = np.abs(librosa.stft(y, n_fft=512, hop_length=256, window='hann',
                                  center=False)).T
    scale = 2.0 / np.sum(np.hanning(513)[:-1])
    want = np.maximum(20 * np.log10(np.maximum(offline * scale, 1e-10)),
                      spec.floor_db)
    err = float(np.max(np.abs(live[:, 1:-1] - want[:, 1:-1])))
    check("matches librosa center=False", err < 0.05, f"max |diff| {err:.4f} dB")

    print("\n  a long take does not drift or grow")
    spec = kudio.SpectrogramStream(16000, seconds=3.0)
    block = voice(16000, 0.1, 180.0)
    for _ in range(600):                                # 60 seconds of audio
        spec.push(block)
    check("window stays one width", spec.columns().shape ==
          (spec.n_columns, spec.n_bins), str(spec.columns().shape))
    check("filled saturates", spec.filled == spec.n_columns)
    want = (600 * len(block) - spec.n_fft) // spec.hop_length + 1
    check("every column of 60 s was computed", spec.frames_seen == want,
          f"{spec.frames_seen} of {want}")
    check("nothing non-finite", bool(np.all(np.isfinite(spec.columns()))))
    check("latest is the right edge",
          np.array_equal(spec.latest(), spec.columns()[-1]))

    print("\n  mel")
    mel = kudio.SpectrogramStream(16000, n_mels=64, fmin=50, fmax=7000)
    cols = mel.push(voice(16000, 1.0, 150.0))
    check("64 rows", cols.shape[1] == 64)
    check("bands ascend", bool(np.all(np.diff(mel.freqs) > 0)))
    check("within fmin..fmax", mel.freqs[0] >= 50 and mel.freqs[-1] <= 7000)


# ------------------------------------------------------------------- pitch

def pitch_checks(tmp: Path) -> None:
    print("\nkudio.f0")
    for sr in (8000, 16000, 44100):
        y = voice(sr, 3.0, 140.0)
        track = kudio.f0(y, sr)
        check(f"sr={sr}: finds 140 Hz",
              abs(track.median_hz - 140.0) < 4.0, f"{track.median_hz:.1f} Hz")
        check(f"sr={sr}: hop is 10 ms", track.hop_length == sr // 100)
        check(f"sr={sr}: times reach the end",
              abs(track.times[-1] - len(y) / sr) < 0.05,
              f"{track.times[-1]:.2f}s of {len(y) / sr:.2f}s")

    print("\n  the pauses are found")
    sr = 16000
    track = kudio.f0(voice(sr, 4.0, 140.0), sr)
    spans = track.voiced_segments()
    check("one span per burst", len(spans) == 4, f"{len(spans)} spans")
    check("spans are ~0.6 s",
          all(0.45 < b - a < 0.75 for a, b in spans),
          ", ".join(f"{b - a:.2f}" for a, b in spans))
    check("voiced ratio near 0.6",
          0.5 < track.voiced_ratio < 0.7, f"{track.voiced_ratio:.2f}")

    print("\n  through a telephone band, the fundamental is gone from the")
    print("  spectrum and still found")
    band = kudio.bandpass(voice(sr, 2.0, 120.0), sr, 300.0, 3400.0)
    check("still 120 Hz", abs(kudio.f0(band, sr).median_hz - 120.0) < 5.0,
          f"{kudio.f0(band, sr).median_hz:.1f} Hz")

    print("\n  labels round-trip through a file")
    path = tmp / "voiced.txt"
    kudio.save_labels(path, track.to_labels())
    back = kudio.load_labels(path)
    check("same number of labels", len(back) == len(spans))
    check("first label lands", abs(back[0].start - spans[0][0]) < 1e-3)


# --------------------------------------------------------------------- CLI

def cli_checks(tmp: Path) -> None:
    print("\nkudio pitch (CLI)")
    clip = tmp / "speech.wav"
    kudio.save_wave(clip, voice(16000, 3.0, 160.0), 16000)
    labels = tmp / "cli.txt"

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "pitch", str(clip),
         "--segments", "--labels", str(labels)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("exit 0", out.returncode == 0, out.stderr.strip()[:120])
    check("prints a median", "median 1" in out.stdout, out.stdout.splitlines()[0])
    check("lists spans", out.stdout.count("s)") >= 3)
    check("wrote labels", labels.exists() and len(kudio.load_labels(labels)) >= 3)

    silence = tmp / "silence.wav"
    kudio.save_wave(silence, np.zeros(16000, dtype=np.float32), 16000)
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "pitch", str(silence)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("silence exits 1", out.returncode == 1, out.stdout.strip())


# ----------------------------------------------------------------- loudness

def loudness_checks(tmp: Path) -> None:
    print("\nloudness over time")
    sr = 16000
    t = np.arange(int(sr * 24), dtype=np.float64) / sr
    # eight seconds loud, eight 14 dB down, eight loud again
    gate = np.where((t < 8) | (t >= 16), 1.0, 10 ** (-14 / 20))
    y = (0.5 * np.sin(2 * np.pi * 220 * t) * gate).astype(np.float32)

    curve = kudio.loudness_over_time(y, sr)
    loud_at, loud = curve.loudest()
    quiet_at, quiet = curve.quietest()
    check("the drop is found", abs((loud - quiet) - 14.0) < 1.0,
          f"{loud - quiet:.2f} LU")
    check("and it is in the middle", 8.0 < quiet_at < 16.0, f"{quiet_at:.2f}s")
    check("the loud part is not", loud_at < 8.0 or loud_at > 16.0,
          f"{loud_at:.2f}s")
    check("range agrees with the curve",
          abs(kudio.loudness_range(y, sr) - curve.range_lu) < 1e-9)

    print("\n  the quiet stretch as spans")
    spans = curve.spans_below((loud + quiet) / 2, min_seconds=1.0)
    check("one span", len(spans) == 1, str(spans))
    if spans:
        check("starting near 8 s", abs(spans[0][0] - 8.0) < 1.5,
              f"{spans[0][0]:.2f}s")

    print("\n  every rate agrees about the same sound")
    levels = []
    for rate in (8000, 16000, 44100, 48000):
        tt = np.arange(int(rate * 10), dtype=np.float64) / rate
        tone = (0.5 * np.sin(2 * np.pi * 220 * tt)).astype(np.float32)
        levels.append(kudio.loudness_over_time(tone, rate).max)
    check("within 0.3 LU across rates", max(levels) - min(levels) < 0.3,
          ", ".join(f"{v:.2f}" for v in levels))

    print("\n  true peak")
    n = np.arange(48000)
    inter = np.sin(2 * np.pi * 12000 * n / 48000 + np.pi / 4)   # fs/4, phased
    sample_peak = 20 * np.log10(np.max(np.abs(inter)))
    check("samples read -3 dBFS", abs(sample_peak + 3.01) < 0.02,
          f"{sample_peak:.2f} dBFS")
    check("the waveform reads ~0 dBTP",
          abs(kudio.true_peak(inter, 48000)) < 0.3,
          f"{kudio.true_peak(inter, 48000):.2f} dBTP")

    print("\n  through a file, via the CLI")
    clip = tmp / "varying.wav"
    kudio.save_wave(clip, y, sr)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "loudness", str(clip),
         "--target", "-23"], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("a missed target exits 1", out.returncode == 1,
          out.stdout.strip().splitlines()[-1] if out.stdout else "")
    for word in ("integrated", "range", "true peak", "short-term"):
        check(f"prints {word}", word in out.stdout)


# ------------------------------------------------------------------ channel

def channel_checks(tmp: Path) -> None:
    print("\nthe channel: companding, quantisation, packet loss")
    sr = 16000
    speech = voice(sr, 4.0, 150.0)

    def snr(ref, deg):
        n = min(len(ref), len(deg))
        ref, deg = np.asarray(ref[:n], float), np.asarray(deg[:n], float)
        return 10 * np.log10(np.sum(ref ** 2) /
                             (np.sum((ref - deg) ** 2) + 1e-30))

    print("\n  companding holds its SNR as the level falls; linear does not")
    normalised = speech / np.max(np.abs(speech))
    companded, linear = [], []
    for amp in (1.0, 0.5, 0.25, 0.125, 0.0625):
        quiet = (normalised * amp).astype(np.float32)
        companded.append(snr(quiet, kudio.mu_law(quiet)))
        linear.append(snr(quiet, kudio.bit_depth(quiet, 8)))
    check("mu-law is flat across 24 dB of level",
          max(companded) - min(companded) < 3.0,
          " ".join(f"{v:.1f}" for v in companded))
    check("linear 8-bit loses 6 dB a halving",
          abs((linear[0] - linear[-1]) - 24.0) < 3.0,
          " ".join(f"{v:.1f}" for v in linear))

    print("\n  quantisation noise is six decibels a bit")
    t = np.arange(48000) / 48000
    tone = (0.999 * np.sin(2 * np.pi * 997 * t)).astype(np.float32)
    for bits in (8, 12, 16):
        measured = snr(tone, kudio.bit_depth(tone, bits))
        check(f"{bits:>2} bits", abs(measured - (6.02 * bits + 1.76)) < 1.0,
              f"{measured:.2f} vs {6.02 * bits + 1.76:.2f} dB")

    print("\n  the loss rate asked for is the loss rate delivered")
    packets = int(np.ceil(len(speech) / int(0.02 * sr)))
    for loss in (0.01, 0.05, 0.2):
        _, gaps = kudio.dropouts(speech, sr, loss=loss, seed=0,
                                 return_spans=True)
        check(f"{loss:.0%}", len(gaps) == round(loss * packets),
              f"{len(gaps)} of {packets} packets")

    print("\n  whether concealment 'wins' is decided by phase, not by merit")

    def harmonic(f0):
        tt = np.arange(sr * 4) / sr
        return (sum(np.sin(2 * np.pi * f0 * k * tt) / k
                    for k in range(1, 10)) / 5).astype(np.float32)

    for f0, expect in ((150.0, "hold"), (133.0, "hole")):
        tone = harmonic(f0)
        silent = kudio.dropouts(tone, sr, loss=0.1, seed=2, conceal="silence")
        held = kudio.dropouts(tone, sr, loss=0.1, seed=2, conceal="hold")
        winner = "hold" if snr(tone, held) > snr(tone, silent) else "hole"
        check(f"f0={f0:g} Hz ({0.02 * f0:.2f} cycles a packet): {expect} wins",
              winner == expect,
              f"silence {snr(tone, silent):+.1f}  hold {snr(tone, held):+.1f} dB")
        check(f"f0={f0:g} Hz: the holes were filled either way",
              np.count_nonzero(held == 0.0) < np.count_nonzero(silent == 0.0))

    print("\n  bursts: the same packets, arranged so the holes are long")
    long_speech = np.tile(speech, 15)

    def runs_of(spans):
        lengths, current = [], 1
        for before, after in zip(spans, spans[1:]):
            if after[0] - before[1] < 1e-9:
                current += 1
            else:
                lengths.append(current)
                current = 1
        lengths.append(current)
        return lengths

    _, scattered = kudio.dropouts(long_speech, sr, loss=0.05, seed=7,
                                  return_spans=True)
    check("independent loss is not runs of one",
          abs(np.mean(runs_of(scattered)) - kudio.independent_burst(0.05)) < 0.1,
          f"{np.mean(runs_of(scattered)):.3f} vs "
          f"{kudio.independent_burst(0.05):.3f} packets")
    for want in (2.0, 6.0, 12.0):
        _, spans = kudio.dropouts(long_speech, sr, loss=0.05, burst=want,
                                  seed=7, return_spans=True)
        got = np.mean(runs_of(spans))
        check(f"burst={want:g} comes back", abs(got - want) / want < 0.25,
              f"{got:.2f} packets, longest {max(runs_of(spans))}")
        check(f"burst={want:g} still loses exactly 5%",
              len(spans) == len(scattered),
              f"{len(spans)} vs {len(scattered)} packets")

    check("and holding cannot help across a long one",
          snr(long_speech, kudio.dropouts(long_speech, sr, loss=0.05, burst=16,
                                          conceal="hold", seed=7))
          < snr(long_speech, kudio.dropouts(long_speech, sr, loss=0.05,
                                            burst=1, conceal="hold", seed=7)))

    print("\n  a phone line narrows the band and says so")
    phone = kudio.telephone(speech, sr)
    report = kudio.audio_report(phone, sr)
    check("length kept", len(phone) == len(speech))
    check("band-limited", report.band_limited)
    check("to about 3.4 kHz", 3000 < report.bandwidth_hz < 4200,
          f"{report.bandwidth_hz:.0f} Hz")

    print("\n  through files, via the CLI")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    src = tmp / "channel_in.wav"
    kudio.save_wave(src, speech, sr)
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "channel", str(src),
         str(tmp / "channel_out.wav"), "--telephone"], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("exit 0", out.returncode == 0, out.stderr.strip()[:100])
    check("reports the bandwidth it took away", "bandwidth" in out.stdout)
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "channel", str(src),
         str(tmp / "nothing.wav")], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("asking for nothing exits 1", out.returncode == 1)

    print("\n  the whole link in one call")
    for name in kudio.CHANNEL_PRESETS:
        out = kudio.apply_channel(speech, sr, name, seed=0)
        check(f"{name:<12} keeps the length and stays finite",
              len(out) == len(speech) and np.all(np.isfinite(out)),
              kudio.channel_tag(name) or "no link")
    check("nothing asked for is falsy rather than a dict of defaults",
          not kudio.channel_spec({"loss": 0.0, "bits": None}))


# ------------------------------------------------------- a corpus, end to end

def corpus_checks(tmp: Path) -> None:
    """The whole chain between a talker and a file, in one Synthesizer run.

        talker -> room -> + noise -> link -> file

    Every earlier section measures one of these on a buffer. This one is the
    only place they are all on disk at once, which is where the ordering and
    the bookkeeping have to actually agree.
    """
    print("\nthe corpus: room, noise and link, in that order")
    sr = 16000
    clean, noise = tmp / "corpus_clean", tmp / "corpus_noise"
    clean.mkdir(exist_ok=True)
    noise.mkdir(exist_ok=True)
    for index in range(3):
        kudio.save_wave(clean / f"c{index}.wav", voice(sr, 2.0, 120 + 20 * index), sr)
    kudio.save_wave(noise / "n.wav",
                    (0.05 * np.random.default_rng(0)
                     .standard_normal(sr * 3)).astype(np.float32), sr)

    out = tmp / "corpus_mixed"
    syx = kudio.Synthesizer(str(clean), str(noise), out_path=str(out),
                            snr_ratio=(0, 10), rt60=(0.4,),
                            channel="telephone", write_targets=True)
    produced = syx.syn(mode="inc", seed=5)
    pairs = syx.manifest()
    check("every mixture is on disk", bool(pairs) and all(p.exists() for p in pairs),
          f"{len(produced or [])} file(s)")
    check("the room is in the names", all("rt400ms" in p.noisy for p in pairs))
    check("the link is not — it is constant for the run",
          not any("telephone" in Path(p.noisy).name for p in pairs))
    check("the manifest carries it instead",
          all(p.channel == "telephone" for p in pairs))

    print("\n  the target is the room and nothing else")
    check("one target per mixture, named to match",
          all(Path(p.target).is_file()
              and Path(p.target).name == Path(p.noisy).name for p in pairs))
    wet, _ = kudio.file_load(pairs[0].target, sr=None, mono=True)
    mixed, _ = kudio.file_load(pairs[0].noisy, sr=None, mono=True)
    check("the mixture went down the line",
          kudio.audio_report(mixed, sr).band_limited,
          f"{kudio.audio_report(mixed, sr).bandwidth_hz:.0f} Hz")
    check("the target did not",
          not kudio.audio_report(wet, sr).band_limited,
          f"{kudio.audio_report(wet, sr).bandwidth_hz:.0f} Hz")

    print("\n  and each file gets its own room and its own packets")
    check("a room per output", len({r[2] for r in syx.rooms.values()}) == len(pairs))
    check("a loss pattern per output",
          len({seed for _, seed in syx.channels.values()}) == len(pairs))

    again = kudio.Synthesizer(str(clean), str(noise),
                              out_path=str(tmp / "corpus_again"),
                              snr_ratio=(0, 10), rt60=(0.4,),
                              channel="telephone", write_targets=True)
    again.syn(mode="inc", seed=5)
    check("a rerun rebuilds the same corpus",
          sorted(r[2] for r in again.rooms.values())
          == sorted(r[2] for r in syx.rooms.values()))


# --------------------------------------------------------------------- rooms

def room_checks(tmp: Path) -> None:
    print("\nrooms: build one, measure it back")
    for sr in (8000, 16000, 48000):
        for want in (0.3, 0.8):
            room = kudio.rt60(kudio.rir(sr, rt60=want, seed=0), sr)
            check(f"sr={sr:<6} rt60 {want}s round trips",
                  abs(room.t30 - want) / want < 0.06,
                  f"T30 {room.t30:.3f}s  fit {room.fit:.4f}")

    print("\n  distance is a knob with a unit")
    for want in (-3.0, 0.0, 6.0, 15.0):
        room = kudio.rt60(kudio.rir(16000, rt60=0.5, drr_db=want, seed=0), 16000)
        check(f"drr {want:+5.1f} dB comes back", abs(room.drr - want) < 0.1,
              f"{room.drr:+.2f} dB")

    print("\n  and it refuses to call things rooms that are not")
    for label, signal in (
            ("speech", voice(16000, 6.0, 150.0)),
            ("noise", (0.2 * np.random.default_rng(0)
                       .standard_normal(16000 * 3)).astype(np.float32)),
            ("truncated IR", kudio.rir(16000, rt60=1.0, seed=0)[:800])):
        room = kudio.rt60(signal, 16000)
        check(f"{label} is not believed", not room.reliable(),
              f"fit {room.fit:.3f}, edt/t30 {room.edt / room.t30:.2f}")

    print("\n  a room puts sound where there was none")
    dry = np.concatenate([voice(16000, 2.0, 150.0),
                          np.zeros(16000, dtype=np.float32)])
    wet = kudio.apply_rir(dry, kudio.rir(16000, rt60=0.8, seed=0),
                          normalise=False)
    gap = slice(int(2.05 * 16000), int(2.5 * 16000))
    check("the gap was silent", float(np.max(np.abs(dry[gap]))) == 0.0)
    check("and now is not", float(np.max(np.abs(wet[gap]))) > 1e-3,
          f"peak {float(np.max(np.abs(wet[gap]))):.4f}")
    check("the length is kept",
          len(kudio.apply_rir(dry, kudio.rir(16000, rt60=0.5, seed=0))) == len(dry))

    print("\n  and a dataset built in them")
    clean_dir, noise_dir = tmp / "syn_clean", tmp / "syn_noise"
    clean_dir.mkdir(exist_ok=True)
    noise_dir.mkdir(exist_ok=True)
    # speech then real silence: a tail is only visible against nothing
    sr = 16000
    t = np.arange(sr) / sr
    burst = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12)) / 6
    burst = (burst * np.hanning(sr)).astype(np.float32)
    kudio.save_wave(clean_dir / "c.wav",
                    np.concatenate([burst, np.zeros(sr, np.float32)]), sr)
    kudio.save_wave(noise_dir / "n.wav",
                    (1e-5 * np.random.default_rng(0)
                     .standard_normal(sr * 3)).astype(np.float32), sr)

    def tail_below_speech(path):
        y, rate = kudio.file_load(path, sr=None, mono=True)
        tail = y[int(1.0 * rate):int(1.5 * rate)]
        speech = y[int(0.2 * rate):int(0.8 * rate)]
        return 20 * np.log10((np.sqrt(np.mean(tail ** 2)) + 1e-20) /
                             (np.sqrt(np.mean(speech ** 2)) + 1e-20))

    tails = {}
    for want in (None, 0.3, 0.9, 2.0):
        out = tmp / f"syn_{want}"
        syx = kudio.Synthesizer(str(clean_dir), str(noise_dir), out_path=str(out),
                                snr_ratio=[60], rt60=want)
        produced = syx.syn(mode="reg", seed=1)
        check(f"rt60={want}: entries are still 4-tuples",
              all(len(e) == 4 for e in produced))
        tails[want] = tail_below_speech(sorted(out.rglob("*.wav"))[0])
        if want is not None:
            pairs = syx.manifest()
            check(f"rt60={want}: the manifest records it",
                  all(p.rt60 == want for p in pairs) and
                  all(f"rt{int(want * 1000)}ms" in p.noisy for p in pairs),
                  f"{Path(pairs[0].noisy).name}")

    check("a longer room leaves more behind",
          tails[None] < tails[0.3] < tails[0.9] < tails[2.0],
          "  ".join(f"{k}: {v:.1f}dB" for k, v in tails.items()))
    check("and no room leaves nothing", tails[None] < -60,
          f"{tails[None]:.1f} dB")

    print("\n  the same request rebuilds the same dataset")
    runs = []
    for run in ("a", "b"):
        out = tmp / f"syn_repeat_{run}"
        kudio.Synthesizer(str(clean_dir), str(noise_dir), out_path=str(out),
                          snr_ratio=[10], rt60=[0.4, 0.8]).syn(mode="reg", seed=7)
        runs.append([kudio.file_load(f, sr=None, mono=True)[0]
                     for f in sorted(out.rglob("*.wav"))])
    check("byte-identical",
          len(runs[0]) == len(runs[1]) > 0 and
          all(np.array_equal(a, b) for a, b in zip(*runs)),
          f"{len(runs[0])} files")

    print("\n  through files, via the CLI")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    ir_path = tmp / "room.wav"
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "room", "--make", str(ir_path),
         "--rt60", "0.7", "--drr", "6", "--seed", "0"], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("--make exits 0", out.returncode == 0, out.stderr.strip()[:100])
    check("and the room is the one asked for", "T30 0.70 s" in out.stdout,
          out.stdout.strip().splitlines()[-1] if out.stdout else "")

    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "room", str(ir_path)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("measuring it exits 0", out.returncode == 0)
    for field in ("T20", "T30", "EDT", "C50", "C80", "DRR"):
        check(f"prints {field}", field in out.stdout)

    speech_path = tmp / "room_speech.wav"
    kudio.save_wave(speech_path, voice(16000, 3.0, 150.0), 16000)
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "room", str(speech_path)], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("speech is refused as a room", out.returncode == 1,
          out.stdout.strip().splitlines()[0] if out.stdout else "")


# ---------------------------------------------------------------- alignment

def align_checks(tmp: Path) -> None:
    print("\nalignment, and what it is worth")
    sr = 16000
    clean = voice(sr, 6.0, 150.0)
    rng = np.random.default_rng(4)
    noisy = kudio.add_noise_snr(
        clean, rng.standard_normal(len(clean)).astype(np.float32),
        snr_db=5, seed=0)
    enhanced = kudio.spectral_enhance(noisy, sr, method="logmmse")

    for shift in (1, 16, 128, 800):
        delayed = np.concatenate([np.zeros(shift, dtype=np.float32), enhanced])
        found = kudio.find_delay(clean, delayed, sr)
        check(f"{shift:>4} samples found exactly", found.delay == shift,
              str(found))

        as_they_sit = kudio.si_sdr(clean, delayed[:len(clean)])
        a, b = kudio.align(clean, delayed, sr)
        check(f"{shift:>4} samples: aligning is worth something",
              kudio.si_sdr(a, b) > as_they_sit,
              f"{as_they_sit:+.2f} -> {kudio.si_sdr(a, b):+.2f} dB")

    print("\n  a delay measured once, applied to a set")
    found = kudio.find_delay(clean, np.concatenate(
        [np.zeros(300, dtype=np.float32), clean]), sr)
    others = [voice(sr, 3.0, f, seed=i) for i, f in enumerate((120.0, 200.0))]
    ok = all(np.allclose(*found.apply(
        o, np.concatenate([np.zeros(300, dtype=np.float32), o])), atol=1e-6)
        for o in others)
    check("the same delay lines up every file from that chain", ok)

    print("\n  it refuses two unrelated recordings")
    unrelated = rng.standard_normal(len(clean)).astype(np.float32)
    check("not confident", not kudio.find_delay(clean, unrelated, sr).confident())
    try:
        kudio.align(clean, unrelated, sr)
    except Exception as e:
        check("align() raises", "same recording" in str(e), str(e)[:60])
    else:
        check("align() raises", False, "it did not")

    print("\n  polarity is named, not silently fixed")
    flipped = kudio.find_delay(clean, -clean, sr)
    check("inverted is reported", flipped.inverted and flipped.strength > 0.99,
          str(flipped))

    print("\n  through files, via the CLI")
    ref_path, deg_path = tmp / "ref.wav", tmp / "deg.wav"
    kudio.save_wave(ref_path, clean, sr)
    kudio.save_wave(deg_path,
                    np.concatenate([np.zeros(128, dtype=np.float32), enhanced]), sr)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run(
        [sys.executable, "-m", "kudio.cli", "align", str(ref_path),
         str(deg_path), "--metrics", "--write-to", str(tmp / "aligned")],
        env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    check("exit 0", out.returncode == 0, out.stderr.strip()[:120])
    check("names the delay", "lags by 128 samples" in out.stdout)
    check("shows both scores", out.stdout.count(" dB") == 2, out.stdout)
    written = sorted((tmp / "aligned").glob("*.wav"))
    check("wrote the aligned pair", len(written) == 2,
          ", ".join(p.name for p in written))
    if len(written) == 2:
        sizes = {kudio.audio_info(p).frames for p in written}
        check("both the same length", len(sizes) == 1, str(sizes))


# ---------------------------------------------------------------- the app

def app_checks(tmp: Path) -> None:
    print("\nKudioStudio, driving the real widgets")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from kudiostudio.app import KudioStudio

    app = QApplication.instance() or QApplication(sys.argv[:1])

    def settle(ms=30):
        app.processEvents()
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        app.processEvents()

    clip = tmp / "app.wav"
    kudio.save_wave(clip, voice(16000, 3.0, 160.0), 16000)

    window = KudioStudio(reset=True)
    window.open_files([str(clip)])
    check("clip loaded", window.session.has_audio)

    pitch = next(p for p in window.panels if p.title == "Pitch")
    window.tools.setCurrentWidget(pitch)
    pitch._run()
    for _ in range(400):
        settle(20)
        if not pitch.jobs.busy and pitch._track is not None:
            break
    check("the worker finished", pitch._track is not None)
    check("median is right",
          abs(pitch._track.median_hz - 160.0) < 5.0,
          f"{pitch._track.median_hz:.1f} Hz")
    check("contour is on the spectrogram",
          window.analysis.spectrogram.overlay_visible)

    pitch._add_markers()
    check("markers added", len(window.session.markers) >= 3,
          f"{len(window.session.markers)} markers")

    window.analysis.mode.setCurrentIndex(window.analysis.MODE_MEL)
    check("hidden on the mel axis",
          not window.analysis.spectrogram.overlay_visible)
    window.analysis.mode.setCurrentIndex(window.analysis.MODE_STFT)
    check("back on the STFT axis", window.analysis.spectrogram.overlay_visible)

    # a real edit must drop it: it describes audio that no longer exists
    window.session.set_selection(0.5, 1.0)
    window.session.delete()
    settle()
    check("an edit drops the contour",
          not window.analysis.spectrogram.overlay_visible)
    check("and the panel knows", pitch._track is None)

    print("\n  a take, through the real signal path")
    record = next(p for p in window.panels if p.title == "Record")
    y, sr = kudio.file_load(str(clip), sr=None, mono=True)
    record.monitor_started.emit(sr)
    for i in range(0, len(y), 800):                 # 50 ms at a time
        record.monitor_chunk.emit(y[i:i + 800], sr)
    settle()
    stream = window.analysis._monitor_stream
    check("the view built a stream", stream is not None)
    expected = (len(y) - stream.n_fft) // stream.hop_length + 1
    check("every column computed once", stream.frames_seen == expected,
          f"{stream.frames_seen} vs {expected}")
    # a 3.00 s clip cannot fill a 3.008 s window (the window is a column
    # count, not a time), so what must hold is that nothing was dropped
    check("nothing dropped on the way in",
          stream.filled == min(stream.frames_seen, stream.n_columns),
          f"{stream.filled} of {stream.n_columns} columns held")

    record.monitor_stopped.emit()
    check("stopping releases the stream", window.analysis._monitor_stream is None)

    # committing a take must take the live picture down
    window.analysis.start_monitor(sr)
    window.analysis.feed_monitor(y[:8000], sr)
    window.session.set_audio(y, sr, name="take")
    settle()
    check("committing ends the live view", window.analysis._monitor_sr == 0)

    print("\n  the loudness curve reaches the waveform")
    analysis = window.analysis
    analysis.set_audio(y, sr)
    for _ in range(80):                     # the readout debounces onto a worker
        settle(30)
        if analysis._loudness is not None:
            break
    check("the readout handed one over", analysis._loudness is not None)
    check("true peak is on screen", "dBTP" in analysis.levels.true_peak.text(),
          analysis.levels.true_peak.text())
    check("range is on screen", "LU" in analysis.levels.range.text(),
          analysis.levels.range.text())

    analysis.show_loudness.setChecked(True)
    check("drawn when asked", analysis.waveform.loudness_visible)
    check("both plots reserve the gutter",
          analysis.spectrogram.plotItem.getAxis("right").isVisible())

    print("\n  and the two plots line up, with it on and with it off")
    for state in (True, False):
        analysis.show_loudness.setChecked(state)
        settle(80)
        wave = analysis.waveform.plotItem.vb.sceneBoundingRect()
        spec = analysis.spectrogram.plotItem.vb.sceneBoundingRect()
        check(f"curve {'on ' if state else 'off'}: same plot area",
              abs(wave.left() - spec.left()) < 1
              and abs(wave.right() - spec.right()) < 1,
              f"{wave.left():.1f}..{wave.right():.1f} vs "
              f"{spec.left():.1f}..{spec.right():.1f}")

    print("\n  crossfade, per edit")
    window.transport.crossfade.setValue(25.0)
    check("the bar writes the session", window.session.crossfade_ms == 25.0)
    messages = []
    window.session.message.connect(messages.append)
    window.session.set_selection(0.2, 0.4)
    window.session.delete(crossfade_ms=120.0)
    check("one edit can override", "120 ms" in messages[-1], messages[-1])
    check("without moving the default", window.session.crossfade_ms == 25.0)

    window.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        stream_checks(tmp)
        pitch_checks(tmp)
        loudness_checks(tmp)
        channel_checks(tmp)
        corpus_checks(tmp)
        room_checks(tmp)
        align_checks(tmp)
        cli_checks(tmp)
        app_checks(tmp)

    total = CHECKS["ok"] + CHECKS["fail"]
    print(f"\n{CHECKS['ok']}/{total} checks passed, {CHECKS['fail']} failed")
    return 1 if CHECKS["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
