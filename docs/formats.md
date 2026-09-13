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

### Up axis (verified on the corpus)

Composing the node tree, **every DAoC exporter (NetImmerse 4.1.0.12 through
Gamebryo 10.1) authors Z-up in NIF world space**. The glTF conversion is
therefore always `(x,y,z) → (x, z, −y)` (`nifutil.mat4_axis`, the synthetic
`__axes__` root node).

Evidence (row-major reading): for every biped the feet sit lowest along +Z and
the pelvis→neck direction is +Z — Zombie.NIF (4.1.0.12), Ettin/Ogre/Fomor
(4.2.1.0), Boar/humans (10.1) — and the skinning identity
`inv(meshWorld)·boneWorld·skinToBone` holds at the bind (Zombie 1.6e-05,
Ettin 5.3e-03). Quadrupeds (wolves) stand on +Z with a horizontal spine along
±Y; serpents lie horizontally with +Z still up. `feet_below_pelvis` in
`core/tests/_bbox.py` locks this in the render-regression tests.

History: an earlier version rule ("4.1.0.12/4.2.1.0 geometry is X-down, use
`(x,y,z) → (y, −x, z)`") was written while legacy matrices were read
transposed; once the row-major fix landed it exported Zombie/Ettin/Ogre lying
down. Removed (commit: "Drop the stale X-up axis rule…").

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
  Belt, legs). `core/camelot/skeleton.py` keeps the DFS pre-order of this
  skeleton (minus `Scene Root`) as a structural reference only.
- **NIF rotation storage is row-major** in DAoC files (DirectX/Max
  convention), contrary to nif.xml's "column-major" note: without the
  transpose the Gamebryo skinning identity misses by ~56 units, with it by
  5e-06, and KFA quaternions agree with their bind matrices (Bip01: 0.0°).
  Implemented as `nif_rot_cell` + row-major `legacy.mat33`.
- **Skinning (Gamebryo, verified on Boar)**: per bone
  `Mb = inv(meshWorld) * boneAnim * inv(boneBind) * meshWorld`
  (`rootParentToSkin * boneToRootParent * skinToBone`); baked into NIF-local
  vertices, axis node unchanged. Boar idle at mid-breath: mean edge
  5.509→5.445 (bending, no stretch), bbox preserved. glTF consumers beware:
  three.js r186 `GLTFLoader` binds with identity while `SkinnedMesh`
  refreshes the inverse from the live world, which drops parented mesh
  transforms — re-bind with the live bind world before rendering
  (`rebindSkinnedMeshes`).
- **KFA channel -> bone mapping** (`map_kfa_to_nodes`,
  `camelot gltf --pose/--bake-pose`): the client ships the engine's own
  index table, `figures/animnode.dat` — 175 bone names, one per line, index =
  channel (0 = Bip01, 1 = Bip01 Pelvis, no Footsteps; same table as
  blender_niftools PR #558). It fills every channel by name when the pair's
  offsets vouch for it (fraction of translated channels that resolve >= 0.7),
  including rotation-only channels (Ext/cloth) that offset matching cannot
  bind. Otherwise mapping is per-figure **bind-translation matching**
  (anchors + same-side runs): corresponding pairs store identical offsets
  (Boar.NIF vs BOAR_idle.kfa: 31 channels at 0.000). Near-ties (mirrored
  bones, a root at the origin) break by first-key rotation; `Scene Root` and
  `Bip01 Footsteps` are never targets. Pairs that score < 0.3 (wolf/lynx/giant
  idles authored on another bind) are refused entirely — bind pose beats a
  wrong pose. The old "shared 0..53 DFS = KFA space" claim is superseded
  (Boar Pelvis rides channel 1, not 2).
- **Grounding: the root translation track is not decoration.** In a vetted
  pair, **all** KFA translations replace the bind locals (absolute, engine-
  faithful; rotations too); scales stay at the bind (never animated). The
  root (`Bip01`, channel 0) track is the foot-planting compensation: dropping
  it because no key happens to equal the bind made 2HWolf float 2.2 units,
  3head 1.0, Zombie 1.1, Ettin 1.3, Wolf 2.3 (min-Y span over the loop; with
  the track applied all are <= 0.34, checked in the viewer). So
  `kfa_translation_trust` now trusts **every** translated channel of a vetted
  pair, and only falls back to the old "a key must return to the bind" test
  for partial mappings.
- **Only vetted pairs animate (`mapping_is_vetted`).** A mapping is vetted
  when it comes from the `animnode.dat` table (engine binding) or when offset
  matching resolved ~all translated channels (>= 0.9). Partial offset subsets
  (amazon 17/43, Bear 13/32, CATA boar 19/40) mix another rig's limb motions:
  the viewer/API refuses them and shows the bind pose (they walked on air).
  Coverage (2026-09-13): 306 vetted links (up to ~280 animate after the
  `skin_stretch` gate), 169 partials refused, 48 without a mapping. The
  `with_source=True` variant of `map_kfa_to_nodes` reports how a mapping was
  obtained.
- **Which KFA belongs to a figure: the gamedata chain first.**
  `monnifs.csv` (Name = NIF basename) -> `Anim Set` id -> `anims.csv` row ->
  animation id per state slot (Idle/C-Idle/Fly/Walk…) -> `animnifs.csv` ->
  `.kfa` file. This is how the client links figures whose animations are not
  named after them: `Arachite02.NIF` -> `sect_idle.kfa`, `amazon.nif` ->
  `Hoplite_Idle.kfa`, `amb_bat.nif` -> `amb_bat_fly.kfa`, `b_avmon01.nif` ->
  `undead_idle.kfa`, `2HWolf.NIF` -> `2HW_idle.kfa`. The viewer/API uses it
  (`anims.resolve_anim_from_gamedata`) with a per-client, mtime-keyed table
  cache; filename conventions + player aliases (`BritonM1` -> `britm_idle`)
  remain the fallback for Cata remodels and test roots. The chain linked 475
  figures (was 151 with filename matching alone); 306 are vetted and animate
  (see below), the rest keep the bind pose.
- **Skin-bind consistency gate (`skin_stretch`)**: some figures store
  `NiSkinData` bone transforms that disagree with their node tree — AfancB by
  exactly 302.56 units (the whole mesh shifts rigidly), dracolich by 392,
  phoenix, soulkeeper, amb_bird, dracolich… While the bind pose still looks
  right, the viewer's skinning model (`inv(meshWorld)·boneAnim·inv(boneBind)`)
  stretches those limbs (AfancB mean edge ratio 1.45). Clips are sampled at 4
  times; mean edge ratio > 1.12 (or max > 10) refuses the animation (bind
  pose shown instead: 30 of 474 links in 2026-09). The engine-exact model
  uses the **stored** transform `S`: world `p = boneAnim·S·v`, i.e. bake
  `Mb = inv(meshWorld)·boneAnim·S` and glTF
  `inverseBind = S·meshWorld⁻¹·axis⁻¹` (reduces to the current values when
  `S = inv(boneWorld)·meshWorld`, which holds for Boar/Ettin/Zombie and most
  files). Switching to it also shifts placement for inconsistent rigs, so it
  needs the viewer's grounding reworked first — measured, not yet shipped.
- **KFA playback timing comes from `animnifs.csv`, not the key stamps.**
  Every file is authored at 15 or 30 fps key density (`native_fps`: median
  rotation-key interval, snapped), and the game plays `frames` key intervals
  at `fps` frames per game second: rate `fps / native` over a loop of
  `frames / fps` game seconds. Verified exact on 12+ files (Boar idle
  `16,3,15` → 0.2x over 5.33s; run `15,30,30` → 2x over 0.5s; walk/run share
  one byte-identical file and still differ: 7 keys at 1x vs 16 keys at 2x).
  Missing rows and `fps` <= 0 (code-driven swims) fall back to stamped file
  timing. Served as `{rate, duration}` with the tracks; the viewer samples
  key time `(t_game % duration) * rate`.
- Items: statics (3/60 skinned), textured (58/60), attach nodes
  `BACK`/`BELT`/`GROUND`.

### Figure textures (resolved from gamedata, not the NIF)

Figures carry **no texture in the NIF** (a few have embedded/writable names,
rarely usable); the texture is resolved at runtime from `gamedata.mpk`:

```
figure NIF  ->  monnifs.csv (Name = NIF basename, lower-case, no ext)
            ->  monsters.csv (# = monnifs ID)  ->  skin IDs per slot
                (Body/Head/Arms/Gloves/Lbody/Legs/Boots/Cloak/Helm)
            ->  skins/mskins/pskins.csv (skin ID) -> texture name + Num
            ->  figures/skins/skinNNN.mpk (NNN = Num, 3 digits) -> the .dds
```

Texture names in the tables are `.tga` while the archive entry is `.dds`
(match on basename). Example: Boar `Body` skin 109 → `Num=3` →
`figures/skins/skin003.mpk` → `boar.dds` (512×256, DXT1). Implemented in
`core/camelot/gamedata.py`; `camelot gltf` embeds the resolved DDS via the
`CS_camelot_texture` extension. Pets/special figures not in `monnifs.csv`
fall back to the NIF's own `NiSourceTexture` names searched across the skin
archives (e.g. `fox_pet01` → `Fox_red01.dds` → `skin193.mpk`).

**Measured coverage** (617 figures, `notes`):
458 monster + 67 NIF-name fallback = **525 resolved (85%)**, 92 unresolved
(15%): player/class figures (customisable — the texture depends on the
character's chosen skin, not the figure), creatures/pets absent from
`monsters.csv` (2headed_Wolf, Dragon01…), Catacombs variants, LOD/utility
nodes.

**Multi-slot mapping**: a monster's skin slots map to meshes by the mesh's
node name (body/head/arms/limbs…), so the 2-headed dragon gets its
body/head/wing textures on the right meshes (`_slot_for_name` in `gltf.py`).

Improvement leads: (1) a pets/summons table for the creatures without a
monster row; (2) the player chain — the figure is only a base mesh, the
skin/armour is per-character (fig3 + `pskins`), needs a cleaner model;
(3) the fig3 wear pipeline.

## 4. KFA animation files (= NetImmerse NIFs, not EMotion FX)
- `anims/` = 4060 `.kfa` (154 MB), fully covered by `animnifs.csv`.
- Same architecture in all 3 versions (4.2.1.0, 4.2.2.0, 10.1.0.0): a list
  of channels, one per animated bone:
  ```
  NiNode (anonymous) + NiStringExtraData (numeric bone index)
    + NiKeyframeController → NiKeyframeData(rot_keys=N≈frames)
  ```
- KFA channels address bones **by index into a per-skeleton table**: classic
  creatures share index ranges (`2HW_idle` and `BOAR_idle` share 0..77),
  while CATA remodels reshuffle theirs (their arm slots address different
  offsets — matched only when translation-decisive, otherwise left on bind).
  `game.dll` finds bones **by name** at runtime; our tooling resolves
  index→node per figure by bind-offset matching (§3).
- `.kfa` key times are **seconds at the authored key rate** (15 classic,
  30 CATA-era); the game loops `frames` of them at `fps` (see §3 timing).
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
0xFFFFFFFF = null ref, footer `[u32 num_roots][roots...]`. Rotation matrices
are row-major on disk in ALL DAoC exporters (4.x and 10.1 alike, contrary to
nif.xml's column-major note) — proven per family by the skinning identity
`inv(meshWorld) * boneWorld * skinToBone == I` (Boar 10.1: 5e-06, Ettin
4.2.1: 5e-03; transposed: ~56/~70). Versioned facts:

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