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
    from .mpak import MpakArchive, write_mpak

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
    )
    app = create_app(cfg)

    url = f"http://{'localhost' if cfg.is_loopback else host}:{port}/"
    print(f"camelot serve: root={cfg.root}")
    print(f"  viewer: {url}")
    print(f"  api:    {url}api/v1/health  (docs at {url}api/docs)")
    if not static_dir:
        print("  (no viewer build; the API alone is served)")
    if cfg.open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=args.log_level)
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
    # `elapsed_seconds` lives only in the report: the manifest is hashed
    # into checksums.lock, so a wall-clock field there would make every
    # build differ from the last.
    payload = {
        "ok": report.ok,
        "figures": len(manifest.figures),
        "appearance_slots": slots,
        "anim_states": states,
        "unresolved": [u.to_dict() for u in report.unresolved],
        "errors": report.errors,
        "client_build": manifest.client_build,
        "client_profile": manifest.client_profile,
        "elapsed_seconds": round(time.time() - started, 2),
    }
    stable = {k: v for k, v in payload.items() if k != "elapsed_seconds"}
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

    os.makedirs(os.path.join(args.out, "manifest"), exist_ok=True)
    # Merge, never replace: emit_artifacts has already hashed the content
    # files, and overwriting here would leave the lock covering only the
    # report while the manifest claims to pin the GLBs.
    manifest.artifacts.update(contract_mod.artifact_hashes({
        os.path.join("reports", "validation.json"): (
            json.dumps(stable, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }))
    # The manifest hashes the report, so the lock covers the whole build in
    # one place.
    with open(os.path.join(args.out, "manifest", "content.json"), "wb") as fh:
        fh.write(manifest.to_bytes())
    lock_lines = [f"{d}  {n}" for n, d in sorted(manifest.artifacts.items())]
    with open(os.path.join(args.out, "checksums.lock"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lock_lines) + "\n")
    print(f"wrote {args.out}/manifest/content.json")
    return 0


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
    p.add_argument("--dry-run", action="store_true", help="report only, write no manifest")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("serve", help="local daemon: asset library + viewer")
    p.add_argument("--root", help="client directory (default: $DAOC_CLIENT)")
    p.add_argument("--cache-dir", help="cache directory (default: platform cache)")
    p.add_argument("--host", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="port (default 8760)")
    p.add_argument("--static", help="built viewer directory (default: web/build)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--no-watch", action="store_true", help="disable filesystem watching")
    p.add_argument("--allow-remote", action="store_true",
                   help="allow binding a non-loopback host (unsafe)")
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())