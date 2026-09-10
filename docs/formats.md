# DAoC client formats — reference

What we know about the Dark Age of Camelot client data formats (client
1.127). Sources: hands-on exploration of the real client (2026-09), pyffi's
nif.xml, DAOC_Navigator as behavioral reference. Facts marked *open* are
not yet fully resolved.

## 1. Data model & lookup tables

Everything is resolved client-side through CSV tables packed in
`gamedata.mpk` (51 tables, exported to `notes/gamedata_csv/` locally):

- `monsters.csv` (2249 rows) → per monster: a `monnifs` id, body/skin ids…
- `monnifs.csv` (860) → NIF file name + **animation set id**
- `anims.csv` (495) → per animation set: state columns (Walk/Run/Idle/Death/
  Flinch/Combat Idle…) referencing animation ids
- `animnifs.csv` (4080) → animation id → `.kfa` file + frames/fps/clamp/loop
- `skins.csv` (3267) / `pskins.csv` / `mskins.csv` (2646) → humanoid/monster
  skins; `fig3*.csv` (map/parts/facemap/haircolormap/decalmap/tofigmain…);
  `figtofig3main.csv` / `fig3tofigmain.csv`
- world: `items.csv`, `objects.csv`, `nifswap.csv` (house normal/burned/
  reconstructed), `siege*.csv`, `moneffects*.csv`, `spells.csv`…

**Proven end-to-end chain (the Boar, monster 99):**
`monsters.csv:99 → monnifs:221 (Boar, set 12) → figures/Boar.NIF` +
`anims.csv set 12 (Idle=320…) → animnifs:320-327 → anims/BOAR_*.kfa`.
CSV headers span 2-3 lines; files on disk are case-insensitive to the client
(`BOAR_idle.kfa` ↔ `boar_idle.kfa`).

## 2. NIF versions in the client

| Version | Where | Readable by |
|---|---|---|
| Gamebryo 10.1.0.0 | figures (112/120 sampled), fig3 parts, most items, KFA (164/400) | pyffi 2.2.3 |
| NetImmerse 4.2.1.0 / 4.2.2.0 | old figures (Ettin, fox_pet01, ghost, siegetower…), old items/effects, KFA (236/400) | pyffi for KFA only; meshes need the built-in `legacy.py` reader |
| NetImmerse 4.1.0.12 | the oldest items (3/60 sampled) | `legacy.py` |

## 3. Figures & fig3 architecture

- Figures (humans AND creatures) are **vertex-skinned** (`NiSkinInstance`/
  `NiSkinData`/`NiSkinPartition`) on a **Bip01 rig** (3ds Max Biped) — not
  rigid hierarchies. Roots: `Scene Root` / `Arborist` / asset name.
- Embedded controllers in figure NIFs are **empty shells** (rot_keys=0):
  the NIF carries geometry + skinning + bind pose; animation comes from the
  `.kfa` at runtime.
- **Runtime attach nodes** (game.dll strings): `Bip01 L/R Held/Back/Belt/
  Shield` — weapons/shields hook onto these named nodes. Keep the names for
  any model swap.
- **fig3 pieces** (Catacombs era, in `figures/fig3/figNNN.mpk`): minimal
  wearables — bind-pose, skinned meshes, **0 controllers, 0 textures**, with
  a `NiStringsExtraData` **`bonelist`** naming the bones that deform the mesh
  (e.g. `Bip01 L/R UpperArm/BicepTwist/Forearm/ForeTwist`) + a base-set
  marker (`cthArms01`). fig3 meshes attach **by bone names**, KFAs reference
  bones **by index** — the two constraints of the game.
- Skeletons: `%s_skeleton.nif` (game.dll string) + `figures/skeleton.nif` =
  canonical human Bip01, **51 bones** (`Scene Root → Bip01 → Footsteps →
  Pelvis → Spine1/2 → Neck → Head`, full arms/fingers, Held/Shield/Back/
  Belt, legs). The KFA bone index of a bone = **DFS pre-order position in
  this skeleton, excluding `Scene Root`** — *verified* for indices 0..53
  (`core/camelot/skeleton.py`): the `2HW_idle.kfa` channels land exactly on
  the expected arm/shield bones (11=R Forearm, 12=R Hand, 23=R Shield, 24=L
  Clavicle…), and a figure's idle/walk/run share the same channel set (only
  the keys differ). Indices 54+ are extra shared bones (full-hand fingers,
  twists, creature appendages) absent from `skeleton.nif` — *open*. The
  master table (0..114) exceeds the human skeleton.
- Items: statics (3/60 skinned), textured (58/60), attach nodes
  `BACK`/`BELT`/`GROUND`.

## 4. KFA animation files (= NetImmerse NIFs, not EMotion FX)

- `anims/` = 4060 `.kfa` (154 MB), fully covered by `animnifs.csv`.
- Same architecture in all 3 versions (4.2.1.0, 4.2.2.0, 10.1.0.0): a list
  of channels, one per animated bone:
  ```
  NiNode (anonymous) + NiStringExtraData (numeric bone index)
    + NiKeyframeController → NiKeyframeData(rot_keys=N≈frames)
  ```
- The `NiStringExtraData` **index** is stable across creatures
  (`2HW_idle` and `BOAR_idle` share 0..77) — a shared canonical skeleton
  template (Biped + extras).
- `game.dll` embeds the Gamebryo engine statically (`NiMain/NiAnimation/
  NiCollision… Version 10.1.0.0`) — no EMotion FX anywhere.
- ⚠️ 10.1.0.0 files list each block type once (header table) while 4.2.x
  repeats it per block: naive text-type counting is not comparable between
  versions.

## 5. MPAK archives (`*.mpk` and `*.npk`)

Single format: magic `MPAK`; 21-byte header; zlib stream 1 = archive name;
zlib stream 2 = directory of 0x11c-byte records (NUL-terminated name + u32
offset at 0x110); then zlib data streams. Directory offsets are
**base-relative** (base = end of the directory stream). **Read and write are
implemented** (`core/camelot/mpak.py`; write validated by re-reading every
retail entry and by the workspace reference reader). Virtual URLs
`archive://….mpk:file` in `loading.dat`. Zone archives:
`zones/zoneNNN/datNNN.mpk, csvNNN.mpk, lodNNN.mpk, terNNN.mpk, texNNN.mpk`;
dungeons: `zones/Dnifs/*.npk`.

**Header (17 bytes after `MPAK`), reverse-engineered from the retail corpus:**

| field | value | status |
|---|---|---|
| byte 4 | `0x02` | constant |
| bytes 5..10 | 6-byte checksum | *open* — not decoded |
| bytes 11,12 | `0x06 0x07` | constant |
| byte 13 | `len(name_stream_compressed) + 8` | decoded |
| bytes 14..16 | `0x09 0x0a 0x0b` | constant |
| byte 17 | checksum byte (deterministic fn of entry count, not a plain count) | *open* |
| bytes 18..20 | `0x0d 0x0e 0x0f` | constant |

`byte 13` verified across 40+ archives (`.mpk` and `.npk`); `byte 17` and the
6-byte checksum are deterministic per file but not a simple adler32/crc32 of
the file or directory — *open research*. Reference readers (mharj's
dempak.py 2002, our `MpakArchive`) skip the header past the magic, so
round-trips prove the structural bytes; whether the game validates the
checksums needs in-game confirmation. `write_mpak` emits deterministic
placeholders for the two *open* fields.

## 6. The NetImmerse 4.x legacy reader — block-level facts

`core/camelot/legacy.py` reads 4.1.0.12 / 4.2.x meshed NIFs that pyffi
cannot parse. Layout: `[u32 type_len][type_name][body]`, little-endian,
0xFFFFFFFF = null ref, footer `[u32 num_roots][roots...]`. Versioned facts:

- Booleans: u8 from 4.1.0.1; **0x00 and 0xFF both mean false** (exporter
  wrote 0xFFFFFFFF-style false).
- `NiStringExtraData` (4.x): **no name field** — next-ref u32 +
  bytes-remaining u32 + sized string (strings can be >16 KB; cap 4 MB).
- `NiKeyframeData`: `rotation_type` u32 present only when
  num_rotation_keys>0; types 1/2 = 20-byte quat keys (time+wxyz), type 3
  (TBC) = 32 B, type 4 (XYZ) = unknown float + 3 per-axis `KeyGroup<float>`.
- `KeyGroup<T>`: count u32 + **optional** interpolation u32 (present iff
  count>0 and the peeked u32 is a plausible KeyType 1..7; per-file
  convention). Quadratic keys carry fwd/back tangents (2× payload),
  TBC adds 3 floats.
- `NiUVData` = 4 × `KeyGroup<float>` (per-axis U/V translation & scale),
  NOT vec2 keys.
- `NiTriShapeData`/`NiTriStripsData`: geometry common + `num_triangles` u16
  + triangles/match-groups or strips/points.
- `NiSourceTexture`: objectnet first; embedded pixel data (use_external=0)
  has no file name in 4.x — unknown byte + `NiPixelData` ref.
- `NiPixelData` = masks/bpp/unk bytes + extra u32 + mipmap table +
  Num Pixels + raw blob (the extra u32 is DAoC-specific).
- `NiTexturingProperty`: apply mode u32 + texture count u32 + 7 slots
  (base/dark/detail/gloss/glow/bump/decal0), desc = source/clamp/filter/
  uv/ps2_l/ps2_k; **4.1.0.12 appends an extra u16** (22-byte descs vs 20).
- `NiStencilProperty` (DAoC) = 43 bytes: objectnet + flags u16 + enabled
  u8 + 7×u32.
- `NiSkinData`: `has_weights` flag exists only from 4.2.1.0; older files
  always store weights.
- `NiLODNode` (4.x) derives from NiSwitchNode: children/effects first,
  then switch_index u32, lod_center vec3, num_lod_levels u32, per-level
  (near f32, far f32).
- `NiTextureEffect` (4.1.0.12): extra u16 after PS2 K.
- Particle family: `NiParticleSystemController`, modifiers, `NiUVData`;
  `NiParticleMeshesData` = geometry-like header + radius, num_active,
  sizes, rotations + trailing u32 (empirical). `NiSphericalCollider`,
  `NiLightColorController` also supported.

## 7. Coverage & remaining cases

Full client NIF corpus (5089 files, `--read-only`): **5087 OK / 2 errors**.
The 2 remaining errors are Gamebryo **10.1.0.0 housing files** failing
inside **pyffi** (not the legacy reader):
`phousing/nifs/int_floor_table_Frontier_{Albion,Midgard}.NIF` —
`string too long` while reading a "BC…" string. *Open:* pyffi patch or a
10.1.0.0 fallback.

## 8. fig3 archive map

| Archive | Content |
|---|---|
| fig001 | arms01 + boots01-08 |
| fig002-003 | body01-08 |
| fig004 | cloak01-03 + legs01 + lbody01-08 |
| fig005-006 | gloves01-08 |
| fig007-008 | heads per race |
| fig009-015 | special sets / sparse races |
| fig016 | mounts (horse, nightmare, undead, clydesdale + barding) |
| fig017 | realm kings |
| fig018-021 | misc/special |
| fig022-023 | adr_dragonsworn_* set |
| fig024 | hair per race |
| sfig001-008 | fig3 creatures (bear, stag, werewolf…) |

"01-08" = armor tiers (base → epic/classic).

## 9. Model swap constraints (for the edit pipeline)

1. Replacing a figure mesh: preserve the **Bip01 node tree and order** (the
   KFA index space), the attach-node names, the `Scene Root` root, and the
   scale/bind pose.
2. Making a fig3 piece: skinned mesh whose `bonelist` names exactly the
   shared skeleton's bones + a compliant NiSkinData.
3. `.kfa` are reusable as-is if bone order/structure is unchanged; any
   re-rigging requires rewriting the `.kfa` (feasible: documented format,
   pyffi reads/writes 4.2.x and 10.1.0.0).

## 10. Prototype 1 (cold asset swap) — protocol

Recommended guinea pig: `figures/Boar.NIF` (simple animal, no fig3/archive/
texture, full chain known, mount rig = good anim regression test).
1. **Backup first**: `Boar.NIF`, `Boar_CATA.NIF`, `Boar_Keggers.nif` + the
   `anims/BOAR*.kfa` set.
2. Replace `Boar.NIF` with an editorialized copy (retopologized mesh
   keeping the Bip01 tree).
3. Launch the client; verify loading without integrity checks + walk/run/
   idle/attack intact. Never run without a backup.
Simpler static alternative first: an item on a `GROUND` node
(e.g. `items/jackolantern-GROUND.NIF`).