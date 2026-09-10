"""Camelot Studio — CLI.

Commands:
  camelot info <file>             NIF info (version, blocks, types, nodes, textures)
  camelot kfa <file>              channels of a .kfa (bone indices, keys)
  camelot gltf <file.nif> --out <prefix>
                                  glTF export (bind pose, skinned)
  camelot roundtrip <file>        read -> write -> read (diff)
  camelot batch <manifest> --out <report>
                                  bulk validation (roundtrip + info)

Paths in the manifest starting with ``DAOC CLIENT/`` are resolved against the
``DAOC_CLIENT`` environment variable when set (the manifest format predates
the env var and keeps those literal prefixes).
"""
from __future__ import annotations

import argparse
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
    export_gltf(args.path, out, verbose=not args.quiet, emit_skinned=args.skinned)
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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())