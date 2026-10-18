"""The players' entry point: the launcher and the feed, no Studio.

This script is the PyInstaller root (`packaging/launcher/build.py`). It imports
`camelot.launcher` and nothing else of ours, so the bundle carries the players'
half and the page — never the editor, the project or pyffi.
"""

from __future__ import annotations

import argparse
import sys

from camelot import launcher


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="camelot-launcher",
        description="the shard's launcher: a page, the feed, and the game",
    )
    parser.add_argument("--feed", help="the feed URL, overriding shard.json")
    parser.add_argument("--client", help="the game client's folder")
    parser.add_argument("--account", help="the account to fill in (never a password)")
    parser.add_argument("--server", help="the game's address, overriding the feed's")
    parser.add_argument("--settings", help="this launcher's own settings file")
    parser.add_argument("--shard", help="the distributed shard.json")
    parser.add_argument("--page", help="the built page (default: the packaged page)")
    parser.add_argument("--host", default="127.0.0.1", help="the interface")
    parser.add_argument("--port", type=int, default=8710, help="the port (default: 8710)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    try:
        settings = launcher.read_settings(args.settings)
        shard = launcher.read_shard(args.shard)
    except launcher.LauncherError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    settings = launcher.configure(
        settings,
        shard,
        feed=args.feed,
        server=args.server,
    )
    if args.client:
        settings["client"] = args.client
    if args.account:
        settings["account"] = args.account
    try:
        launcher.write_settings(settings, args.settings)
        return launcher.serve(
            settings,
            page=args.page or launcher.packaged_page(),
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
        )
    except (launcher.LauncherError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
