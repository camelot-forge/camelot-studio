# DAoC client formats — reference

What we know about the Dark Age of Camelot client data formats (client
profile: `DAOC_CLIENT V2`, historically labelled 1.127). Sources: hands-on
exploration of the real client (2026-09), pyffi's nif.xml, DAOC_Navigator as a
behavioural reference. Facts marked *open* are not yet fully resolved. The
documentation audit is closed; current code and reproducible client
observations take precedence over older prose snapshots.

## 1. Data model & lookup tables

Everything is resolved client-side through CSV tables packed in
`gamedata.mpk` (51 tables, exported to `notes/gamedata_csv/` locally):

- `monsters.csv` (retained snapshot: 2249 rows) → per monster: a `monnifs` id,
  body/skin ids…
- `monnifs.csv` (retained snapshot: 860) → NIF file name + **animation set id**
- `anims.csv` (retained snapshot: 495) → per animation set: state columns
  (Walk/Run/Idle/Death/Flinch/Combat Idle…) referencing animation ids
- `animnifs.csv` (retained snapshot: about 4080 rows) → animation id → `.kfa`
  file + frames/fps/clamp/loop
- `skins.csv` / `pskins.csv` / `mskins.csv` → humanoid/monster skins;
  `fig3*.csv` (map/parts/facemap/haircolormap/decalmap/tofigmain…);
  `figtofig3main.csv` / `fig3tofigmain.csv`
- world: `items.csv`, `objects.csv`, `nifswap.csv` (house normal/burned/
  reconstructed), `siege*.csv`, `moneffects*.csv`, `spells.csv`…

**Proven end-to-end chain (the Boar, monster 99):**
`monsters.csv:99 → monnifs:221 (Boar, set 12) → figures/Boar.NIF` +
`anims.csv set 12 (Idle=320…) → animnifs:320-327 → anims/BOAR_*.kfa`.
CSV headers span 2-3 lines; files on disk are case-insensitive to the client
(`BOAR_idle.kfa` ↔ `boar_idle.kfa`). **Name matching is also
separator-insensitive**: the tables spell names differently from the files
(`FrostGiant` in monnifs vs `Frost_Giant.NIF`, `svartalfm` vs
`Svartalf_M.NIF`), so lookups compare alphanumerics-only keys. And a few
figures have **no monnifs row at all**: they are then linked by their
animation *set* name (`Boar Keggars` ↔ `Boar_Keggers.nif`, including one
edit-distance spelling variant), with the rig gates still validating the
result.

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
- **Player base figures** (`figures/B*.NIF`, e.g. `BSaracenF.NIF`) stack
  **every equipment variant of every slot in one file**: 10 `Body`
  (`Body1..Body5` + `b` variants, ~8-15 extra shoulder verts each), 6 `Arms`,
  5 `Boots`, 4 `Legs`, 3 `Gloves`, 4 `Lbody`, `HeadA1..5` + `HeadB1..5`,
  3 `Cloak`, plus 4 whole-body `Lod1a..d` levels (distant-only). The game
  shows one variant per slot. The client resolves the equipped material and
  appearance from its skin/item tables; the current Studio native builder does
  not yet reproduce that complete `pskins`/item/extension pipeline and uses
  fixed preview sheets.
- **The classic `HeadA*/HeadB*` pair cannot be composed with one texture
  pass**: projecting both meshes in world space shows two *overlapping head
  shells* of the same size (z ≈ 57-68 for `BSaracenF.NIF`), and `HeadB`'s UV
  set 0 is another face mapping (ear/teeth/eye islands), not a hair layout —
  the live engine blends them with the decal/multitexture hair shader
  (`ps_HairMaterialShader1x`, `MultiTexture Decal Map`). Any single-texture
  choice renders a blob or a face on the back of the skull, so the viewer
  does **not** draw them: the exporter hides `HeadA*/HeadB*` and attaches the
  Catacombs fig3 head/hair instead (`core/camelot/figure.py`,
  `core/camelot/fig3.py`). This is what the live client and game-accurate
  viewers do — see the fig3 entries below.
- **fig3 player head/hair**: one head mesh per race (`Sar_f_Head01`, full
  Bip01 skeleton embedded, 541 verts / 800 tris for Saracen female) rendered
  with a face sheet selected through `fig3facemap.csv`, plus a hair style
  selected from the fig3 map. The builder resolves the hair triplet
  (`S/T/M`) from `fig3haircolormap.csv`: the base sheet `S`, the
  multiplicative tint `T`, and the `M` decal baked into the served sheet as
  an alpha overlay (`appearance.hair_composite`; `FUN_004174e2` passes base,
  tint and decal to the hair material, and `ps_HairMaterialDecalShader1x` is
  the decal variant). The separate `fig3decalmap.csv` table is not the hair
  `M` cell and is not currently exposed as a selectable decal.
  The `fig3facemap.csv` V2 snapshot has seven populated cells (the client
  descriptions expose seven creation faces); an earlier "eighth face" claim
  does not reproduce on this corpus. The **eyes are separate geometry**
  inside the head mesh (vertices bound to `Bip01 L/R Eyeball`). The exporter
  bakes a face/eye-sheet product into an `Eyes` texture; the exact client
  artwork interpretation, colour space and pixel-level fidelity remain
  verification items.
- **Tints** (`fig3tints.csv`: id + name + Red/Green/Blue) are per-slot
  multiplicative colours. `fig3skincolormap` maps skin-tone choices to face
  tints; `fig3haircolormap` maps hair colours to `T` values. The exporter
  carries the tint on the slot texture extension and the viewer applies it.
- **Only some hair styles have colour sheets** (the exact set depends on the
  client profile), and `fig3haircolormap.csv` can point a style at another
  style's or race's sheet. The current assembler uses a sheet when it can
  resolve one, but this fallback is not an engine-fidelity guarantee. The
  fig3map cell list is **positional**: an empty cell is a deliberate "no
  piece" (the hair `Bald` styles) and survives as a gap (`fig3.slot_ids`,
  `fig3.part_at`); only trailing empties are trimmed. Style numbers must be
  read positionally, not as indices into the non-empty pieces.
  Native assembly uses the per-race runtime skeleton and performs bind
  compensation for attached pieces. Skeleton-less pieces use the canonical
  fig3 reference; do not assume every piece has an identity bind or that the
  classic head/neck alignment applies to the native path.
- **`fig3parts.csv` reuses ids** for different files (id 199 is both
  `hal_m_Hair01` and `hig_m_Hair03`), so an id maps to several candidates
  and `fig3.parts_of` filters them by race description. `monnifs.csv`
  descriptions also come with reversed word order ("Male Half Ogre" vs
  fig3map's "Half Ogre Male"); `fig3.race_id` matches on word sets as a
  fallback, and requires multi-word races for partial matches so a
  one-word description like "Heavy" does not capture "Half Orc Heavy".
- **`fig3parts.csv` column 6 is the `Double`/`Sided` flag**, read as the
  literal `"1"`: 12 hair pieces are explicitly single-sided
  (`bri_f_Hair01`, Firbolg female 1/3/4, Celt male Hair 4…). A boolean
  coercion of the cell is wrong — `"0"` is truthy — and it lit single-sided
  hair from behind. The flag is per piece, not per slot, and travels with
  the piece through assembly, glTF export and the viewer material.
- **`pskins.csv` is the armour texture table**: `Textual Name` is the
  material family (`Plate`, `Hard Leather`, `Chain`, `Cloth`…) and each
  family declares one sheet per body piece and gender —
  `b_plt1_body1m.tga` / `b_plt1_body1f.tga`, and genderless
  `b_plt1_arm1` / `_glov1` / `_lbody1` / `_leg1` / `_boot1`. Facts worth
  keeping:
  - the gender is a trailing `1m`/`1f` (or `_m`/`_f`), not a bare letter;
    require a digit or underscore before it, or a stem that merely ends in
    those letters (`…_helm`) reads as gendered;
  - `lbody` is a distinct piece from `legs`, not a variant: its UVs sample
    the **upper half of the same legs atlas**. Match the longest token
    first, or `lbody` is swallowed by `body` and `leg`;
  - the same table carries the rest of the skin family — shields, banners,
    hats, guild cloth (176 of 3163 rows resolve to no body-piece slot) — so
    filter by piece, not by row;
  - a slot's extra rows (`helm1`, `helm2`) are the item's mesh
    **extensions**, not more genders: the extension is carried by the item
    and resolved server-side, so keep the first row and do not treat them
    as variants here;
  - `archive num` resolves to `items/pskins/pskin%03d.mpk` (50 archives on
    V2). 1953 of the families are wearable; 85 cover the full piece set.
- Skin archives live per table: `figures/skins/skinNNN.mpk` (skins.csv),
  `figures/Mskins/mskinNNN.mpk` (mskins.csv, case varies on disk) and
  `items/pskins/pskinNNN.mpk` (pskins.csv); Cata face sheets ship in a
  later archive than the tables claim, so lookups fall back to a scan.
  DXT1 sheets that cut texels with the 1-bit alpha mode are re-encoded to
  uncompressed A8R8G8B8 before embedding: three.js `DDSLoader` maps DXT1 to
  an opaque RGB format, which would paint the cut texels black.
- **Slot naming**: fig3map slot 2 is `LBody` (attached as `Lbody`, the
  classic mesh spelling); it is a real piece, not an alias of the legs —
  its UVs cover the upper half of the same legs atlas, so it takes its own
  `pskins` `lbody1` sheet rather than the `leg1` one. Only Highlander
  (ids 13/14) ships the fig3 `LBody` piece, so the other 34 rows have no
  such mesh. The skeleton stem (`fig3.race_short`) uses the fig3map
  description: Norseman rows are spelled `Norse Male/Female` in fig3map but
  "Norseman" in monnifs, so both tokens map to `nor`.
- The viewer classifies these meshes by name and hides the rest
  (`web/src/lib/character.ts`); note the GLTFLoader name mangling — it
  strips `:` so `Body1:0` becomes `Body10`, hence the classifier reads the
  glTF document's mesh names, not the THREE object names.
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
- **Skinning (Gamebryo, corpus-validated)**: for the current stored-bind path,
  `Mb = inv(meshWorld) * boneAnim * S`, where `S` is the stored
  `NiSkinData` transform; glTF uses `inverseBind = S * inv(meshWorld)`.
  The earlier node-tree bind formula remains useful for comparison, but it is
  not the current exporter contract. `skin_stretch` is an empirical safety
  heuristic. three.js r186 requires an explicit live bind matrix
  (`rebindSkinnedMeshes`) for parented meshes.
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
- **Player idles: the Catacombs clips, applied with absolute channels.** The
  character builder resolves the race's Cata figure (`sar_f` & co, whose
  monnifs rows link the `Cat_I_*` sets the live client plays) and serves
  every channel the KFA carries, absolute — rotations and translations,
  statics included. This matches the Studio playback rule and selected
  browser checks, but is not a complete proof for every client state. The
  classic sets (`I_HM/I_HF`, `I_SVM`…) remain fallbacks; they were authored on
  pre-fig3 rigs and can distort the assembled native figure.
- **Two-tier mapping: the client's link decides; a deformation heuristic
  filters risky pairs.** A gamedata-linked clip
  (`kfa_comes_from_gamedata`) is mapped through `animnode.dat`; fallback
  filename links keep the conservative offset gate. `skin_stretch` measures
  the result and refuses selected bad deformations. It is not a client physics
  gate. Historical coverage totals are snapshots and must be regenerated for
  the pinned `DAOC_CLIENT V2` corpus.
- **Foreign-rig clips on the native fig3 path** (2026-09-23): the male Troll
  link (`tro_m` → set 263) resolves `Cat_troll_idle.kfa`, whose absolute
  translations sit 1.8-3.6 units off `tro_m_skeleton.nif` (`Bip01
  Head/Neck/EyeLids`) and sheared the face. The builder enables
  `gltf.foreign_rig_gate`: a vetted clip whose mean closest-key-to-bind
  distance exceeds `FOREIGN_RIG_MEAN_TOL` (1.5) is treated as authored on
  another rig and its translations are dropped (rotations still play).
  Classic figures are not gated (their stored bind legitimately disagrees
  with the node tree). See `character-rules.md` §8.18.
- **Sitting idles lose to standing ones.** Some sets fill their Idle slot
  with a sitting clip: the black cat's `Idle`/`Idle1` are
  `amb_blackcat_SIT_TAILSWIPE`, `Idle2` `SIT_IDLE`, `Idle3`
  `amb_blackcat_STAND_IDLE`. `resolve_anim_from_gamedata` penalizes
  `sit`/`tailswipe` filenames and only falls back to them when nothing else
  exists, so the viewer's default animation is the standing idle.
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
- **Stored-bind skinning (corpus-validated, 2026-09-13)**. Some figures store
  `NiSkinData` transforms that disagree with their node tree. The current
  exporter uses the stored transform `S`:
  - glTF `inverseBind = S · meshWorld⁻¹`;
  - CPU bake `Mb = inv(meshWorld)·boneAnim·S`;
  - `skin_stretch` measures the result against that stored bind and refuses
    selected high-stretch cases. This is an empirical safety heuristic, not
    a complete reverse-engineered client gate.
  The formula has been checked on stored-bind figures including
  AfancB/dracolich/phoenix/soulkeeper/amb_bird/ambrat/CwHuman/Dragon01, with
  selected visual checks. Some player-race pairings remain refused.
- **Grounding/framing uses the engine bind** (`bindBoxOf`): bounds are
  measured with three.js' own skinning at rest (in the viewer the bound box
  also unions poses sampled across the idle, so root tracks that travel —
  amb_bird flying, the afanc diving — stay in frame and never clip the
  grid). `rebindSkinnedMeshes` must pass an explicit `bindMatrix`
  (`bind(skeleton, matrixWorld)`): calling `bind(skeleton)` without it makes
  three.js recompute the skeleton's inverse bind matrices from the live
   pose, silently discarding the stored-bind transforms exported above.
- **KFA rotations come in two storages**: quaternion keys and **type-4
  per-axis Euler float tracks** (`xyz_rotations`). DAoC uses both, sometimes
  within the same clip, and some classic mobs are Euler-only (Beetle, sharks,
  snakes, ballistas…: 116 KFAs xyz-only, 1578 mixed). The viewer used to drop
  every Euler channel (Beetle "not animated"); `anims._channel_rotation_keys`
  now samples the three axis tracks at union key times plus 1/30 s steps
  (the engine interpolates per axis and composes; slerping between sparse
  composed quaternions made feet drift), composes a quaternion with
  `kfa.evaluate`, and emits a normal `{times, quats}` track. `native_fps`
  counts the densest axis as well.
- **Translation-only channels produce tracks too**: the Beetle's root has a
  position track and no rotation keys at all. Tracks are now emitted when a
  channel has rotations, translations (trusted), or both; the viewer applies
  positions in a separate pass. Dropping the root track pinned the body while
  the legs animated — the beetle's "floating feet" (raw min-Y span 3.76
  before, 0.52 after).
- **KFA playback timing comes from `animnifs.csv`, not the key stamps.**
  Every file is authored at 15 or 30 fps key density (`native_fps`: median
  rotation-key interval, snapped), and the game plays `frames` key intervals
  at `fps` frames per game second: rate `fps / native` over a loop of
  `frames / fps` game seconds. Missing rows and `fps <= 0` fall back to
  stamped file timing.
  - **A KFA basename is not a unique key.** 92 of the 3978 basenames carry
    several `animnifs` rows, and 58 of those disagree on (type, frames, fps)
    — `ettin_swim.kfa` is both a 10-frame 15 fps `loop` and a 40-frame 30 fps
    `clamp`. Taking the first row invents a duration, so the by-basename
    lookup now refuses a basename whose rows disagree and falls back to
    stamped timing.
  - **States are keyed by animnifs id, through the set's slot names.**
    `anims.anim_states` walks `monnifs -> anims -> animnifs` and returns the
    set's slots (`Idle`, `C-Idle`, `Walk`, `Run`, `Fly`, `EM Wave`… — 166 for
    a player figure), each with its file, id, `frames / fps` duration and the
    `type` column's `loop`/`clamp` flag. That is the only stable identifier
    a server can use to say which state a character is in, and it is immune
    to the duplicated basenames above. `rate` needs the clip's own key
    density, so it is computed on demand: opening all 166 clips to fill it
    turned the states route into a 22s request.
  - **`anims.csv` rows are keyed by set id, never by description** —
    descriptions repeat across sets, so a name match would let an unrelated
    figure's row shadow the real one.
  - `clamp` clips are one-shots: the clip response carries the flag and the
    viewer holds the last frame instead of wrapping. A missing flag means
    loop, so older responses keep playing.
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

**Historical coverage snapshot** (617 figures, `notes`):
the older run reported 458 monster + 67 NIF-name fallback = **525 resolved
(85%)**, but it did not validate final rendered bytes and predates the current
`DAOC_CLIENT V2` profile. Re-run and version the report before using this as a
current KPI. Player/class figures remain a separate material pipeline rather
than a fixed figure texture.

**Multi-slot mapping**: a monster's skin slots map to meshes by the mesh's
node name (body/head/arms/limbs…), so the 2-headed dragon gets its
body/head/wing textures on the right meshes (`_slot_for_name` in `gltf.py`).

**Cloak UV set**: cloak meshes (classic `Cloak1..3` and fig3 `Cloak01..03`)
ship three UV sets; the current preview samples the base cloth on UV set 1.
The client composes the equipped cloak's material and passes; the current
Studio route uses a fixed `Cat_cloak.dds` preview and does not yet reproduce
the item texture, guild/decal pass or hood-state material. `gltf.py`
(`UV_SET_BY_SLOT`) applies the preview UV choice.

Improvement leads: (1) a pets/summons table for the creatures without a
monster row; (2) the player chain — the figure is only a base mesh, the
skin/armour is per-character (fig3 + `pskins`), needs a cleaner model;
(3) the fig3 wear pipeline.

## 4. KFA animation files (= NetImmerse NIFs, not EMotion FX)
- `anims/` = a large KFA corpus (historical inventory: 4060 files). The
  current table contains duplicate KFA basenames, so the old one-to-one
  “fully covered” claim is not a current invariant; resolve timing and state
  from the animation ID, not only the basename.
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
zlib stream 2 = directory of 0x11c-byte records; then zlib data streams.
Directory offsets are **base-relative** (base = end of the directory stream).
**Read and write are implemented** (`core/camelot/mpak.py`; write validated
by re-reading every retail entry, by the workspace reference reader, and
in-client — see below). Virtual URLs `archive://….mpk:file` in
`loading.dat`. Zone archives:
`zones/zoneNNN/datNNN.mpk, csvNNN.mpk, lodNNN.mpk, terNNN.mpk, texNNN.mpk`;
dungeons: `zones/Dnifs/*.npk`.

**Header (17 bytes after `MPAK`) — decoded on 4 729 corpus archives.**
Every carrier byte is position-keyed, `byte[i] = key(i) XOR data`:

| offset | field | encoding |
|---|---|---|
| 0 | version | `0x02` |
| 1..4 | **crc32 of the compressed directory stream** | u32 LE `^ 00 01 02 03` |
| 5..6 | **compressed directory length (s2)** | u16 LE `^ 0x0504` |
| 7..8 | constants | `0x06 0x07` |
| 9 | **compressed name length (s1)** | u8 `^ 0x08` |
| 10..12 | constants | `0x09 0x0a 0x0b` |
| 13 | entry count | u8 `^ 0x0C` |
| 14..16 | constants | `0x0d 0x0e 0x0f` |

An earlier reading recorded bytes 5..10 as "undecoded" and byte 17 as a
count function; both are now explained. The 16-bit directory carrier and
the 8-bit name carrier hold every retail archive (max measured: 21 551 and
49 bytes — larger streams would need a convention this corpus has no
sample of, and `build_header` refuses them rather than guessing).

**Directory record (0x11c bytes)** — an earlier reading stopped at the
0x110 offset; all seven u32 fields are:

| offset | field |
|---|---|
| 0x000..0x100 | entry name, NUL-terminated/padded |
| 0x100..0x104 | f0: undecoded build-tool artefact (carried across rewrites) |
| 0x104..0x108 | always zero in the corpus |
| 0x108..0x10C | uncompressed offset (cumulative over the archive) |
| 0x10C..0x110 | uncompressed size |
| 0x110..0x114 | compressed offset (base-relative) |
| 0x114..0x118 | compressed size |
| 0x118..0x11C | crc32 of the entry's compressed stream |

**The game reads the stream lengths from the header, not from the
streams.** Traced in the client (test A of `notes`): it
reads exactly the header-declared s1/s2 bytes and, when the directory
cannot be parsed from that read, retries the whole archive and exits
during the loading screen — with no message anywhere (`error.log` /
`warn.log` never mention archives, matching the "no corruption message"
observation below). Consequences:

- the carriers **must be recomputed on every rewrite**; preserving them
  produces an archive the client refuses *even at byte-identical content*;
- the client accepts both retail level-9 streams (`78 DA`) and
  deflate-default streams (`78 9C`) once the header matches.

Reference readers (mharj's dempak.py 2002, our `MpakArchive`) skip the
header past the magic, so round-trips alone never exercised the carriers —
which is exactly how a writer that preserved them passed every local test
while failing in-client.

**The client installer uses this writer** (`core/camelot/clientinstall.py`,
2026-10-08): a mod's payload is applied by rebuilding the archive from a
pristine copy with `write_mpak` — same header template, same carried
per-entry bytes — and the result is read back and compared before it
replaces the file in the client directory. A payload cannot *add* an entry
yet (replacement only): a record the corpus has no sample of would need the
0x100..0x104 artefact invented, which is why both `repack_mpak` and the
installer refuse an unknown name rather than guessing one.

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

Full client NIF corpus historical snapshot (5089 files, `--read-only`):
**5087 OK / 2 errors**. The two remaining errors are Gamebryo **10.1.0.0**
housing files failing inside **pyffi** (not the legacy reader):
`phousing/nifs/int_floor_table_Frontier_{Albion,Midgard}.NIF` —
`string too long` while reading a "BC…" string. *Open:* pyffi patch or a
10.1.0.0 fallback. Re-run and version the report against the pinned
`DAOC_CLIENT V2` profile.

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

1. Replacing a figure mesh: preserve the **Bip01 node tree and compatible
   names/bind data**, the attach-node names, the `Scene Root` root, and the
   scale/bind pose. The KFA channel space is the client's
   `figures/animnode.dat` name table, not simply the per-skeleton DFS order.
2. Making a fig3 piece: provide a skinned mesh whose `bonelist` names the
   target skeleton's bones plus a compliant `NiSkinData`.
3. A `.kfa` can be reused only when the target bone names, structure and bind
   remain compatible. Re-rigging requires rewriting or revalidating the
   animation; do not assume byte reuse is safe.
4. **The model table is a fixed array in the client binary** (measured
   2026-10-10): `game1127.dll` — the image the launcher loads, not the
   `game.dll` beside it (58% of the bytes differ) — keeps the `monsters.csv`
   rows in a static array (base `0xb77b98`, 464 bytes an entry, 2500 entries),
   and a row at or above 2500 makes the client stop parsing the whole table.
   The array can be **moved**: `camelot.clientpatch` appends a zero-filled PE
   section and repoints the two cap compares (`0x457d7e`, `0x43454a`) and the
   four array references (`0x421f7e`, `0x434557`, `0x457d94`, `0x473af4`),
   raising the ceiling to 32768. The recipe (offsets and bytes) travels in mod
   packs; no client byte is redistributed. Other tables have their own
   arrays and caps (`objects` caps at 5000, 196 bytes an entry).

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

## 11. Zones (the world)

Measured 2026-10-11 on the OpenDAoC 1.127 client. The reader is
`core/camelot/zones.py`; the probe and its numbers are in the private
workspace (`notes/`), and the design is `notes/phase6-world-editor.md`.

- `zones/zones.mpk` → **`zones.dat`**: the client's own world index, a text
  INI (BOM, CRLF) with **483 `[zoneNNN]` and 358 `[regionNNN]`** sections. A
  comment may be attached to a header (`[zone245];Hall of the Corrupt`), so a
  header ends at its first `]`. A zone carries `name`, `region`, `type`
  (0 normal, 1 city, 2 dungeon, 4 skycity), `enabled`, `entry_music`, and its
  world rectangle in 8192-unit cells: `region_offset_x/y`, `width`, `height`.
- **The world grid**: a zone spans `offset × 8192 … + size × 8192`, i.e.
  65536 units; zone-local coordinates are `0..65535`. The server builds the
  same rectangle from its DB (`OffX * 8192`, `WorldMgr.cs`), and
  `Zone.ZoneSkinID` is the id the client is sent.
- A zone's data lives in archives named after it: `zones/zoneNNN/datNNN.mpk`
  (+ `csvNNN`, `terNNN`, `lodNNN`, `texNNN`). Which exist depends on the
  type: an outdoor zone ships `terrain.pcx`, `offset.pcx`, `fixtures.csv`,
  `nifs.csv`, `bound.csv`, `sector.dat`, `zonejump.csv` (plus `densemap`,
  `grassmap`, `shademap`, `shadow` `.pcx`); a dungeon ships
  `dungeon.chunk/.place/.prop/.water` and `portals.csv`; a city `city.csv`
  and `adjacency.csv`. `dat` and `csv` carry identical bytes in the zones
  measured; which one the client reads first is unmeasured (W-1).
- **`terrain.pcx` / `offset.pcx`**: 8-bit, one-plane, RLE PCX, 256×256 for a
  65536-unit zone. The ground height is
  `terrain[y][x] × scalefactor + offset[y][x] × offsetfactor` — factors per
  zone in `sector.dat`'s `[terrain]` (8 and 32 in Camelot Hills, 8 and 48 in
  Vale of Mularn) — sampled **row-major, bilinear across each subtile's
  diagonal** (`xPercent + yPercent <= 1` picks the first triangle).
  Calibrated against the client's own `fixtures.csv`: exact (median error 0-1)
  on the zones whose rows are ground-placed.
- **`fixtures.csv`**: two header rows; `ID, NIF #, Textual Name, X, Y, Z, A,
  Scale, Collide, Radius, Animate, Ground, Flip, Cave, Unique Id` (newer
  files add 3D angle/axis columns). Coordinates are zone-local, `A` a 12-bit
  heading, `Scale` a percentage. **`Z = 0` with `Ground = 1` means "drop it on
  the terrain"** (Midgard's zones ship most rows that way); a non-zero `Z` may
  be authored on a structure — Camelot Hills' rows sit ~600-1000 above the
  terrain and only 2.5 % match the heightmap — so an editor should emit
  `z = 0` + `Ground = 1` rather than copy an authored value.
- **`nifs.csv`**: the zone's object catalog (`NIF, Textual Name, Filename,
  Only, Shadow, Color, Animate, Collide, Ground, MinAngle, MaxAngle,
  MinScale, MaxScale, Radius, Unique, Local, Terrain`); `fixtures.csv`'s
  `NIF #` indexes it.
- **`bound.csv`**: `id, count, x,y pairs…` — the walkable polygons.
- **`sector.dat`**: INI — `[sectorsize]` (8×8), `[fog]`, `[sky]`, `[terrain]`
  (factors, horizon, clouds), `[jumpdefs]`/`[jumpNN]` (named jump points),
  `[waterdefs]`/`[riverNN]` (water: `height`, `flow`, `texture`,
  `bankpoints`, `leftNN`/`rightNN` in **256-unit pixels** — `pixel × 256` is
  zone-local), `[holes]`, `[forest]`, `[mongens]`.
- **Object meshes**: `zones/Nifs/<name>.npk` is an **MPAK containing one
  NetImmerse 4.2.2.0 NIF** (the studio exports it to glTF today); `.nhd` is a
  companion (header `NHD\1` plus the `.nif` name) whose role — collision or
  ground — is unmeasured. `Dnifs/` holds the dungeon set, and a zone's own
  `Nifs/` overrides it.