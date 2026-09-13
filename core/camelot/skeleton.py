"""Camelot Studio — skeleton DFS reference (index -> Bip01 name).

``BONE_INDEX`` is the DFS pre-order of ``figures/skeleton.nif`` excluding
``Scene Root`` (structural fact, locked by corpus test). It was previously
assumed to be the KFA channel space, but per-figure translation matching
(Boar.NIF vs BOAR_idle.kfa: 27 channels at 0.000 units, ``map_kfa_to_nodes``)
shows KFA indices resolve against the figure's own bind offsets — e.g. Boar
Pelvis rides channel 1 while this table puts Pelvis at 2 — so this table is
kept as a DFS reference only, not as the animation mapping.
"""
from __future__ import annotations

# Verified index -> name, DFS pre-order of skeleton.nif excluding "Scene Root".
# Derived with notes and cross-checked against 2HW_idle.kfa.
BONE_INDEX: dict[int, str] = {
    0: "Bip01",
    1: "Bip01 Footsteps",
    2: "Bip01 Pelvis",
    3: "Bip01 Spine",
    4: "Bip01 Spine1",
    5: "Bip01 Spine2",
    6: "Bip01 Neck",
    7: "Bip01 Head",
    8: "Bip01 Helmet",
    9: "Bip01 R Clavicle",
    10: "Bip01 R UpperArm",
    11: "Bip01 R Forearm",
    12: "Bip01 R Hand",
    13: "Bip01 R Finger0",
    14: "Bip01 R Finger01",
    15: "Bip01 R Finger02",
    16: "Bip01 R Held",
    17: "Bip01 R Finger2",
    18: "Bip01 R Finger21",
    19: "Bip01 R Finger22",
    20: "Bip01 R Finger1",
    21: "Bip01 R Finger11",
    22: "Bip01 R Finger12",
    23: "Bip01 R Shield",
    24: "Bip01 L Clavicle",
    25: "Bip01 L UpperArm",
    26: "Bip01 L Forearm",
    27: "Bip01 L Hand",
    28: "Bip01 L Finger0",
    29: "Bip01 L Finger01",
    30: "Bip01 L Finger02",
    31: "Bip01 L Held",
    32: "Bip01 L Finger2",
    33: "Bip01 L Finger21",
    34: "Bip01 L Finger22",
    35: "Bip01 L Finger1",
    36: "Bip01 L Finger11",
    37: "Bip01 L Finger12",
    38: "Bip01 L Shield",
    39: "Bip01 R Back",
    40: "Bip01 L Back",
    41: "targetchest",
    42: "Bip01 R Thigh",
    43: "Bip01 R Calf",
    44: "Bip01 R Foot",
    45: "Bip01 R Toe0",
    46: "Bip01 L Thigh",
    47: "Bip01 L Calf",
    48: "Bip01 L Foot",
    49: "Bip01 L Toe0",
    50: "Bip01 R Belt",
    51: "Bip01 L Belt",
    52: "body",
    53: "Editable Mesh",
}

# Highest KFA bone index seen in the retail anims corpus (open range above it).
MAX_VERIFIED = max(BONE_INDEX)


def bone_name(index: int) -> str | None:
    """Return the canonical bone name for a KFA *index*, or None if unknown."""
    return BONE_INDEX.get(index)