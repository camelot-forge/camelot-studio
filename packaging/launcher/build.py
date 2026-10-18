"""Build the players' launcher package: the two modules and the page.

    .venv/bin/python packaging/launcher/build.py --feed http://127.0.0.1:8080/

PyInstaller is the only extra tool. The result is one folder (`camelot-launcher`
with its `_internal/` and `shard.json`) and a zip beside it; no checkout, no
Python, and no pyffi are needed on a player's machine.
"""

from __future__ import annotations

import argparse
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
    shard = {
        "name": args.name or example["name"],
        "feed": args.feed or example["feed"],
    }
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
    archive = shutil.make_archive(
        str(DIST / f"{NAME}-{sys.platform}"),
        "zip",
        root_dir=str(DIST),
        base_dir=NAME,
    )
    print(f"package: {built}")
    print(f"archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
