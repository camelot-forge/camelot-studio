# The players' launcher package

Builds the only binary a player receives: `camelot.feed`, `camelot.launcher`,
the page, and a `shard.json`.

## Build

```sh
.venv/bin/python -m pip install pyinstaller     # once
cd web/launcher && pnpm install && pnpm build   # the page, if it changed
cd ../.. && .venv/bin/python packaging/launcher/build.py \
  --name "Camelot Hills" \
  --feed "http://127.0.0.1:8080/" \
  --launcher "http://127.0.0.1:8090/launcher/" \
  --version 1
```

`--version` is the launcher's own version: a package carrying a greater one is
what replaces a player's (`deployment.md` §D7), so it only moves forward.
`--launcher` is optional. It names the directory where the zip and the
`launcher-manifest.json` are published; without it, the launcher has no
self-update.

Output:

```text
packaging/dist/camelot-launcher/
├── camelot-launcher(.exe)
├── _internal/
│   └── page/            # index.html and its assets
├── shard.json
└── version.txt
```

plus `packaging/dist/camelot-launcher-<platform>.zip` and
`packaging/dist/launcher-manifest.json`.

PyInstaller does not cross-compile: build the Windows package on Windows, the
Linux one on Linux. The build script uses the platform's `--add-data`
separator, so the same command works on both.

## `shard.json`

```json
{ "name": "Camelot Hills", "feed": "https://patch.example/" }
```

`feed` is the only URL the player's package needs; the game address comes from
the feed's `launcher.json` (`C §D15`), and an optional `server` pins one as a
fallback. `--feed` and `--server` override for a development run, as do
`CAMELOT_FEED_URL` and `CAMELOT_GAME_SERVER`.

## Its own update

A shard that publishes launcher packages adds a `launcher` URL:

```json
{ "name": "Camelot Hills", "feed": "https://patch.example/", "launcher": "https://patch.example/launcher/" }
```

That directory holds the zip `build.py` wrote and the `launcher-manifest.json`
beside it. The page reads the manifest, shows **Restart and update** when the
manifest's version is greater than `version.txt`'s, checks the SHA-256, and
stages the package in `<install parent>/update/<version>/`
(`deployment.md` §D6-§D8).

**Restart and update** writes `swap-launcher-<pid>.sh` (or `.bat`) beside the
install and stops the process. The script waits for the pid to disappear,
renames the install folder to `<name>.old-<version>`, moves the staged folder
into place, starts the new launcher with the same command line plus
`--no-open`, and removes the old folder. A failed move restores the old folder.
The page polls until the new process answers, then reloads.

## Run

```sh
cd packaging/dist/camelot-launcher
./camelot-launcher --client /path/to/the/client --account forge
```

The page opens on `http://127.0.0.1:8710/`. The client folder and account are
remembered in the player's config (`~/.config/camelot-launcher/settings.json`
on Linux); the password is never stored. `--no-open` is for a test harness.

On Linux the game runs under Wine; on Windows, `connect.exe` runs directly.
