"""Tests for the KFA animation runtime (synthetic + corpus)."""

import os

from camelot.kfa import evaluate, load_kfa


def test_evaluate_empty_channel():
    from camelot.kfa import Channel

    assert evaluate(Channel(index=3), 0.0) == (None, None, None)


def test_load_kfa_boar(client_dir):
    anim = load_kfa(os.path.join(client_dir, "anims", "BOAR_idle.kfa"))
    # 35 channels (31 with data, 4 empty placeholders 74..77).
    assert len(anim.channels) == 35
    assert anim.duration > 0
    rot, trans, _scale = anim.sample(0, 0.0)
    assert rot is not None and len(rot) == 4
    assert trans is not None and len(trans) == 3
    # Empty placeholder samples to Nones.
    assert anim.sample(74, 0.0) == (None, None, None)
