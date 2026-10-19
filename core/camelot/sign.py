"""Signing a release, and checking who signed it (`remote.md` §D7, M-14).

A checksum detects corruption; a signature proves *who produced a file*. The
hashes in `release.json` travel in the same commit as the artifacts they describe,
so they catch a half-copied or edited file and nothing more — a hostile
`camelot-deploy` branch rewrites both. A signature is what a server can check
without trusting the branch it pulled from.

The tool is `ssh-keygen -Y`. The desk, the container job and any server already
have OpenSSH; the operator already has an Ed25519 key; and the trust model is the
one they use for a deploy key — a public key they configured themselves. The
format is OpenSSH's SSHSIG, which is Ed25519 for an ed25519 key
(`client-update.md` §D9) and a text blob that travels in git unharmed.

What is signed is the **release record**, never the payloads: `release.json`
carries the sha256 of the pack, of `rows.json` and of the client patchlist, so
one signature covers the release and every file it names.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

__all__ = [
    "ENV_KEY",
    "NAMESPACE",
    "PRINCIPAL",
    "SIGNATURE",
    "SignError",
    "available",
    "configured",
    "key_id",
    "sign",
    "verify",
]

#: The signature's own name: `<file>.sig`, beside the file it signs. OpenSSH's
#: own convention, and what `ssh-keygen -Y verify -s` is pointed at.
SIGNATURE = ".sig"

#: Part of the signed data, so a signature made for another purpose does not
#: verify here — which is the point of naming one.
NAMESPACE = "camelot-release"

#: The name given to the public key when the operator hands us a `.pub` rather
#: than an `allowed_signers` file. A signature is checked against the *key*, so
#: the name only has to be the one both sides use.
PRINCIPAL = "camelot"

#: Where a job with no operator finds its key — the container job has an
#: environment, not a `machine.json` somebody edits.
ENV_KEY = "CAMELOT_RELEASE_KEY"

#: The first field of a public key line. Used to tell a `.pub` from the file
#: `ssh-keygen -Y verify` wants, so a private key handed in by mistake is named
#: rather than turned into "Could not verify signature".
KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)


class SignError(ValueError):
    """Anything signing or checking refuses, with the reason in the message."""


def available() -> bool:
    """Whether this machine's `ssh-keygen` can sign at all — OpenSSH 8.0 added
    `-Y`. The probe is the feature itself: run it wrong and it says what it
    wanted."""
    try:
        result = subprocess.run(
            ["ssh-keygen", "-Y", "sign"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return False
    return "namespace" in (result.stderr + result.stdout)


def _run(args, *, data: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["ssh-keygen", *args], input=data, capture_output=True, check=False
        )
    except FileNotFoundError as error:
        raise SignError(
            "ssh-keygen is not installed: signing a release needs OpenSSH 8.0 or later"
        ) from error


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    text = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
    return [line for line in text.splitlines() if line.strip()]


def configured(given=None, machine=None) -> str | None:
    """The key this machine was told to use: what was passed, then `machine.json`'s
    `release_key`, then `$CAMELOT_RELEASE_KEY`.

    The order the client folder and the author's name are resolved in
    (`project.md` §D9): what the operator passed beats what the machine says, and
    the environment is the last resort for a job that has no operator.
    """
    if given:
        return str(given)
    found = (machine or {}).get("release_key")
    if found:
        return str(found)
    return os.environ.get(ENV_KEY) or None


def sign(key, data: bytes, *, namespace: str = NAMESPACE) -> bytes:
    """The detached signature over *data*, by the key at *key*.

    *key* is a private key file, or the `.pub` of one held by `ssh-agent` — the
    two things `ssh-keygen -f` accepts, unchanged. The message goes in on stdin
    and the signature comes out on stdout, so nothing is written beside the record
    it signs and a second signature never meets an overwrite prompt.
    """
    where = Path(key).expanduser()
    if not where.is_file():
        raise SignError(
            f"{where}: no key there — `machine.json`'s release_key names this desk's own"
        )
    result = _run(["-Y", "sign", "-f", str(where), "-n", namespace, "-"], data=data)
    if result.returncode != 0:
        detail = _lines(result)
        raise SignError(
            f"ssh-keygen -Y sign: {detail[0] if detail else f'exited {result.returncode}'}"
        )
    return result.stdout


def verify(
    key,
    data: bytes,
    signature: bytes,
    *,
    namespace: str = NAMESPACE,
    principal: str = PRINCIPAL,
) -> dict:
    """Whether *signature* is *key*'s over *data*.

    The answer comes back as data — `{"ok", "key", "why"}` — because "nothing is
    configured" and "this signature does not check out" are different facts to a
    caller, and only one of them is a refusal. A broken setup (no key file, no
    ssh-keygen) still raises: that is an operator's mistake, not a verdict.

    *key* is the operator's **public** key (`.pub`), which is what a server is
    given. A private key handed in by mistake is named rather than reported as a
    failed signature.
    """
    if not signature:
        return {"ok": False, "key": None, "why": "it carries no signature"}
    line = _public_line(key)
    with tempfile.TemporaryDirectory(prefix="camelot-sign-") as scratch:
        allowed = Path(scratch) / "allowed_signers"
        allowed.write_text(f"{principal} {line}\n", encoding="utf-8")
        handle, where = tempfile.mkstemp(prefix="camelot-sign-", suffix=SIGNATURE)
        os.close(handle)
        try:
            Path(where).write_bytes(signature)
            result = _run(
                [
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed),
                    "-I",
                    principal,
                    "-n",
                    namespace,
                    "-s",
                    where,
                ],
                data=data,
            )
        finally:
            Path(where).unlink(missing_ok=True)
    if result.returncode == 0:
        return {"ok": True, "key": _key_id(result.stdout.decode("utf-8", "replace")), "why": ""}
    detail = _lines(result)
    return {
        "ok": False,
        "key": None,
        "why": detail[0] if detail else f"ssh-keygen exited {result.returncode}",
    }


def _public_line(key) -> str:
    """`<type> <base64>` out of the public key file *key* names."""
    where = Path(key).expanduser()
    if not where.is_file():
        raise SignError(
            f"{where}: no public key there — a server is configured with the "
            "operator's .pub, never with the private key"
        )
    for line in where.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if fields and fields[0] in KEY_TYPES:
            if len(fields) < 2:
                break
            return f"{fields[0]} {fields[1]}"
    raise SignError(
        f"{where}: not a public key — an `ssh-keygen -t ed25519` .pub is what this is "
        "pointed at"
    )


def _key_id(text: str) -> str | None:
    """The fingerprint `ssh-keygen` names in its own success line: the `key_id`
    `client-update.md` §D9 reserves, read rather than invented."""
    for token in text.split():
        if token.startswith("SHA256:"):
            return token
    return None


def key_id(key) -> str | None:
    """The fingerprint of the key at *key*, or None.

    `ssh-keygen -lf` answers for a private key and for a `.pub` alike, which is
    what lets a desk record who signed without holding the public key beside it.
    A fingerprint that cannot be read is not a refusal: it is a line the marker
    and the deploy log do without.
    """
    where = Path(key).expanduser()
    if not where.is_file():
        return None
    try:
        result = _run(["-l", "-f", str(where)])
    except SignError:
        return None
    if result.returncode != 0:
        return None
    return _key_id(result.stdout.decode("utf-8", "replace"))
