"""Build the players' launcher package: the two modules and the page.

    .venv/bin/python packaging/launcher/build.py --feed http://127.0.0.1:8080/

PyInstaller is the only extra tool. The result is one folder (`camelot-launcher`
with its `_internal/` and `shard.json`) and a zip beside it; no checkout, no
Python, and no pyffi are needed on a player's machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[2]
EXAMPLE = STUDIO / "packaging" / "launcher" / "shard.example.json"
DIST = STUDIO / "packaging" / "dist"
WORK = STUDIO / "packaging" / "build"
NAME = "camelot-launcher"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="build-launcher")
    parser.add_argument("--feed", help="the shard's public feed URL")
    parser.add_argument("--name", help="the shard's display name")
    parser.add_argument(
        "--launcher",
        help="the base URL the launcher's own packages are published under, ending with /",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=1,
        help="the launcher's own version: a package with a greater one replaces this one",
    )
    parser.add_argument("--python", default=sys.executable, help="the interpreter with PyInstaller")
    args = parser.parse_args(argv)

    probe = subprocess.run(
        [args.python, "-m", "PyInstaller", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        print(
            "error: PyInstaller is not installed — "
            f"{args.python} -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    launcher_url = str(args.launcher or "")
    if launcher_url and not launcher_url.endswith("/"):
        launcher_url += "/"
    shard = {
        "name": args.name or example["name"],
        "feed": args.feed or example["feed"],
    }
    if launcher_url:
        shard["launcher"] = launcher_url
    page = STUDIO / "web" / "launcher" / "dist"
    if not (page / "index.html").is_file():
        print(
            f"error: {page} is missing — build it (`cd web/launcher && pnpm build`)",
            file=sys.stderr,
        )
        return 1

    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    command = [
        args.python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        NAME,
        "--paths",
        str(STUDIO / "core"),
        "--add-data",
        f"{page}{os.pathsep}page",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK),
        "--specpath",
        str(WORK),
        str(STUDIO / "packaging" / "launcher" / "camelot_launcher.py"),
    ]
    subprocess.run(command, check=True, cwd=STUDIO)

    built = DIST / NAME
    (built / "shard.json").write_text(
        json.dumps(shard, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # The launcher's own version, beside the binary (`deployment.md` §D7): a
    # package with a greater one is what replaces this one.
    (built / "version.txt").write_text(f"{args.version}\n", encoding="utf-8")
    archive = shutil.make_archive(
        str(DIST / f"{NAME}-{sys.platform}"),
        "zip",
        root_dir=str(DIST),
        base_dir=NAME,
    )
    # The manifest the launcher fetches from the shard's `launcher` URL. Signed
    # by nothing: the hash binds the file it names, and the URL is the shard's
    # own (`deployment.md` §D7).
    payload = Path(archive).read_bytes()
    manifest = {
        "version": args.version,
        "file": Path(archive).name,
        "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }
    manifest_path = DIST / "launcher-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"package: {built}")
    print(f"archive: {archive}")
    print(f"manifest: {manifest_path}  (version {args.version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
