"""Signing a release, and checking who signed it (`remote.md` §D7, M-14).

The tool is the machine's own `ssh-keygen`, so the tests generate a real key and
sign real bytes: what a hostile branch can do to a record is the thing being
tested, and a fake signer would test the fake.
"""

import subprocess
from pathlib import Path

import pytest

from camelot import sign

pytestmark = pytest.mark.skipif(
    not sign.available(), reason="signing a release needs OpenSSH 8.0 or later"
)

RECORD = b'{\n "format": "camelot.release/1",\n "number": 7\n}\n'


def _key(where: Path, name: str = "release") -> Path:
    """A real Ed25519 key pair, the way an operator makes one."""
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "camelot", "-f", str(where / name)],
        capture_output=True,
        check=True,
    )
    return where / name


def test_a_signature_checks_out_and_a_changed_byte_does_not(tmp_path):
    """§D7's exit criterion: the signature is what a server checks without
    trusting the branch, so a record that moved after signing has to be refused."""
    key = _key(tmp_path)
    signature = sign.sign(key, RECORD)

    assert signature.startswith(b"-----BEGIN SSH SIGNATURE-----")
    found = sign.verify(key.with_name("release.pub"), RECORD, signature)
    assert found["ok"] is True
    assert found["why"] == ""
    # the key_id `client-update.md` §D9 reserves, read from ssh-keygen's own line
    assert found["key"].startswith("SHA256:")

    changed = RECORD.replace(b'"number": 7', b'"number": 8')
    assert sign.verify(key.with_name("release.pub"), changed, signature) == {
        "ok": False,
        "key": None,
        "why": "Signature verification failed: incorrect signature",
    }


def test_another_key_does_not_check_out(tmp_path):
    """A signature proves *who* produced a file: the right bytes under the wrong
    key are still a refusal."""
    mine = _key(tmp_path, "mine")
    theirs = _key(tmp_path, "theirs")
    signature = sign.sign(theirs, RECORD)

    found = sign.verify(mine.with_name("mine.pub"), RECORD, signature)

    assert found["ok"] is False
    assert found["why"]


def test_a_signature_made_for_another_purpose_is_refused(tmp_path):
    """The namespace is part of the signed data, so a signature made elsewhere
    with the same key does not pass as a release's."""
    key = _key(tmp_path)
    elsewhere = sign.sign(key, RECORD, namespace="something-else")

    found = sign.verify(key.with_name("release.pub"), RECORD, elsewhere)

    assert found["ok"] is False
    assert "namespace" in found["why"]


def test_no_signature_is_a_fact_and_not_a_verdict(tmp_path):
    """An unsigned branch is not a broken one: the caller decides whether that is
    a refusal, which is why this is data rather than an exception."""
    key = _key(tmp_path)

    found = sign.verify(key.with_name("release.pub"), RECORD, b"")

    assert found == {"ok": False, "key": None, "why": "it carries no signature"}


def test_a_private_key_handed_in_is_named(tmp_path):
    """The mistake an operator makes once: pointing a server at the private key.
    `ssh-keygen` would answer "Could not verify signature", which sends them
    looking at the wrong thing."""
    key = _key(tmp_path)

    with pytest.raises(sign.SignError) as raised:
        sign.verify(key, RECORD, sign.sign(key, RECORD))

    assert "not a public key" in str(raised.value)


def test_signing_needs_a_key_that_is_there(tmp_path):
    with pytest.raises(sign.SignError) as raised:
        sign.sign(tmp_path / "nowhere", RECORD)

    assert "no key there" in str(raised.value)


def test_the_same_record_signs_to_the_same_bytes(tmp_path):
    """Ed25519 is deterministic and SSHSIG carries no timestamp, so a publish is
    reproducible: a second run of the same release makes the same blob, and a
    re-publish cannot churn the branch's bytes."""
    key = _key(tmp_path)

    assert sign.sign(key, RECORD) == sign.sign(key, RECORD)
