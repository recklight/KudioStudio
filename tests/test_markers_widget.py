# -*- coding: utf-8 -*-
"""The marker list widget, and the markers drawn on the plots."""
import pytest

from kudiostudio.session import Marker
from kudiostudio.widgets import MarkerList
from kudiostudio.widgets.markers import format_span


def test_format_span_reads_as_a_point_or_a_range():
    assert format_span(12.5, 12.5) == "0:12.500"
    assert format_span(12.5, 14.0) == "0:12.500 – 0:14.000"
    assert format_span(75.0, 75.0) == "1:15.000"


def test_the_list_mirrors_the_session(qapp, session):
    widget = MarkerList(session)
    assert widget.list.count() == 0
    session.add_marker(0.5, 0.7, "noise")
    assert widget.list.count() == 1
    assert "noise" in widget.list.item(0).text()
    session.clear_markers()
    assert widget.list.count() == 0


def test_unnamed_markers_get_a_number(qapp, session):
    widget = MarkerList(session)
    session.add_marker(0.5)
    assert "marker 1" in widget.list.item(0).text()


def test_clicking_a_marker_asks_to_seek(qapp, session):
    widget = MarkerList(session)
    session.add_marker(0.42)
    seen = []
    widget.seek_requested.connect(seen.append)
    widget.list.setCurrentRow(0)
    widget._on_clicked(widget.list.item(0))
    assert seen == [pytest.approx(0.42)]


def test_double_clicking_a_range_asks_to_select_it(qapp, session):
    widget = MarkerList(session)
    session.add_marker(0.2, 0.6)
    seen = []
    widget.select_requested.connect(lambda a, b: seen.append((a, b)))
    widget.list.setCurrentRow(0)
    widget._on_double_clicked(widget.list.item(0))
    assert seen == [(pytest.approx(0.2), pytest.approx(0.6))]


def test_double_clicking_a_point_selects_nothing(qapp, session):
    widget = MarkerList(session)
    session.add_marker(0.2)
    seen = []
    widget.select_requested.connect(lambda a, b: seen.append((a, b)))
    widget.list.setCurrentRow(0)
    widget._on_double_clicked(widget.list.item(0))
    assert seen == []


def test_add_at_playhead(qapp, session):
    widget = MarkerList(session)
    widget.set_playhead(0.75)
    widget._add_at_playhead()
    assert session.markers[0].start == pytest.approx(0.75)


def test_mark_selection_needs_one(qapp, session):
    widget = MarkerList(session)
    messages = []
    widget.status.connect(messages.append)
    widget._mark_selection()
    assert session.markers == ()
    assert "Select a range" in messages[-1]


def test_vad_fills_the_list(qapp, real_session):
    widget = MarkerList(real_session)
    widget._from_vad()
    assert real_session.markers, "kudio.vad found nothing in the speech fixture"
    assert all(m.label.startswith("speech") for m in real_session.markers)


def test_vad_replaces_rather_than_appends(qapp, real_session):
    widget = MarkerList(real_session)
    widget._from_vad()
    first = len(real_session.markers)
    widget._from_vad()
    assert len(real_session.markers) == first


def test_vad_on_silence_says_so(qapp, session):
    """The tone fixture is a constant sine: nothing in it stands out."""
    import numpy as np
    session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="silence")
    widget = MarkerList(session)
    messages = []
    widget.status.connect(messages.append)
    widget._from_vad()
    assert session.markers == ()
    assert "no speech" in messages[-1]


def test_buttons_track_what_is_possible(qapp, session):
    widget = MarkerList(session)
    assert widget.mark_button.isEnabled() is False
    session.set_selection(0.1, 0.2)
    assert widget.mark_button.isEnabled() is True
    assert widget.remove_button.isEnabled() is False
    session.add_marker(0.5)
    widget.list.setCurrentRow(0)
    widget._sync_enabled()
    assert widget.remove_button.isEnabled() is True


def test_plots_draw_the_markers(qapp, session):
    from kudiostudio.widgets import AnalysisView

    view = AnalysisView()
    view.set_audio(session.y, session.sr)
    view.set_markers([Marker(0.2, 0.4, "range"), Marker(0.8, 0.8, "point")])
    assert len(view.waveform._marker_items) == 2
    assert len(view.spectrogram._marker_items) == 2

    view.set_markers([])
    assert view.waveform._marker_items == []


# ------------------------------------------------------- persistence (3.5.0)

def test_the_sidecar_sits_beside_the_audio(qapp, session):
    from kudiostudio.session import AudioSession
    assert AudioSession.sidecar_path("a/clip.wav").name == "clip.wav.labels.json"
    # not .labels.json replacing .wav -- two clips of different formats with the
    # same stem would share one file
    assert AudioSession.sidecar_path("a/clip.flac").name == "clip.flac.labels.json"


def test_markers_round_trip_through_the_sidecar(qapp, session, tone):
    session.add_marker(0.2, 0.5, "noise")
    session.add_marker(0.8, label="click")
    written = session.save_markers()
    assert written == session.sidecar_path(tone)
    assert written.is_file()

    session.clear_markers()
    assert session.load_markers(written) == 2
    assert [(m.start, m.end, m.label) for m in session.markers] == [
        (0.2, 0.5, "noise"), (0.8, 0.8, "click")]


def test_opening_a_file_picks_up_its_sidecar(qapp, tone):
    from kudiostudio.session import AudioSession

    first = AudioSession()
    first.load_file(tone)
    first.add_marker(0.3, 0.6, "kept")
    first.save_markers()

    later = AudioSession()
    later.load_file(tone)
    assert [m.label for m in later.markers] == ["kept"]


def test_opening_a_file_without_a_sidecar_is_silent(qapp, tone):
    from kudiostudio.session import AudioSession
    s = AudioSession()
    messages = []
    s.message.connect(messages.append)
    s.load_file(tone)
    assert s.markers == ()
    assert not any("marker" in m for m in messages)


def test_opening_a_second_file_drops_the_first_ones_markers(qapp, tone, recording):
    """They point at moments in audio that is no longer loaded."""
    from kudiostudio.session import AudioSession
    s = AudioSession()
    s.load_file(tone)
    s.add_marker(0.5, label="from the tone")
    s.load_file(recording)
    assert s.markers == ()


def test_a_broken_sidecar_says_so_rather_than_failing_silently(qapp, tone):
    from kudiostudio.session import AudioSession
    AudioSession.sidecar_path(tone).write_text("{not json", encoding="utf-8")
    s = AudioSession()
    messages = []
    s.message.connect(messages.append)
    s.load_file(tone)
    assert s.has_audio is True                     # the audio still loads
    assert any("could not be read" in m for m in messages)


def test_exporting_an_audacity_label_track(qapp, session, tmp_path):
    import kudio
    session.add_marker(0.25, 0.75, "speech")
    path = session.save_markers(tmp_path / "labels.txt")
    assert path.read_text(encoding="utf-8") == "0.250000\t0.750000\tspeech\n"
    assert kudio.load_labels(path) == [kudio.Label(0.25, 0.75, "speech")]


def test_save_needs_a_file_to_sit_beside(qapp, session):
    import numpy as np
    widget = MarkerList(session)
    session.set_audio(np.zeros(16000, dtype=np.float32), 16000, name="recording")
    session.add_marker(0.5)
    messages = []
    widget.status.connect(messages.append)
    widget._save()
    assert "Export" in messages[-1]


def test_the_file_buttons_track_what_is_possible(qapp, session, tone):
    widget = MarkerList(session)
    assert widget.save_button.isEnabled() is False        # no markers yet
    assert widget.export_button.isEnabled() is False
    session.add_marker(0.5)
    widget._sync_enabled()
    assert widget.save_button.isEnabled() is True
    assert widget.export_button.isEnabled() is True
