# The players' launcher package

Builds the only binary a player receives: `camelot.feed`, `camelot.launcher`,
the page, and a `shard.json`. The Studio, the project and pyffi are not in it.

## Build

```sh
.venv/bin/python -m pip install pyinstaller     # once
cd web/launcher && pnpm install && pnpm build   # the page, if it changed
cd ../.. && .venv/bin/python packaging/launcher/build.py \
  --name "Camelot Hills" \
  --feed "http://127.0.0.1:8080/"
```

Output:

```text
packaging/dist/camelot-launcher/
├── camelot-launcher(.exe)
├── _internal/
│   └── page/            # index.html and its assets
└── shard.json
```

and `packaging/dist/camelot-launcher-<platform>.zip`.

PyInstaller does not cross-compile: build the Windows package on Windows, the
Linux one on Linux. The build script uses the platform's `--add-data`
separator, so the same command works on both.

## `shard.json`

```json
{ "name": "Camelot Hills", "feed": "https://patch.example/" }
```

`feed` is the only URL the player's package needs. The game address comes from
the feed's `launcher.json` (`C §D15`); an optional `server` is a fallback only.
`--feed` and `--server` override for a development run, as do `CAMELOT_FEED_URL`
and `CAMELOT_GAME_SERVER`.

## Run

```sh
cd packaging/dist/camelot-launcher
./camelot-launcher --client /path/to/the/client --account forge
```

The page opens on `http://127.0.0.1:8710/`. The client folder and account are
remembered in the player's config (`~/.config/camelot-launcher/settings.json`
on Linux); the password is never stored. `--no-open` is for a test harness.

On Linux the game runs under Wine; on Windows, `connect.exe` runs directly.
