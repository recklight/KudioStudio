# -*- coding: utf-8 -*-
"""Signals without Qt, behaving the way the Qt code that uses them expects."""
from __future__ import annotations

import gc

import pytest

from kudiostudio.events import Signal


class Source:
    happened = Signal(int)
    other = Signal()


class Receiver:
    def __init__(self):
        self.seen = []

    def on(self, value):
        self.seen.append(value)


def test_slots_run_in_order_with_the_arguments():
    source, seen = Source(), []
    source.happened.connect(lambda v: seen.append(("a", v)))
    source.happened.connect(lambda v: seen.append(("b", v)))
    source.happened.emit(3)
    assert seen == [("a", 3), ("b", 3)]


def test_each_instance_has_its_own_signal():
    first, second, seen = Source(), Source(), []
    first.happened.connect(seen.append)
    second.happened.emit(1)
    assert seen == []
    assert isinstance(Source.happened, Signal)


def test_a_connected_method_does_not_keep_its_object_alive():
    source, receiver = Source(), Receiver()
    source.happened.connect(receiver.on)
    source.happened.emit(1)
    assert receiver.seen == [1]
    del receiver
    gc.collect()
    source.happened.emit(2)            # no error, nothing left to call
    assert len(source.happened) == 0


def test_a_lambda_is_kept_however_it_was_made():
    source, seen = Source(), []

    def connect():
        source.happened.connect(lambda v: seen.append(v))

    connect()
    gc.collect()
    source.happened.emit(5)
    assert seen == [5]


def test_disconnect_one_or_all():
    source, receiver, seen = Source(), Receiver(), []
    source.happened.connect(receiver.on)
    source.happened.connect(seen.append)
    source.happened.disconnect(receiver.on)
    source.happened.emit(1)
    assert receiver.seen == [] and seen == [1]
    source.happened.disconnect()
    source.happened.emit(2)
    assert seen == [1]
    with pytest.raises(RuntimeError, match="not connected"):
        source.happened.disconnect(receiver.on)


def test_a_slot_on_a_deleted_qt_object_is_dropped_not_fatal():
    source, seen = Source(), []

    def deleted(value):
        raise RuntimeError("Internal C++ object (QLabel) already deleted.")

    source.happened.connect(deleted)
    source.happened.connect(seen.append)
    source.happened.emit(1)
    source.happened.emit(2)
    assert seen == [1, 2]
    assert len(source.happened) == 1


def test_any_other_error_in_a_slot_propagates():
    source = Source()

    def broken(value):
        raise RuntimeError("something else")

    source.happened.connect(broken)
    with pytest.raises(RuntimeError, match="something else"):
        source.happened.emit(1)


def test_the_session_needs_no_qt(tmp_path):
    """Imported in a fresh interpreter: the session alone loads no PySide6."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import kudiostudio.session\n"
            "print(sorted(m for m in sys.modules if m.startswith('PySide6')))"
            % str(root))
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=120, cwd=tmp_path)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "[]"
