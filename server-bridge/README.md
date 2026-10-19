# server-bridge — the server side of a deploy

- **`camelot-installer/`** — the Installer (`server-install.md` §D10, §D17): a C#
  plugin in the server's script folder, discovered through `IDatabaseUpdater` and
  run at boot. It honours the Console's ask, applies what Deploy handed over,
  records it, and reverses it.
- **the local test server** — provisioned from a fresh OpenDAoC baseline plus the
  project's content (`camelot env provision`).

The server works from model IDs and declarative rows; the client resolves the
rest through its own tables. The Console is a separate app on the server,
sharing the Installer's state file.
