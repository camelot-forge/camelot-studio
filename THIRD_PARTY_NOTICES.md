# Third-party notices

Camelot Studio is MIT-licensed (`LICENSE`). It uses the following third-party
work.

## Space Grotesk (font)

The `SpaceGrotesk.woff2` file is distributed under the **SIL Open Font License
1.1**. The full license is in `web/static/fonts/OFL.txt` and beside the copy
the launcher ships.

Copyright 2020 The Space Grotesk Project Authors
(https://github.com/floriankarsten/space-grotesk).

## Python packages

Installed from PyPI, not vendored:

- **pyffi** — BSD-3-Clause.
- **numpy** — BSD-3-Clause.
- **FastAPI**, **uvicorn**, **watchfiles**, **httpx** — MIT / BSD / Apache-2.0
  as declared by each package.
- **pymysql** — MIT.
- **pytest**, **ruff** — MIT.

## JavaScript packages

Installed from npm, not vendored:

- **Svelte**, **SvelteKit**, **Vite** — MIT.
- **three.js** — MIT.
- **@lucide/svelte** — ISC.

## OpenDAoC

The server stack pulls the OpenDAoC core image at deployment time and clones the
OpenDAoC database baseline at install time. Neither is part of this repository.
Their licenses are their own.

## Game assets

No Dark Age of Camelot client files, extracted tables, or original art are
distributed here. The tests read a client the user already owns, through
`DAOC_CLIENT`.
