"""Camelot Studio — figure animation resolution and track building.

A figure's animation comes from the client's own tables first (``monnifs``
``Anim Set`` -> ``anims.csv`` -> ``animnifs.csv``, see
:func:`resolve_anim_from_gamedata`); filename conventions and player aliases
are the fallback for figures the tables do not cover (Cata remodels, test
roots). Resolution yields the default KFA (the game's ``Idle`` slot, which
some creatures fill with a fly/locomotion file); this module then converts
its channels into viewer-ready joint rotation tracks (node names, not KFA
indices), reusing the bind-translation mapping from :mod:`camelot.gltf`.

Playback timing comes from ``animnifs.csv`` (``frames`` + ``fps`` columns):
every KFA in the corpus is authored at 15 or 30 fps key density, and the game
plays ``frames`` key intervals at ``fps`` frames per game second, i.e. with
rate ``fps / native`` over a loop of ``frames / fps`` game seconds. That is
why walk and run share one byte-identical file and still differ (7 keys at
1x vs 16 keys at 2x), and why classic idles (``16,3,15``) loop over 5.33s
while the keys span 1.07s. ``fps`` <= 0 (code-driven swims) and missing rows
fall back to stamped file timing.
"""

from __future__ import annotations

import itertools
import os

__all__ = [
    "anim_timing",
    "animation_tracks",
    "load_bone_table",
    "native_fps",
    "resolve_anim_from_gamedata",
    "resolve_idle_kfa",
]

# Authoring key rates observed across the anims corpus (classic 15, CATA 30).
_NATIVE_RATES = (15.0, 30.0)


def load_bone_table(figures_dir: str) -> list[str] | None:
    """Canonical KFA channel index -> bone name (`figures/animnode.dat`).

    The client ships this 175-entry text table (one bone name per line, index
    = line number); it is the engine's authoritative channel-to-bone binding
    (same table as blender_niftools PR #558). Returns None when the file is
    absent, so synthetic roots and tests fall back to offset matching.
    """
    path = os.path.join(figures_dir, "animnode.dat")
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("latin1")
    except OSError:
        return None
    names = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    return [n for n in names if n] or None


def _levenshtein1(a: str, b: str) -> bool:
    """True when *a* and *b* differ by at most one edit (insert/delete/replace)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    i = j = 0
    seen = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if seen:
            return False
        seen = True
        if len(a) > len(b):
            i += 1
        elif len(b) > len(a):
            j += 1
        else:
            i += 1
            j += 1
    return True


def _score(stem: str, name: str) -> int | None:
    if stem == f"{name}_idle":
        return 0
    if stem == f"{name}_idle2":
        return 1
    if stem == f"{name}_cidle":
        return 2
    if stem.startswith(f"{name}_") and stem.endswith("idle") and "cata" not in stem:
        return 3
    return None


# The game fills "Idle" with the stand-still clip whatever the file is called
# (amb_bat's idle points at its fly file); combat/secondary idles come next,
# then locomotion for rigs that only have those.
_ANIM_SLOT_PRIORITY = (
    "idle",
    "c-idle",
    "idle1",
    "idle2",
    "idle3",
    "fly",
    "alt fly anim",
    "swim",
    "walk",
    "run",
)

_TABLE_CACHE: dict[tuple[str, str, float], list[list[str]]] = {}


def _cached_table(client_dir: str, name: str) -> list[list[str]]:
    key = (client_dir, name, os.path.getmtime(os.path.join(client_dir, "gamedata.mpk")))
    hit = _TABLE_CACHE.get(key)
    if hit is None:
        from .gamedata import _read_table

        try:
            hit = _read_table(client_dir, name)
        except (OSError, ValueError, KeyError):
            hit = []
        _TABLE_CACHE[key] = hit
    return hit


def resolve_anim_from_gamedata(client_dir: str, nif_path: str) -> str | None:
    """The client's own animation link for a figure, or ``None``.

    ``monnifs.csv`` (Name = NIF basename) gives the Anim Set id, ``anims.csv``
    gives that set's animation ids per slot, ``animnifs.csv`` maps each id to
    its .kfa file. This is how the game links creatures whose animation files
    are not named after the model: Arachite02 -> ``sect_idle.kfa``,
    amazon -> ``Hoplite_Idle.kfa``, amb_bat -> ``amb_bat_fly.kfa``,
    b_avmon01 -> ``undead_idle.kfa``.
    """
    from .gamedata import ANIMNIFS_FILE, MONNIFS_NAME, MONNIFS_SET, norm_name

    base = os.path.splitext(os.path.basename(nif_path))[0].lower()
    try:
        monnifs = _cached_table(client_dir, "monnifs.csv")
        anims = _cached_table(client_dir, "anims.csv")
        animnifs = _cached_table(client_dir, "animnifs.csv")
    except OSError:
        return None
    if not monnifs or not anims or not animnifs:
        return None

    set_ids: list[str] = []
    fallback_sid = None
    for row in monnifs[2:]:
        if len(row) <= MONNIFS_SET or not row[MONNIFS_NAME].strip():
            continue
        name = row[MONNIFS_NAME].strip().lower()
        if name == base:
            sid = row[MONNIFS_SET].strip()
            if sid and sid not in set_ids:
                set_ids.append(sid)
        elif fallback_sid is None and norm_name(name) == norm_name(base):
            fallback_sid = row[MONNIFS_SET].strip()
    if not set_ids and fallback_sid:
        set_ids = [fallback_sid]
    if not set_ids:
        # Orphan models: no monnifs row, but the animation *set* names the
        # figure (`Boar Keggars` vs Boar_Keggers.nif, Stingray, wolverine…).
        # Normalised match first, then a unique one-character spelling
        # variant (the tables say `Keggars` for `Keggers`); the rig gates
        # still validate the result.
        wanted = norm_name(base)
        exact = [r[0].strip() for r in anims[2:] if r and len(r) > 1 and norm_name(r[1]) == wanted]
        if exact:
            set_ids = list(dict.fromkeys(exact))
        else:
            near = [
                r[0].strip()
                for r in anims[2:]
                if r and len(r) > 1 and len(wanted) >= 6 and _levenshtein1(wanted, norm_name(r[1]))
            ]
            near = list(dict.fromkeys(near))
            if len(near) == 1:
                set_ids = near
    if not set_ids:
        return None

    by_id: dict[str, str] = {}
    for row in animnifs[2:]:
        if len(row) > ANIMNIFS_FILE and row[0].strip().isdigit():
            by_id.setdefault(row[0].strip(), row[ANIMNIFS_FILE].strip())

    anim_dir = os.path.join(client_dir, "anims")
    try:
        entries = {e.lower(): e for e in os.listdir(anim_dir)}
    except OSError:
        return None

    header = anims[1] if len(anims) > 1 else []
    for sid in set_ids:
        slots: dict[str, str] = {}
        for row in anims[2:]:
            if not row or row[0].strip() != sid:
                continue
            for i in range(2, min(len(header), len(row))):
                value = row[i].strip()
                if value and value != "0":
                    slots.setdefault(header[i].strip().lower(), value)
            break
        # Prefer a standing idle: some sets fill their "Idle" slots with a
        # sitting animation (the black cat: Idle/Idle1 = SIT_TAILSWIPE,
        # Idle2 = SIT_IDLE, Idle3 = STAND_IDLE) — for a viewer default the
        # sit clips must lose to the stand one. Penalized candidates are only
        # used when nothing better exists.
        candidates: list[tuple[int, int, str]] = []
        for rank, slot in enumerate(_ANIM_SLOT_PRIORITY):
            fname = by_id.get(slots.get(slot, ""))
            if not fname:
                continue
            real = entries.get(fname.lower())
            if not real:
                continue
            lowered = real.lower()
            penalty = 1 if ("sit" in lowered or "tailswipe" in lowered) else 0
            candidates.append((penalty, rank, real))
        if candidates:
            candidates.sort()
            return os.path.join(anim_dir, candidates[0][2])
    return None


# Player figures do not always share their KFAs' stem spelling:
# BritonM1.NIF pairs with britm_idle.kfa, Troll_M.nif with Troll_idle.kfa.
# Aliases only *propose* files (direct name matches rank first); the mapping
# quality gate still refuses a clip whose rig does not match.
FIGURE_ALIASES = {
    "britonm1": "britm",
    "britonf1": "britf",
    "troll_m": "troll",
    "troll_f": "trollf",
}


def _score_alias(stem: str, name: str) -> int | None:
    best = None
    for candidate, penalty in ((name, 0), (FIGURE_ALIASES.get(name), 10)):
        if not candidate:
            continue
        rank = _score(stem, candidate)
        if rank is not None:
            rank += penalty
            best = rank if best is None else min(best, rank)
    return best


def kfa_comes_from_gamedata(client_dir: str, nif_path: str, kfa_path: str) -> bool:
    """True when *kfa_path* is the figure's gamedata-linked clip.

    Gamedata links are the engine's own binding, so callers use the name
    table directly (``prefer_table``) instead of the offset heuristics.
    """
    linked = resolve_anim_from_gamedata(client_dir, nif_path)
    return bool(linked) and os.path.basename(linked).lower() == os.path.basename(kfa_path).lower()


def resolve_idle_kfa(client_dir: str, nif_path: str) -> str | None:
    """Path to a figure's default .kfa, or ``None``.

    The game's own link (monnifs -> anims -> animnifs, see
    :func:`resolve_anim_from_gamedata`) wins when it resolves; the filename
    convention plus player aliases is the fallback for figures the tables do
    not cover (Cata remodels, test roots).
    """
    from_gamedata = resolve_anim_from_gamedata(client_dir, nif_path)
    if from_gamedata is not None:
        return from_gamedata
    name = os.path.splitext(os.path.basename(nif_path))[0].lower()
    anim_dir = os.path.join(client_dir, "anims")
    try:
        entries = os.listdir(anim_dir)
    except OSError:
        return None
    ranked: list[tuple[int, str]] = []
    for entry in entries:
        if not entry.lower().endswith(".kfa"):
            continue
        rank = _score_alias(entry[:-4].lower(), name)
        if rank is not None:
            ranked.append((rank, entry))
    if not ranked:
        return None
    ranked.sort()
    return os.path.join(anim_dir, ranked[0][1])


def _channel_rotation_keys(ch) -> list:
    """Rotation keys of a channel as (time, quat) pairs.

    Gamebryo KFAs store rotations either as quaternion keys or as three
    per-axis Euler float tracks (``rotation_type`` 4, kept in
    ``xyz_rotations``); DAoC uses both, sometimes within the same file
    (Beetle/shark/snake idles are xyz-only). The Euler tracks are sampled at
    their union of key times and composed with :func:`camelot.kfa.evaluate`.
    """
    if ch.rotations:
        return [(k.time, k.value) for k in ch.rotations]
    if ch.xyz_rotations:
        from .kfa import evaluate

        key_times = sorted({k.time for group in ch.xyz_rotations for k in group})
        # The engine interpolates the three angle tracks and composes; the
        # viewer slerps between the quaternions we give it, which diverges
        # between keys (Beetle's feet floated). Emit densely sampled keys
        # (>= 30 Hz like the authored density) so the viewer follows the
        # per-axis path: t_key = union of key times plus 1/30 s steps.
        times = set(key_times)
        for a, b in itertools.pairwise(key_times):
            steps = int((b - a) * 30) + 1
            for s in range(1, steps):
                times.add(a + (b - a) * s / steps)
        out = []
        for t in sorted(times):
            rot, _trans, _scale = evaluate(ch, t)
            if rot is not None:
                out.append((t, rot))
        return out
    return []


def native_fps(pose) -> float:
    """Authoring key rate of a KFA (15 classic, 30 CATA-era).

    Median rotation-key interval of the densest channel, snapped to the
    observed authoring rates (median resists sparse easing keys that would
    fool a mean, e.g. spider_run). Each axis of type-4 Euler channels counts.
    Falls back to 15.0 when degenerate.
    """
    best: list = []
    for ch in pose.channels.values():
        keys = ch.rotations or max(ch.xyz_rotations, key=len, default=())
        if len(keys) > len(best):
            best = list(keys)
    if len(best) < 2:
        return 15.0
    gaps = sorted(
        b.time - a.time for a, b in itertools.pairwise(best) if b.time > a.time
    )
    if not gaps:
        return 15.0
    median = gaps[len(gaps) // 2]
    if median <= 0:
        return 15.0
    return min(_NATIVE_RATES, key=lambda r: abs(1.0 / r - median))


def anim_timing(client_dir: str, kfa_path: str) -> tuple[float, float] | None:
    """Raw ``(fps, frames)`` game timing for a KFA from ``animnifs.csv``.

    Matched by KFA basename (case-insensitive), first hit wins. Returns
    ``None`` when the row is missing or unusable (caller falls back to
    stamped file timing).
    """
    from .gamedata import _read_table

    try:
        rows = _read_table(client_dir, "animnifs.csv")
    except (OSError, ValueError):
        return None
    want = os.path.basename(kfa_path).lower()
    for row in rows:
        if len(row) < 6 or not row[2].strip().lower().endswith(".kfa"):
            continue
        if row[2].strip().lower() != want:
            continue
        try:
            frames, fps = float(row[3]), float(row[4])
        except ValueError:
            return None
        if fps <= 0 or frames <= 0:
            return None
        return fps, frames
    return None


def animation_tracks(
    nif_path: str, kfa_path: str, client_dir: str | None = None,
    prefer_table: bool | None = None,
) -> dict:
    """Joint rotation tracks for posing *nif_path* with *kfa_path*.

    *prefer_table* forces the engine's canonical bone table for the channel
    mapping (``None`` keeps the gamedata heuristic): the native fig3 runtime
    rigs are not listed in monnifs, but they are exactly what the table
    describes.

    Returns ``{"kfa", "duration", "rate", "tracks": [...]}`` where each track
    carries ``{node, times, quats}`` (rotation locals in KFA key seconds) and,
    when the channel animates them, ``{ptimes, positions}`` (translation
    locals — they ground the figure: root bob with planted feet). *node* is
    the figure's NiNode name. *duration* is the loop length in game seconds
    and *rate* maps game seconds to key seconds (``t_key = (t_game %
    duration) * rate``). Translations stay at the figure bind only for
    channels without translation keys; scales always stay at the bind (never
    animated in the corpus).
    """
    from .gltf import (
        STRETCH_LIMIT_MAX,
        STRETCH_LIMIT_MEAN,
        kfa_translation_trust,
        map_kfa_to_nodes,
        mapping_is_vetted,
        skin_stretch,
    )
    from .kfa import load_kfa
    from .nifutil import load

    data = load(nif_path)
    objs: list = []
    names: list[str] = []
    for root in data.roots:
        stack = [root]
        while stack:
            obj = stack.pop()
            objs.append(obj)
            nm = ""
            if hasattr(obj, "name") and obj.name:
                nm = obj.name.decode("latin1")
            names.append(nm or type(obj).__name__)
            stack.extend(reversed(list(getattr(obj, "children", []) or [])))
    pose = load_kfa(kfa_path)
    table = load_bone_table(os.path.join(client_dir, "figures")) if client_dir else None
    if prefer_table is None:
        prefer_table = bool(
            client_dir and kfa_comes_from_gamedata(client_dir, nif_path, kfa_path)
        )
    mapping, source = map_kfa_to_nodes(
        objs, names, pose, bone_names=table, with_source=True, prefer_table=prefer_table
    )
    trusted = kfa_translation_trust(pose, objs, mapping, source=source)
    # Partial offset subsets are approximations (a subset of bones from
    # another rig): they visibly break grounding (amazon walks on air, Bear
    # floats 5 units). Only vetted pairs animate; everything else keeps bind.
    if not mapping_is_vetted(pose, mapping, source):
        mapping = {}
    tracks = []
    for kfa_idx in sorted(mapping):
        ch = pose.channels.get(kfa_idx)
        if ch is None:
            continue
        rot_keys = _channel_rotation_keys(ch)
        has_pos = bool(ch.translations and kfa_idx in trusted)
        # A channel can be translation-only (Beetle's root bob has no rotation
        # keys): dropping it left the body pinned while the legs animated —
        # the feet floated. Serve rotation-less tracks with positions only.
        if not rot_keys and not has_pos:
            continue
        node = names[mapping[kfa_idx]]
        track: dict = {"node": node}
        if rot_keys:
            track["times"] = [t for t, _q in rot_keys]
            track["quats"] = [list(q) for _t, q in rot_keys]
        if has_pos:
            track["ptimes"] = [k.time for k in ch.translations]
            track["positions"] = [list(k.value) for k in ch.translations]  # type: ignore[union-attr]
        tracks.append(track)
    # Refuse clips that explode the mesh under the viewer's skinning model
    # (rigs whose stored skin bind disagrees with their node tree — AfancB,
    # dracolich…): an empty track list sends the viewer to the bind pose.
    stretch = skin_stretch(data, pose, bone_names=table, prefer_table=prefer_table)
    if stretch is not None and (stretch[0] > STRETCH_LIMIT_MEAN or stretch[1] > STRETCH_LIMIT_MAX):
        tracks = []
    rate, duration = 1.0, pose.duration
    if client_dir is not None:
        timing = anim_timing(client_dir, kfa_path)
        if timing is not None:
            fps, frames = timing
            rate = fps / native_fps(pose)
            duration = frames / fps
    return {
        "kfa": os.path.basename(kfa_path),
        "duration": duration,
        "rate": rate,
        "tracks": tracks,
    }
