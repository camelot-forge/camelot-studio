"""Camelot Studio — .kfa animation file analysis (NetImmerse/Gamebryo).

A .kfa is a NIF file where each "channel" = one animated bone:
  anonymous NiNode + NiStringExtraData (bone index) + NiKeyframeController(+Data).
"""
from __future__ import annotations

from .nifutil import load

__all__ = ["kfa_summary"]


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