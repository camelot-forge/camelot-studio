"""Camelot Studio — .kfa animation files (NetImmerse/Gamebryo).

A .kfa is a NIF file where each "channel" = one animated bone root node:
an anonymous ``NiNode`` with a ``NiStringExtraData`` (the bone index) and a
``NiKeyframeController`` whose data holds the keys.

``kfa_summary`` gives a cheap overview; :func:`load_kfa` parses the channels
into sampleable TRS tracks so the exporter/viewer can pose a skeleton.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .nifutil import load

__all__ = ["Animation", "Channel", "Key", "evaluate", "kfa_summary", "load_kfa"]

Quat = tuple[float, float, float, float]  # (w, x, y, z)
Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class Key:
    time: float
    value: float | Vec3 | Quat


@dataclass
class Channel:
    index: int
    rotations: tuple[Key, ...] = ()
    translations: tuple[Key, ...] = ()
    scales: tuple[Key, ...] = ()
    xyz_rotations: tuple[tuple[Key, ...], ...] = ()  # type-4 fallback (3 axes)


@dataclass
class Animation:
    path: str
    version: int
    duration: float
    channels: dict[int, Channel] = field(default_factory=dict)

    def sample(self, index: int, t: float) -> tuple[Quat | None, Vec3 | None, float | None]:
        """Local (rotation, translation, scale) of a channel at time *t*."""
        channel = self.channels.get(index)
        if channel is None:
            return None, None, None
        return evaluate(channel, t)


def _channel_index(node) -> int | None:
    extra = getattr(node, "extra_data", None)
    raw = getattr(extra, "string_data", None) if extra is not None else None
    return _parse_index(raw)


def _parse_index(raw) -> int | None:
    if raw is None:
        return None
    text = raw.decode("latin1") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _index_datas(data) -> list[tuple[int, object]]:
    """Pair bone indices with keyframe data.

    Gamebryo KFAs store the ``NiStringExtraData`` (index) and the
    ``NiKeyframeController`` (data) as separate, parallel block streams; pyffi
    only links the first controller to its node. Pair them by block order, and
    fall back to the per-node ``extra_data``/``controller`` association when the
    counts do not line up.
    """
    indices: list[int] = []
    datas: list[object] = []
    for b in data.blocks:
        kind = type(b).__name__
        if kind == "NiStringExtraData":
            index = _parse_index(getattr(b, "string_data", None))
            if index is not None:
                indices.append(index)
        elif kind == "NiKeyframeController":
            datas.append(getattr(b, "data", None))
    if len(indices) == len(datas):
        return list(zip(indices, datas))
    pairs: list[tuple[int, object]] = []
    for node in data.roots:
        index = _channel_index(node)
        controller = getattr(node, "controller", None)
        if index is not None and controller is not None:
            pairs.append((index, getattr(controller, "data", None)))
    return pairs


def _value(value) -> float | Vec3 | Quat:
    if hasattr(value, "w"):  # Quaternion
        return (float(value.w), float(value.x), float(value.y), float(value.z))
    if hasattr(value, "x"):  # Vector3
        return (float(value.x), float(value.y), float(value.z))
    return float(value)


def _keygroup(group) -> tuple[Key, ...]:
    keys = getattr(group, "keys", None) if group is not None else None
    if not keys:
        return ()
    return tuple(Key(float(k.time), _value(getattr(k, "value", None))) for k in keys)


def _rotation_keys(data) -> tuple[tuple[Key, ...], tuple[tuple[Key, ...], ...]]:
    if not int(getattr(data, "num_rotation_keys", 0) or 0):
        return (), ()
    if int(getattr(data, "rotation_type", 0) or 0) == 4:
        groups = getattr(data, "xyz_rotations", None) or ()
        return (), tuple(_keygroup(g) for g in groups)
    quats = getattr(data, "quaternion_keys", None) or ()
    return tuple(Key(float(k.time), _value(getattr(k, "value", None))) for k in quats), ()


def load_kfa(path: str) -> Animation:
    """Parse *path* into an :class:`Animation` (one channel per animated bone)."""
    data = load(path)
    channels: dict[int, Channel] = {}
    duration = 0.0
    for index, kd in _index_datas(data):
        if kd is None:
            channels[index] = Channel(index=index)
            continue
        rotations, xyz = _rotation_keys(kd)
        channel = Channel(
            index=index,
            rotations=rotations,
            translations=_keygroup(getattr(kd, "translations", None)),
            scales=_keygroup(getattr(kd, "scales", None)),
            xyz_rotations=xyz,
        )
        channels[index] = channel
        for keys in (rotations, channel.translations, channel.scales, *xyz):
            if keys:
                duration = max(duration, keys[-1].time)
    return Animation(path=path, version=data.version, duration=duration, channels=channels)


def _lerp(a, b, u: float):
    if isinstance(a, tuple):
        return tuple(a[i] + u * (b[i] - a[i]) for i in range(len(a)))
    return a + u * (b - a)


def _slerp(a: Quat, b: Quat, u: float) -> Quat:
    dot = sum(a[i] * b[i] for i in range(4))
    if dot < 0.0:
        b = tuple(-c for c in b)
        dot = -dot
    if dot > 0.9995:
        out = tuple(a[i] + u * (b[i] - a[i]) for i in range(4))
    else:
        theta0 = math.acos(max(-1.0, min(1.0, dot)))
        theta = theta0 * u
        sin0 = math.sin(theta0)
        s0 = math.cos(theta) - dot * math.sin(theta) / sin0
        s1 = math.sin(theta) / sin0
        out = tuple(a[i] * s0 + b[i] * s1 for i in range(4))
    norm = math.sqrt(sum(c * c for c in out)) or 1.0
    return tuple(c / norm for c in out)


def _sample(keys: tuple[Key, ...], t: float):
    if not keys:
        return None
    if t <= keys[0].time:
        return keys[0].value
    if t >= keys[-1].time:
        return keys[-1].value
    for i in range(1, len(keys)):
        if t <= keys[i].time:
            k0, k1 = keys[i - 1], keys[i]
            span = k1.time - k0.time
            u = 0.0 if span <= 0.0 else (t - k0.time) / span
            return _lerp(k0.value, k1.value, u)
    return keys[-1].value


def _sample_quat(keys: tuple[Key, ...], t: float) -> Quat | None:
    if not keys:
        return None
    if t <= keys[0].time:
        return keys[0].value  # type: ignore[return-value]
    if t >= keys[-1].time:
        return keys[-1].value  # type: ignore[return-value]
    for i in range(1, len(keys)):
        if t <= keys[i].time:
            k0, k1 = keys[i - 1], keys[i]
            span = k1.time - k0.time
            u = 0.0 if span <= 0.0 else (t - k0.time) / span
            return _slerp(k0.value, k1.value, u)  # type: ignore[arg-type]
    return keys[-1].value  # type: ignore[return-value]


def _euler_xyz_to_quat(rx: float, ry: float, rz: float) -> Quat:
    cx, sx = math.cos(rx / 2), math.sin(rx / 2)
    cy, sy = math.cos(ry / 2), math.sin(ry / 2)
    cz, sz = math.cos(rz / 2), math.sin(rz / 2)
    return (
        cx * cy * cz + sx * sy * sz,
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
    )


def evaluate(channel: Channel, t: float):
    """Sample a channel at *t* -> (rotation wxyz, translation, scale)."""
    rotation: Quat | None = _sample_quat(channel.rotations, t)
    if rotation is None and channel.xyz_rotations:
        angles = [_sample(axis, t) for axis in channel.xyz_rotations]
        if all(a is not None for a in angles):
            rotation = _euler_xyz_to_quat(*angles)  # type: ignore[arg-type]
    translation = _sample(channel.translations, t)
    scale = _sample(channel.scales, t)
    return rotation, translation, scale


def kfa_summary(path: str) -> dict:
    data = load(path)
    indices = []
    controllers = []
    max_keys = 0
    for b in data.blocks:
        t = type(b).__name__
        if t == "NiStringExtraData":
            sd = b.string_data
            if sd is not None:
                txt = sd.decode("latin1") if isinstance(sd, bytes) else str(sd)
                if txt.strip().isdigit():
                    indices.append(int(txt))
        elif t == "NiKeyframeController":
            kd = getattr(b, "data", None)
            keys = 0
            if kd is not None:
                keys = max(
                    int(getattr(kd, "num_rotation_keys", 0) or 0),
                    int(getattr(kd, "num_translations", 0) or 0),
                )
            controllers.append(keys)
            max_keys = max(max_keys, keys)
    # Frames/fps come from the client's animnifs.csv; here we only report the
    # max key count (≈ frames when rotation is sampled per frame).
    return {
        "path": path,
        "version": hex(data.version),
        "blocks": len(data.blocks),
        "roots": len(data.roots),
        "bone_indices": indices,
        "index_order": sorted(indices),
        "controllers": controllers,
        "max_keys": max_keys,
    }
