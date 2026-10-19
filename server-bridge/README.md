# server-bridge — the server side of a deploy

Where a release meets a server. Two pieces, both designed in the private
workspace's `notes/`:

- the **Installer** — `camelot-installer/`, a C# plugin beside the server,
  discovered through `IDatabaseUpdater` and run at boot: it honours the Console's
  ask, applies what Deploy handed over, records it, and reverses it
  (`server-install.md` §D10, §D17);
- the **local test server** — provisioned from a fresh OpenDAoC baseline plus
  the project's content, never a dump of production (`camelot env provision`).

`camelot-mod-loader/` is the previous mod-host line, kept as the reference its
design came from: many mods, ids allocated by the server, no dependencies. With
one project per server that arbitration is unnecessary, and the Installer is
that design with the mod host removed — but three of its ideas survive intact,
and the READMEs of both say which (`mod-loader.md`).

The server does not need to know the client's formats: it references model
IDs and consumes declarative rows; the client resolves the rest through its
own tables.

The admin Console is a separate app on the server, sharing the Installer's state
file.
