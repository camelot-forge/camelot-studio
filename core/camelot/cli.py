"""Camelot Studio — CLI.

Commands:
  camelot info <file>             NIF info (version, blocks, types, nodes, textures)
  camelot kfa <file>              channels of a .kfa (bone indices, keys)
  camelot gltf <file.nif> --out <prefix>
                                  glTF export (bind pose, skinned)
  camelot roundtrip <file>        read -> write -> read (diff)
  camelot batch <manifest> --out <report>
                                  bulk validation (roundtrip + info)
  camelot mpak list <archive>     MPAK: internal name + entries
  camelot mpak get <archive> <entry>
                                  decompress one entry to stdout
  camelot mpak pack <out> <name> <file...>
                                  build an MPAK archive from files
  camelot contract --out DIR      build the server content contract (manifest
                                  + reports + checksums) from $DAOC_CLIENT
  camelot mod build --dir DIR --out PACK.tar.gz
                                  build a mod pack from an authoring directory
                                  (mod.json, client/, client.patch.json)
  camelot client install PACK     apply a mod's client half to a client
                                  directory (also: uninstall, status, verify)
  camelot project new NAME --client DIR
                                  create a project — the whole content of one
                                  server — and its git scaffolding; also:
                                  open (verify it and start the daemon on it),
                                  info (where it stands), populate (record what
                                  the edits depend on), verify (read the base
                                  back), import (make a 6.3 authoring
                                  directory a project, in place)
  camelot serve [--root DIR]      local daemon: asset library + viewer

Paths in the manifest starting with ``DAOC CLIENT/`` are resolved against the
``DAOC_CLIENT`` environment variable when set (the manifest format predates
the env var and keeps those literal prefixes).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

from .kfa import kfa_summary
from .nifutil import block_types, load, store

SHAPE_TYPES = ("NiTriShape", "NiTriStrips")

MANIFEST_CLIENT_PREFIX = "DAOC CLIENT/"


def resolve_path(p: str) -> str:
    """Rewrite a manifest path against $DAOC_CLIENT when applicable."""
    client = os.environ.get("DAOC_CLIENT")
    if client and p.startswith(MANIFEST_CLIENT_PREFIX):
        return os.path.join(client, p[len(MANIFEST_CLIENT_PREFIX):])
    return p


def cmd_info(args) -> int:
    from .nifutil import version_bits

    data = load(args.path)
    ver, nb = version_bits(data)
    print(f"file: {args.path}")
    print(f"version: {hex(ver)} — blocks: {nb} — roots: {[type(r).__name__ for r in data.roots]}")
    types = block_types(data)
    from collections import Counter

    for t, c in Counter(types).most_common():
        print(f"  {t:<28} {c}")
    print("-- named nodes --")
    for b in data.blocks:
        if type(b).__name__ in ("NiNode",) + SHAPE_TYPES:
            nm = b.name.decode("latin1") if b.name else ""
            if nm:
                print(f"  [{type(b).__name__}] {nm}")
    print("-- textures --")
    tex = set()
    for b in data.blocks:
        if type(b).__name__ == "NiSourceTexture" and b.file_name:
            tex.add(b.file_name.decode("latin1"))
    for t in sorted(tex):
        print(f"  {t}")
    return 0


def cmd_kfa(args) -> int:
    s = kfa_summary(args.path)
    print(f"file: {s['path']}")
    print(f"version: {s['version']} — blocks: {s['blocks']} — roots: {s['roots']}")
    print(f"animated bone channels: {len(s['bone_indices'])}")
    print(f"bone indices: {s['index_order']}")
    print(f"max keys (~frames): {s['max_keys']}")
    return 0


def cmd_gltf(args) -> int:
    from .gltf import export_gltf

    out = args.out or args.path.rsplit(".", 1)[0]
    client = os.environ.get("DAOC_CLIENT")
    textures = None
    if args.texture is None and not args.no_texture and client:
        from .gamedata import resolve_figure_skins

        skins = resolve_figure_skins(client, args.path)
        if skins:
            textures = list(skins)
    pose = None
    pose_time = args.pose_time
    if getattr(args, "pose", None):
        from .kfa import load_kfa

        pose = load_kfa(args.pose)
        if client:
            from .anims import anim_timing, native_fps

            timing = anim_timing(client, args.pose)
            if timing is not None:
                fps, frames = timing
                rate = fps / native_fps(pose)
                pose_time = args.pose_time * rate
                if not args.quiet:
                    print(f"  anim timing: {frames:g} frames @ {fps:g}fps "
                          f"(rate {rate:.3g}, loop {frames / fps:.3g}s)")
    kwargs = {
        "verbose": not args.quiet,
        "emit_skinned": args.skinned,
        "texture": args.texture,
        "textures": textures,
        "pose": pose,
        "pose_time": pose_time,
        "bake_pose": args.bake_pose,
    }
    if client and args.texture is None and not args.no_texture and not args.no_fig3_head:
        # Player base figures get the Catacombs fig3 head/hair (see figure.py).
        from .figure import export_figure

        export_figure(client, args.path, out, **kwargs)
    else:
        export_gltf(args.path, out, **kwargs)
    return 0


def cmd_roundtrip(args) -> int:
    from collections import Counter

    from .legacy import is_legacy

    t0 = time.time()
    data = load(args.path)
    before = (data.version, len(data.blocks), Counter(block_types(data)))
    roots_b = sorted(str(getattr(r, "name", None)) for r in data.roots)
    if is_legacy(data):
        print(f"roundtrip {os.path.basename(args.path)} : N/A (legacy 4.x is read-only in M0)")
        return 0
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "rt.nif")
        store(data, tmp)
        data2 = load(tmp)
    after = (data2.version, len(data2.blocks), Counter(block_types(data2)))
    roots_a = sorted(str(getattr(r, "name", None)) for r in data2.roots)
    ok = before == after and roots_b == roots_a
    print(f"roundtrip {os.path.basename(args.path)} : {'OK' if ok else 'DIFF'}")
    if not ok:
        if before[0] != after[0]:
            print(f"  version {hex(before[0])} -> {hex(after[0])}")
        if before[1] != after[1]:
            print(f"  block count {before[1]} -> {after[1]}")
        if before[2] != after[2]:
            print(f"  types: {dict(before[2] - after[2])} -> {dict(after[2] - before[2])}")
    print(f"  {time.time() - t0:.2f}s")
    return 0 if ok else 1


def cmd_batch(args) -> int:
    from collections import Counter

    from .legacy import is_legacy

    paths = []
    with open(args.manifest, "r", encoding="utf-8") as fh:
        for line in fh:
            line = resolve_path(line.strip().replace("\\", os.sep))
            if not line or line.startswith("#"):
                continue
            paths.append(line)
    if args.limit:
        paths = paths[: args.limit]
    lines = [f"# batch {args.manifest} — {len(paths)} files", ""]
    n_ok = n_diff = n_absent = n_err = n_na = 0
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        if not os.path.exists(p):
            lines.append(f"ABSENT  {p}")
            n_absent += 1
            print(f"[{i}/{len(paths)}] ABSENT {p}", flush=True)
            continue
        try:
            data = load(p)
            ver = hex(data.version)
            nb = len(data.blocks)
            types = Counter(block_types(data))
            line = f"READ OK  {p}  version={ver} blocks={nb}"
            if is_legacy(data) or args.read_only:
                line += "  RT=N/A (read-only mode)" if args.read_only else "  RT=N/A (legacy read-only)"
                n_na += 1
                n_ok += 1
            else:
                with tempfile.TemporaryDirectory() as td:
                    tmp = os.path.join(td, "rt.nif")
                    store(data, tmp)
                    data2 = load(tmp)
                ok = (data2.version, len(data2.blocks), Counter(block_types(data2))) == (
                    data.version, nb, types)
                line += f"  RT={'OK' if ok else 'DIFF'}"
                n_ok += 1 if ok else 0
                n_diff += 0 if ok else 1
            lines.append(line)
        except Exception as e:  # noqa: BLE001
            lines.append(f"ERROR  {p}  {type(e).__name__}: {e}")
            n_err += 1
            print(f"[{i}/{len(paths)}] ERROR {p}: {type(e).__name__}: {e}", flush=True)
        if i % 25 == 0 or i == len(paths):
            el = time.time() - t0
            print(f"[{i}/{len(paths)}] {el:.1f}s elapsed ({el/max(i,1):.2f}s/file) "
                  f"ok={n_ok} err={n_err} absent={n_absent}", flush=True)
    lines += ["", (f"summary: {n_ok} OK, {n_diff} DIFF (reorder), "
                 f"{n_na} N/A (read-only), {n_absent} absent, "
                 f"{n_err} parse errors")]
    dest = args.out or "work/camelot_batch.txt"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"batch report: {dest} ({n_ok} OK / {n_diff} DIFF / {n_err} errors)")
    return 0 if n_diff == 0 and n_err == 0 else 1


def cmd_mpak(args) -> int:
    from .mpak import MpakArchive, repack_mpak, write_mpak

    if args.mpak_cmd == "repack":
        # The mod path: rewrite an archive, optionally swapping an entry's
        # content. The header carriers (directory crc32 and length, name
        # length, entry count) are recomputed for the streams actually
        # written; the version/constant bytes and the undecoded per-entry
        # record field stay those of the source archive.
        replacements = {}
        for spec in args.replace or []:
            name, _, path = spec.partition("=")
            if not path:
                print(f"repack: --replace wants NAME=FILE, got {spec!r}", file=sys.stderr)
                return 2
            with open(path, "rb") as fh:
                replacements[name] = fh.read()
        repack_mpak(args.archive, args.out, replacements)
        print(f"wrote {args.out} ({len(replacements)} entr{'y' if len(replacements) == 1 else 'ies'} replaced)")
        return 0

    if args.mpak_cmd == "list":
        a = MpakArchive(args.archive)
        try:
            print(f"# {os.path.basename(args.archive)} (internal name: {a.name!r}) "
                  f"— {len(a.entries)} entries")
            for e in sorted(a.entries):
                print(f"  {e}")
        finally:
            a.close()
        return 0
    if args.mpak_cmd == "get":
        a = MpakArchive(args.archive)
        try:
            sys.stdout.buffer.write(a.read(args.entry))
        finally:
            a.close()
        return 0
    if args.mpak_cmd == "pack":
        entries = []
        for f in args.files:
            with open(f, "rb") as fh:
                entries.append((os.path.basename(f), fh.read()))
        write_mpak(args.out, args.name, entries)
        print(f"packed {len(entries)} entries -> {args.out}")
        return 0
    return 2


def _default_static_dir() -> str | None:
    """The built SvelteKit viewer, if present next to the core package."""
    here = os.path.dirname(os.path.abspath(__file__))
    build = os.path.join(os.path.dirname(os.path.dirname(here)), "web", "build")
    return build if os.path.exists(os.path.join(build, "index.html")) else None


def cmd_server(args) -> int:
    from . import serverrows

    if bool(args.config) == bool(args.dsn):
        print("error: pass exactly one of --config or --dsn", file=sys.stderr)
        return 2
    try:
        connection = (serverrows.parse_server_config(args.config) if args.config
                      else serverrows.parse_dsn(args.dsn))
        snapshot = serverrows.export_snapshot(connection, args.out)
    except serverrows.ServerRowsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except serverrows.mysql_errors() as error:
        # the driver's own errors (a dead socket, a bad row); ours above
        print(f"error: {error}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{name} {count:,}" for name, count in snapshot.counts().items())
    print(f"wrote {args.out}: {counts}")
    return 0


def cmd_project(args) -> int:
    from pathlib import Path

    from . import project as projects

    if args.project_cmd == "new":
        names = [item.strip() for item in args.environments.split(",") if item.strip()]
        try:
            created = projects.new(
                Path(args.dir) / args.name,
                name=args.name,
                client=args.client or os.environ.get("DAOC_CLIENT"),
                cache=args.cache,
                environments=names,
                author=args.author,
                git=args.git,
            )
        except projects.ProjectError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        shown = projects.info(created)
        print(
            f"created {shown['root']}: {shown['resources']} base resources, "
            f"environments {', '.join(shown['environments'])}"
        )
        if shown["build"]:
            print(f"client build: {shown['build']}")
        else:
            print("client build: not recorded (no client given)")
        if shown["git"]:
            print("git: initialised (no commit, no remote — the history is yours)")
        return 0

    if args.project_cmd == "import":
        try:
            report = projects.import_authoring(
                args.dir,
                name=args.name,
                client=args.client or os.environ.get("DAOC_CLIENT"),
                author=args.author,
                git=args.git,
            )
        except projects.ProjectError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        project = report["project"]
        print(f"imported {project.root}: {project.name} ({project.format})")
        migration = report["migration"]
        if migration is None:
            print("journal: nothing to migrate (no journal.json)")
        else:
            print(
                f"journal: {migration['ops']} op(s) and {migration['rows']} row "
                f"patch(es) → journal/{migration['shard']} "
                "(the old file stays as journal.json.migrated)"
            )
        for fact in report["facts"]:
            print(f"deploy: {fact['kind']}")
        return 0

    if args.project_cmd == "open":
        # `open` is the operator's verb: check the base, say who holds the
        # checkout, then hand over to the daemon (which takes the lock itself).
        try:
            project = projects.load(args.dir)
            findings = projects.verify(project, args.client)
        except projects.ProjectError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for finding in [item for item in findings if item["state"] != "ok"]:
            print(
                f"warning: {finding['resource']} {finding['state']} — {finding['detail']}",
                file=sys.stderr,
            )
        if any(item["state"] != "ok" for item in findings):
            print(
                "warning: the base moved under this project; `camelot project verify` "
                "lists it, and re-base comes with P8",
                file=sys.stderr,
            )
        holder = projects.lock_state(project)
        if holder is not None and holder.is_alive:
            print(
                f"note: {project.root} is held by {holder.author} (pid {holder.pid}): "
                "the daemon will run read-only",
                file=sys.stderr,
            )
        return main(["serve", "--project", str(project.root), *args.rest])

    try:
        project = projects.load(args.dir)
    except projects.ProjectError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.project_cmd == "populate":
        try:
            directory = projects.client_root(project, args.client)
            if directory is None:
                raise projects.ProjectError(
                    f"{project.name}: no client resolved — write {projects.MACHINE_FILE}, "
                    "set $DAOC_CLIENT, or pass --client"
                )
            if args.all_zones:
                zones = projects.zones_on_disk(directory)
            else:
                zones = [int(item) for item in args.zones.replace(" ", "").split(",") if item]
            tables = [item.strip() for item in (args.tables or "").split(",") if item.strip()]
            if not zones and not tables:
                raise projects.ProjectError(
                    "populate needs to know what to record: pass --zones 0,1, --all-zones "
                    "and/or --tables items.csv,monsters.csv"
                )
            found = projects.populate(directory, zones=zones, tables=tables)
            updated, adopted = projects.add_fingerprints(project.root, found, update=args.update)
        except (projects.ProjectError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        groups: dict[str, int] = {}
        for resource in found:
            groups[resource.split("#")[0]] = groups.get(resource.split("#")[0], 0) + 1
        print(
            f"recorded {len(found)} resource(s) from {len(zones)} zone(s) "
            f"and {len(tables)} table(s): {len(updated.fingerprints)} in the project"
        )
        for prefix, count in sorted(groups.items()):
            print(f"  {prefix}: {count} entr{'y' if count == 1 else 'ies'}")
        if adopted:
            print(f"adopted (the base had moved): {', '.join(adopted)}")
        return 0

    if args.project_cmd == "info":
        shown = projects.info(project)
        rows = (
            ("root", shown["root"]),
            ("created", shown["created"]),
            (
                "base",
                f"{shown['build'] or 'no build'} · {shown['resources']} resources",
            ),
            ("client", shown["client"] or "not resolved on this machine"),
            ("author", shown["author"]),
            ("git", "yes" if shown["git"] else "no"),
            ("cache", shown["cache"]),
            ("environments", ", ".join(shown["environments"])),
            ("journal", f"{len(shown['shards'])} shard(s)"),
            ("releases", str(len(shown["releases"]))),
        )
        print(f"{shown['name']}  ({shown['format']})")
        for key, value in rows:
            print(f"  {key:<13} {value}")
        if shown["lock"]:
            print(
                f"  {'lock':<13} held by {shown['lock']['author']} "
                f"(pid {shown['lock']['pid']} on {shown['lock']['host']})"
            )
        return 0

    if args.project_cmd == "release":
        from . import release as release_mod

        try:
            record = release_mod.cut(project.root, client=args.client)
        except (release_mod.ReleaseError, projects.ProjectError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        families = ", ".join(f"{name} {count}" for name, count in record["families"].items())
        server = record["artifacts"]["server"] or {}
        client_half = record["artifacts"]["client"] or {}
        print(f"release {record['number']:04d}  {record['cut_at']}  {families or '—'}")
        print(f"  server  {server.get('pack', '—')}")
        if server.get("rows"):
            print(f"  rows    {server['rows']}")
        if client_half:
            print(f"  client  version {client_half['version']} · {client_half['patchlist']}")
        else:
            print("  client  none (this release has no client half)")
        needs = record["needs"].get("reload") or []
        print(f"  order   {record['order']}" + (f" · reload: {', '.join(needs)}" if needs else ""))
        return 0

    if args.project_cmd == "releases":
        from . import release as release_mod

        try:
            history = release_mod.releases(project.root)
        except release_mod.ReleaseError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        if not history:
            print("no releases yet")
            return 0
        for item in history:
            families = ", ".join(
                f"{name} {count}" for name, count in (item.get("families") or {}).items()
            )
            client_half = (item.get("artifacts") or {}).get("client") or {}
            line = f"{int(item['number']):04d}  {item.get('cut_at', '')}  {families or '—'}"
            if client_half:
                line += f"  client v{client_half.get('version')}"
            print(line)
        return 0

    if args.project_cmd == "verify" and getattr(args, "rebuild", None):
        from . import release as release_mod

        try:
            report = release_mod.rebuild(project.root, args.rebuild, client=args.client)
        except (release_mod.ReleaseError, projects.ProjectError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"release {report['number']:04d}: rebuilt {report['found'] or '(nothing built)'}")
        print(f"  recorded {report['expected'] or '(no server artifact)'}")
        if report["same"]:
            print("  the same bytes — the journal plus the base reproduce it")
            return 0
        print("  DIFFERENT — this release is not reproducible from what is here")
        return 1

    try:
        findings = projects.verify(project, args.client)
    except projects.ProjectError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    wrong = 0
    for finding in findings:
        if finding["state"] != "ok":
            wrong += 1
        line = f"{finding['state']:<8} {finding['resource']}"
        if finding["detail"]:
            line += f"  {finding['detail']}"
        print(line)
    print(f"{len(findings)} resource(s): {len(findings) - wrong} ok, {wrong} not")
    return 1 if wrong else 0


def cmd_serve(args) -> int:
    from .serve.config import (
        DEFAULT_HOST,
        DEFAULT_PORT,
        ServeConfig,
        client_root_from_env,
        default_cache_dir,
    )

    root = args.root or client_root_from_env()
    if not root:
        print("error: no client root; pass --root or set $DAOC_CLIENT", file=sys.stderr)
        return 2
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    host = args.host or DEFAULT_HOST
    port = args.port or DEFAULT_PORT
    if host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
        print(f"refusing to bind {host}: pass --allow-remote to expose the daemon",
              file=sys.stderr)
        return 2

    static_dir = args.static or _default_static_dir()
    if static_dir is None:
        print("note: no built viewer found (run `pnpm build` in web/); API only",
              file=sys.stderr)

    from .serve.app import create_app

    cfg = ServeConfig(
        root=root,
        cache_dir=args.cache_dir or default_cache_dir(),
        host=host,
        port=port,
        open_browser=not args.no_open,
        watch=not args.no_watch,
        static_dir=static_dir,
        server_rows=args.server_rows,
        db_config=args.db_config,
        db_write=args.db_write,
        project=args.project,
    )
    app = create_app(cfg)

    # The project is one *checkout* and one writer (`project.md` §D13): this
    # daemon claims it, or learns who holds it and runs read-only. The lock is
    # the session's (`home.md` §D3): a switch hands it over, and it is dropped
    # when the daemon stops.
    session = app.state.session
    if cfg.project is not None:
        from . import project as projects

        try:
            claim = session.claim()
        except projects.ProjectError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if not claim["held"]:
            held = claim["by"]
            print(
                f"  project: {cfg.project} is held by {held['author']} "
                f"(pid {held['pid']} on {held['host']}): this daemon is read-only",
                file=sys.stderr,
            )
        elif claim["holder"] is not None:
            print(f"  project: {cfg.project} (held by {claim['holder']['author']})")
        if claim.get("recent"):
            print(f"  (not listed as a recent project: {claim['recent']})", file=sys.stderr)

    # D6: the browser lands on the front door when the daemon was started
    # without a project, and on the world editor when it was started on one —
    # the operator's daily flow (a daemon on their project) is unchanged.
    landing = "world" if cfg.project is not None else "home"
    url = f"http://{'localhost' if cfg.is_loopback else host}:{port}/"
    print(f"camelot serve: root={cfg.root}")
    print(f"  viewer: {url}{landing}")
    print(f"  api:    {url}api/v1/health  (docs at {url}api/docs)")
    if not static_dir:
        print("  (no viewer build; the API alone is served)")
    if cfg.open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"{url}{landing}")).start()

    import uvicorn

    # The lock is dropped when the daemon stops — including when it is stopped
    # by a signal, which is the usual way and the one a `finally` misses
    # (uvicorn re-raises the signal once its shutdown is done).
    session.release_on_signal()
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=args.log_level)
    finally:
        session.release()
    return 0


def cmd_pack(args) -> int:
    """Build the server-facing content pack from a client corpus.

    Reads the corpus through the format layer, resolves the appearance and
    animation references the client's tables declare, and writes the
    versioned manifest plus a validation report. The build is
    reproducible: the same corpus produces the same bytes, so the
    checksums in `checksums.lock` mean something. An unresolved reference
    is reported and (unless `--dry-run`) refuses the build rather than
    shipping a guess.
    """
    from . import contract as contract_mod

    client = args.client or os.environ.get("DAOC_CLIENT")
    if not client or not os.path.isdir(client):
        print("contract: no client corpus (pass --client or set $DAOC_CLIENT)", file=sys.stderr)
        return 2

    started = time.time()
    print(f"reading {client} ...", flush=True)
    try:
        manifest = contract_mod.build_player_manifest(
            client, pack_id=args.id, version=args.version,
            material=args.material, with_states=not args.no_states,
        )
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"contract: build failed: {exc}", file=sys.stderr)
        return 1

    # A user may add content the stock client has no row for. Their figures
    # are appended, never renumbered: a server may already have persisted
    # the built-in ids.
    for spec in args.add_figure or []:
        parts = spec.split(":")
        if len(parts) < 3:
            print(f"contract: bad --add-figure {spec!r}, want id:kind:rig", file=sys.stderr)
            return 2
        logical_id, kind, rig = parts[0], parts[1], parts[2]
        try:
            manifest.figures.append(contract_mod.Figure(
                logical_id=logical_id, figure_id=int(parts[3]) if len(parts) > 3 else 0,
                kind=kind, rig=rig or None,
            ))
        except ValueError as exc:
            print(f"contract: --add-figure {spec!r}: {exc}", file=sys.stderr)
            return 2
    if args.figures:
        # A deliberate restriction is a note, not an unresolved reference:
        # only a reference the client could not answer may block publication.
        keep = set(args.figures)
        dropped = [f.logical_id for f in manifest.figures if f.figure_id not in keep]
        manifest.figures = [f for f in manifest.figures if f.figure_id in keep]
        if dropped:
            manifest.notes.append(
                f"restricted to fig3 rows {sorted(keep)}; omitted {len(dropped)} rows"
            )

    states = sum(len(f.anim_states) for f in manifest.figures)
    slots = sum(len(f.appearance) for f in manifest.figures)
    print(
        f"  {len(manifest.figures)} figures, {slots} appearance slots, {states} anim states",
        flush=True,
    )
    report = manifest.validate(strict=False)
    if not report.ok:
        for ref in report.unresolved:
            print(f"  UNRESOLVED {ref.ref}: {ref.reason}", file=sys.stderr)
        for err in report.errors:
            print(f"  ERROR {err}", file=sys.stderr)

    reports = os.path.join(args.out, "reports")
    os.makedirs(reports, exist_ok=True)
    # The report is hashed into checksums.lock, so it must contain nothing
    # that varies between two builds of one corpus. The build's wall-clock
    # time is printed instead of written: an earlier version hashed a copy
    # without it and wrote a copy with it, so the lock described a file that
    # did not exist and every verification failed.
    elapsed = time.time() - started
    payload = {
        "ok": report.ok,
        "figures": len(manifest.figures),
        "appearance_slots": slots,
        "anim_states": states,
        "unresolved": [u.to_dict() for u in report.unresolved],
        "errors": report.errors,
        "client_build": manifest.client_build,
        "client_profile": manifest.client_profile,
    }
    with open(os.path.join(reports, "validation.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")

    if not report.ok and not args.dry_run:
        print("contract: refusing to publish with unresolved references", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry run: no manifest written")
        return 0

    if not args.no_artifacts:
        print("exporting glTF artifacts ...", flush=True)
        try:
            contract_mod.emit_artifacts(client, manifest, args.out)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            print(f"contract: artifact export failed: {exc}", file=sys.stderr)
            return 1
        for ref in manifest.unresolved:
            print(f"  UNRESOLVED {ref.ref}: {ref.reason}", file=sys.stderr)

    if not args.no_targets:
        print("projecting onto the target servers ...", flush=True)
        try:
            from .targets import TARGETS

            written = write_targets(manifest, args.out,
                                    targets=args.target or list(TARGETS))
        except (OSError, ValueError, KeyError, IndexError) as exc:
            print(f"contract: projection failed: {exc}", file=sys.stderr)
            return 1
        for path in written:
            print(f"  wrote {path}")

    if args.creatures is not None:
        print("emitting the creature spawn package ...", flush=True)
        try:
            rel = write_creatures(
                args.out, limit=args.creatures or None,
                mod_id=args.id, version=args.version,
            )
        except (OSError, ValueError, KeyError, IndexError) as exc:
            print(f"contract: creature package failed: {exc}", file=sys.stderr)
            return 1
        print(f"  wrote {rel}")

    os.makedirs(os.path.join(args.out, "manifest"), exist_ok=True)
    # Merge, never replace: emit_artifacts has already hashed the content
    # files, and overwriting here would leave the lock covering only the
    # report while the manifest claims to pin the GLBs.
    manifest.artifacts.update(contract_mod.artifact_hashes({
        os.path.join("reports", "validation.json"): (
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }))
    # The manifest hashes the report, so the lock covers the whole build in
    # one place.
    with open(os.path.join(args.out, "manifest", "content.json"), "wb") as fh:
        fh.write(manifest.to_bytes())
    lock_lines = [f"{d}  {n}" for n, d in sorted(manifest.artifacts.items())]
    with open(os.path.join(args.out, "checksums.lock"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lock_lines) + "\n")
    print(f"wrote {args.out}/manifest/content.json ({elapsed:.1f}s)")

    if args.package:
        try:
            lock = package_pack(manifest, args.out, args.package)
        except (OSError, ValueError) as exc:
            print(f"contract: packaging failed: {exc}", file=sys.stderr)
            return 1
        print(f"packaged {args.package} ({len(lock)} members)")
    return 0



def write_targets(manifest, out_dir: str, *, targets) -> list[str]:
    """Write each requested target projection beside the contract.

    A projection is a separate file per target so a server adapter reads one
    document and never parses the neutral contract.
    """
    import json

    from . import contract as contract_mod
    from .targets import TARGETS

    written: list[str] = []
    for name in targets:
        module = TARGETS.get(name)
        if module is None:
            raise ValueError(f"unknown target {name!r}, known: {sorted(TARGETS)}")
        rows = []
        skipped: list[dict] = []
        for figure in manifest.figures:
            try:
                rows.append(module.project(figure).to_dict())
            except (KeyError, ValueError) as exc:
                # A target that cannot express a figure says so in its own
                # projection instead of failing the build: the pack is still
                # valid for the figures the target does understand, and a
                # silent omission is what nobody would notice.
                skipped.append({"logical_id": figure.logical_id, "reason": str(exc)})
        rel = os.path.join("targets", name, "figures.json")
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "target": name,
            "schema_version": contract_mod.SCHEMA_VERSION,
            "figures": rows,
            "unprojected": skipped,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        written.append(rel)
    return written


def write_creatures(out_dir: str, *, limit=None, mod_id: str = "daoc.studio.spawns",
                    version: str = "0.1.0") -> str:
    """Emit the declarative rows the mod loader will allocate ids for.

    No ids are chosen here: the manager owns identity, which is what makes
    overlap between mods impossible and lets the same mod install on two
    servers under different ids.
    """
    from . import creatures as creatures_mod
    from . import mod as mod_mod
    from .targets import opendaoc

    client = os.environ.get("DAOC_CLIENT")
    if not client:
        raise ValueError("$DAOC_CLIENT is required to read the creature table")
    rows = creatures_mod.read_creatures(client)
    if limit:
        rows = rows[:limit]
    declarative = [
        opendaoc.creature_row(
            model_id=c.model_id, name=c.name, nif=c.nif, anim_set=c.anim_set,
        )
        for c in rows
    ]
    doc = mod_mod.deployment(mod_id, version, declarative)
    path = os.path.join(out_dir, mod_mod.DEPLOYMENT_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(mod_mod.deployment_bytes(doc))
    return mod_mod.DEPLOYMENT_FILE


def package_pack(manifest, out_dir: str, path: str) -> dict:
    """Collapse everything written under `out_dir` into one archive.

    The lock returned is over the archive's members, so the pack and its
    contents are verifiable as a pair.
    """
    from . import assemble as assemble_mod

    files: dict[str, bytes] = {}
    for root, _dirs, names in os.walk(out_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, out_dir)
            with open(full, "rb") as fh:
                files[rel] = fh.read()
    lock = assemble_mod.archive(manifest, path, files=files)
    return lock


def cmd_mod(args) -> int:
    """Build a mod pack from an authoring directory.

    The directory holds the identity (`mod.json`), optional server rows
    (`rows.json`), loose replacements (`client/`, paths mirroring the game)
    and archive or table edits (`client.patch.json`). Everything is resolved
    against the client corpus, so a pack that does not hold together is
    refused here rather than on a player's machine.
    """
    from . import modbuild

    client = args.client or os.environ.get("DAOC_CLIENT")
    if not client or not os.path.isdir(client):
        print("mod build: no client corpus (pass --client or set $DAOC_CLIENT)",
              file=sys.stderr)
        return 2
    if os.path.exists(os.path.join(client, ".camelot", "state.json")):
        # The builder resolves ids and columns against this directory; with
        # mods installed it is no longer the pristine reference, and an `add`
        # of a row an installed mod already carries is refused for the wrong
        # reason. Point at a clean copy of the client instead.
        print("mod build: warning: this client has mods installed; it is not a "
              "pristine reference (use a clean copy)", file=sys.stderr)
    try:
        used_text = None
        if args.used_models:
            with open(args.used_models, encoding="utf-8") as fh:
                used_text = fh.read()
        report = modbuild.build_mod_pack(args.dir, client, args.out,
                                         creatures=args.creatures,
                                         used_models=used_text)
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(f"mod build: {error}", file=sys.stderr)
        return 1
    print(f"built {report['out']}")
    print(f"  mod {report['mod']} {report['version']}: "
          f"{report['items']} client item(s), {report['rows']} server row(s), "
          f"{report['members']} payload member(s)")
    return 0


def cmd_client(args) -> int:
    """Apply a mod's client half, and read the client back."""
    from . import clientinstall

    client = args.client or os.environ.get("DAOC_CLIENT")
    if not client or not os.path.isdir(client):
        print("client: no client directory (pass --client or set $DAOC_CLIENT)",
              file=sys.stderr)
        return 2
    try:
        if args.client_cmd == "install":
            report = clientinstall.install_pack(
                client, args.pack,
                priority=args.priority,
                allow_build_mismatch=args.allow_build_mismatch,
                adopt=args.adopt,
            )
            print(f"installed {report['mod']} {report['version']}")
            for warning in report["warnings"]:
                print(f"  warn: {warning}")
            for rel in report["changed"]:
                print(f"  wrote {rel}")
            if not report["changed"]:
                print("  (nothing changed)")
            return 0
        if args.client_cmd == "uninstall":
            report = clientinstall.uninstall(client, args.mod)
            print(f"removed {report['mod']}")
            for rel in report["changed"]:
                print(f"  restored {rel}")
            if not report["changed"]:
                print("  (nothing changed)")
            return 0
        if args.client_cmd == "status":
            report = clientinstall.status(client)
            if not report["installs"]:
                print("no mods installed on this client")
            for install in report["installs"]:
                print(f"{install['mod']} {install['version']} "
                      f"(priority {install['priority']}, "
                      f"{len(install['claims'])} claim(s))")
            for claim, owner in report["owners"].items():
                print(f"  {claim} -> {owner}")
            return 0
        if args.client_cmd == "free-ids":
            from . import clientpatch, mod, modelusage

            used_text = None
            if args.used_models:
                with open(args.used_models, encoding="utf-8") as fh:
                    used_text = fh.read()
            carried = None
            dll = clientinstall.resolve_existing(client, clientpatch.CLIENT_BINARY)
            if dll:
                with open(dll, "rb") as fh:
                    carried = clientpatch.carried_cap(fh.read())
            max_id = args.max_id or (carried - 1 if carried else mod.CLIENT_ID_MAX)
            ids = modelusage.client_row_ids(client, args.table)
            used = modelusage.used_model_ids(used_text)
            free, reclaimable = modelusage.pool(ids, used, max_id)
            note = f" (client.modelids.{carried})" if carried and not args.max_id else ""
            print(f"{args.table}: {len(ids)} rows, ceiling {max_id}{note}")
            head = ", ".join(str(i) for i in free[:12])
            print(f"  free to add:       {len(free):4d} ids"
                  + (f"  (first: {head})" if free else ""))
            head = ", ".join(str(i) for i in reclaimable[:12])
            print(f"  safe to take over: {len(reclaimable):4d} shipped rows"
                  + (f"  (first: {head})" if reclaimable else ""))
            print("  take-over is a `set` on that row (point it at your figure")
            print("  and textures); `add` needs an id from the free list.")
            return 0
        if args.client_cmd == "patch":
            from . import clientpatch

            running = clientinstall.blocks_install(client)
            if running:
                print(f"client: the game is running ({running}); close it and retry",
                      file=sys.stderr)
                return 1
            path = clientinstall.resolve_existing(client, clientpatch.CLIENT_BINARY)
            if path is None:
                print(f"client: no {clientpatch.CLIENT_BINARY} under {client}",
                      file=sys.stderr)
                return 1
            try:
                if args.restore:
                    clientpatch.restore_file(path)
                    print(f"restored {clientpatch.CLIENT_BINARY} from its .orig backup")
                    return 0
                recipe = clientpatch.patch_file(path, cap=args.max_id + 1)
            except (OSError, clientpatch.PatchError) as error:
                print(f"client patch: {error}", file=sys.stderr)
                return 1
            print(f"patched {clientpatch.CLIENT_BINARY}: the model table holds "
                  f"{args.max_id + 1} entries (ids up to {args.max_id}), providing "
                  f"{', '.join(recipe['provides'])}")
            print(f"  backup: {clientpatch.CLIENT_BINARY}.orig "
                  "(restore with: camelot client patch --restore)")
            return 0
        if args.client_cmd == "verify":
            report = clientinstall.verify(client)
            for warning in report["warnings"]:
                print(f"  warn: {warning}")
            for problem in report["problems"]:
                print(f"  problem: {problem}", file=sys.stderr)
            print("ok" if report["ok"] else "NOT ok")
            return 0 if report["ok"] else 1
    except (clientinstall.InstallError, OSError) as error:
        print(f"client: {error}", file=sys.stderr)
        return 1
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="camelot",
        description="Camelot Studio — DAoC asset editing tools",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="NIF info")
    p.add_argument("path")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("kfa", help=".kfa animation channels")
    p.add_argument("path")
    p.set_defaults(func=cmd_kfa)

    p = sub.add_parser("gltf", help="export a figure NIF to glTF")
    p.add_argument("path")
    p.add_argument("--out", help="output prefix (default: input basename)")
    p.add_argument("--skinned", action="store_true",
                   help="experimental: emit JOINTS/WEIGHTS (inverseBind = inv(worldBind))")
    p.add_argument("--pose", help="pose the skeleton from a .kfa file")
    p.add_argument("--pose-time", type=float, default=0.0,
                   help="game seconds to sample the --pose KFA at (mapped to key "
                        "time via animnifs.csv when $DAOC_CLIENT resolves it, "
                        "else key seconds)")
    p.add_argument("--bake-pose", action="store_true",
                   help="bake the --pose KFA sample into the vertices (CPU skinning)")
    p.add_argument("--texture", help="embed a .dds texture (CS_camelot_texture extension)")
    p.add_argument("--no-texture", action="store_true",
                   help="skip automatic gamedata texture resolution")
    p.add_argument("--no-fig3-head", action="store_true",
                   help="keep the classic (headless) player head instead of "
                        "attaching the Catacombs fig3 head/hair")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_gltf)

    p = sub.add_parser("roundtrip", help="read -> write -> read")
    p.add_argument("path")
    p.set_defaults(func=cmd_roundtrip)

    p = sub.add_parser("batch", help="bulk validation from a manifest")
    p.add_argument("manifest")
    p.add_argument("--out", help="report file (default: work/camelot_batch.txt)")
    p.add_argument("--read-only", action="store_true",
                   help="skip the roundtrip write/rewrite (faster coverage runs)")
    p.add_argument("--limit", type=int, default=0,
                   help="only validate the first N manifest entries")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("mpak", help="MPAK archive tools (.mpk/.npk)")
    mp = p.add_subparsers(dest="mpak_cmd", required=True)
    lp = mp.add_parser("list", help="list internal name + entries")
    lp.add_argument("archive")
    gp = mp.add_parser("get", help="decompress one entry to stdout")
    gp.add_argument("archive")
    gp.add_argument("entry")
    rp = mp.add_parser("repack", help="rewrite an archive, preserving its header")
    rp.add_argument("archive")
    rp.add_argument("out")
    rp.add_argument("--replace", action="append", metavar="NAME=FILE",
                    help="replace an entry's content with a file")

    pp = mp.add_parser("pack", help="build an MPAK archive from files")
    pp.add_argument("out")
    pp.add_argument("name", help="internal archive name")
    pp.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_mpak)

    p = sub.add_parser("contract", help="build the server content contract")
    p.add_argument("--client", help="client corpus (default: $DAOC_CLIENT)")
    p.add_argument("--out", required=True, help="output pack directory")
    p.add_argument("--id", default="daoc.studio", help="pack id")
    p.add_argument("--version", default="0.1.0", help="pack version")
    p.add_argument("--material", help="pskins material to describe (default: first family)")
    p.add_argument("--no-states", action="store_true", help="skip the anim sets")
    p.add_argument("--no-artifacts", action="store_true",
                   help="describe only, emit no glTF")
    p.add_argument("--figures", type=int, nargs="*", metavar="ID",
                   help="restrict to these fig3 row ids (default: all 36)")
    p.add_argument("--add-figure", action="append", metavar="ID:KIND:RIG[:ROWID]",
                   help="add a custom figure, e.g. daoc.custom.sword:object:sword")
    p.add_argument("--target", action="append",
                   help="project onto these targets (default: all)")
    p.add_argument("--no-targets", action="store_true",
                   help="skip the target projections")

    p.add_argument("--package", metavar="PATH.tar.gz",
                   help="assemble the written pack into one deployable archive")
    p.add_argument("--creatures", type=int, metavar="N", nargs="?", const=0,
                   help="also emit the OpenDAoC spawn package (all, or N models)")

    p.add_argument("--dry-run", action="store_true", help="report only, write no manifest")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("mod", help="mod packs: build one from an authoring directory")
    mp = p.add_subparsers(dest="mod_cmd", required=True)
    bp = mp.add_parser("build", help="build a pack from a mod directory")
    bp.add_argument("--dir", required=True, help="the authoring directory")
    bp.add_argument("--client", help="client corpus (default: $DAOC_CLIENT)")
    bp.add_argument("--out", required=True, help="output pack (.tar.gz)")
    bp.add_argument("--creatures", type=int, metavar="N", nargs="?", const=0,
                    help="also add spawn rows (all, or the first N creature models)")
    bp.add_argument("--used-models",
                    help="file of server-referenced model ids (default: the "
                         "measured shipped list)")
    p.set_defaults(func=cmd_mod)

    p = sub.add_parser("client", help="apply a mod's client half to a client directory")
    cp = p.add_subparsers(dest="client_cmd", required=True)
    ip = cp.add_parser("install", help="install (or update) a pack's client half")
    ip.add_argument("pack")
    ip.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    ip.add_argument("--priority", type=int, help="override the pack's priority")
    ip.add_argument("--allow-build-mismatch", action="store_true",
                    help="install a pack built against another client build")
    ip.add_argument("--adopt", action="store_true",
                    help="make files changed outside the tool the new baseline")
    rp = cp.add_parser("uninstall", help="remove a mod's client half")
    rp.add_argument("mod")
    rp.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    tp = cp.add_parser("status", help="what is installed, and who owns what")
    tp.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    fp = cp.add_parser("free-ids",
                       help="model ids a mod may use without a client patch")
    fp.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    fp.add_argument("--table", default="monsters.csv",
                    help="table to inspect (default: monsters.csv)")
    fp.add_argument("--used-models",
                    help="file of server-referenced model ids (default: the "
                         "measured shipped list)")
    fp.add_argument("--max-id", type=int,
                    help="client ceiling to respect (default: the measured one, "
                         "or the patched table's when the client carries one)")
    pp = cp.add_parser("patch",
                       help="raise the client's model-id ceiling in place "
                            "(unmanaged clients; packs carry the same recipe)")
    pp.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    pp.add_argument("--max-id", type=int, default=32767,
                    help="highest usable model id (default 32767; the table "
                         "becomes max_id+1 entries wide)")
    pp.add_argument("--restore", action="store_true",
                    help="put the original game1127.dll back from its .orig backup")
    vp = cp.add_parser("verify", help="read the client back against the state")
    vp.add_argument("--client", help="client directory (default: $DAOC_CLIENT)")
    p.set_defaults(func=cmd_client)

    p = sub.add_parser("serve", help="local daemon: asset library + viewer")
    p.add_argument("--root", help="client directory (default: $DAOC_CLIENT)")
    p.add_argument("--cache-dir", help="cache directory (default: platform cache)")
    p.add_argument("--host", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="port (default 8760)")
    p.add_argument("--static", help="built viewer directory (default: web/build)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--no-watch", action="store_true", help="disable filesystem watching")
    p.add_argument("--server-rows",
                   help="snapshot exported from the server DB (camelot server export)")
    p.add_argument("--db-config",
                   help="the server's own serverconfig.xml: read the live MySQL")
    p.add_argument("--db-write", action="store_true",
                   help="let the editor write the server's own rows (patches "
                        "and removals, recorded and reversible) — never silent")
    p.add_argument("--project",
                   help="the project's directory (P1): the journal's shards, the "
                        "build outputs and the deploy log live there")
    p.add_argument("--edits", dest="project",
                   help="the older name of --project (the 6.3 authoring directory)")
    p.add_argument("--allow-remote", action="store_true",
                   help="allow binding a non-loopback host (unsafe)")
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("server",
                       help="the server's world rows (mob/worldobject/door/path)")
    sp = p.add_subparsers(dest="server_cmd", required=True)
    ep = sp.add_parser("export", help="export the world rows to a snapshot the viewer reads")
    ep.add_argument("--out", required=True, help="snapshot file to write")
    ep.add_argument("--config", help="the server's own serverconfig.xml (DBConnectionString)")
    ep.add_argument("--dsn", help="mysql://user:password@host:port/database instead")
    p.set_defaults(func=cmd_server)

    p = sub.add_parser("project", help="the project: the whole content of one server")
    pp = p.add_subparsers(dest="project_cmd", required=True)
    np = pp.add_parser("new", help="create a project (and its git scaffolding)")
    np.add_argument("name", help="the project's name; also its directory's name")
    np.add_argument("--dir", default=".", help="where to create it (default: .)")
    np.add_argument("--client",
                    help="the DAoC client to record as the base (default: $DAOC_CLIENT)")
    np.add_argument("--cache", choices=("shared", "local"), default="shared",
                    help="where derived data lives (default: shared)")
    np.add_argument("--environments", default="local,production",
                    help="comma-separated names (default: local,production; staging is optional)")
    np.add_argument("--author", help="the name that signs your journal shards")
    np.add_argument("--git", action="store_true",
                    help="run git init in the new project (no commit, no remote)")
    ip = pp.add_parser("info", help="what the project is, and where it stands")
    ip.add_argument("--dir", default=".", help="the project's directory (default: .)")
    mp = pp.add_parser("import", help="make a 6.3 authoring directory a project, in place")
    mp.add_argument("dir", help="the authoring directory (mod.json, journal.json, …)")
    mp.add_argument("--name", help="the project's name (default: mod.json's id, else the folder)")
    mp.add_argument("--client",
                    help="the DAoC client to record as the base (default: $DAOC_CLIENT)")
    mp.add_argument("--author", help="the name that signs your journal shards")
    mp.add_argument("--git", action="store_true", help="run git init (no commit, no remote)")
    op = pp.add_parser("open", help="verify a project and start the daemon on it")
    op.add_argument("dir", help="the project's directory")
    op.add_argument("--client", help="the client to check (default: this machine's own answers)")
    op.add_argument("rest", nargs=argparse.REMAINDER,
                    help="flags for the daemon, forwarded as-is (see `camelot serve --help`)")
    pop = pp.add_parser("populate", help="record the resources the project's edits depend on")
    pop.add_argument("--dir", default=".", help="the project's directory (default: .)")
    pop.add_argument("--client", help="the client to read (default: this machine's own answers)")
    pop.add_argument("--zones", default="", help="zone ids to record, comma-separated (e.g. 0,12)")
    pop.add_argument("--all-zones", action="store_true",
                     help="every zone folder the client ships (a walk, not the index)")
    pop.add_argument("--tables", default="",
                     help="gamedata tables to record, comma-separated (e.g. items.csv)")
    pop.add_argument("--update", action="store_true",
                     help="adopt a resource whose bytes moved (otherwise it is refused)")
    vp = pp.add_parser("verify", help="read the base back against the recorded fingerprints")
    vp.add_argument("--dir", default=".", help="the project's directory (default: .)")
    vp.add_argument("--client", help="the client to read (default: this machine's own answers)")
    vp.add_argument("--rebuild", type=int, metavar="N",
                    help="rebuild release N from the journal plus the base and compare the "
                         "bytes (pipeline.md §D5: what a CI job runs)")
    rp = pp.add_parser("release", help="cut the next release (the range since the last one)")
    rp.add_argument("--dir", default=".", help="the project's directory (default: .)")
    rp.add_argument("--client", help="the client the build is against (default: this machine's)")
    ls = pp.add_parser("releases", help="the release history, oldest first")
    ls.add_argument("--dir", default=".", help="the project's directory (default: .)")
    p.set_defaults(func=cmd_project)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())