# server-bridge — the server side of a deploy

The folder's role in the pipeline (2026-10-14): where a release meets a
server. Two pieces, both designed in the private workspace's `notes/`:

- the **Installer** — a small C# script beside the server, discovered through
  `IDatabaseUpdater` and run at boot, applying one release idempotently,
  recording it and reversing it (`mod-loader.md`);
- the **local test server** — provisioned from a fresh OpenDAoC baseline plus
  the project's content, never a dump of production.

The delivered `camelot-mod-loader/` is the previous mod-host line; the
Installer supersedes its design. The admin Console is a separate app on the
server, sharing the Installer's state file and the pack format.

The server does not need to know the client's formats: it references model
IDs and consumes declarative rows; the client resolves the rest through its
own tables.
