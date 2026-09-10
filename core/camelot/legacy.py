"""Camelot Studio — NetImmerse 4.x (legacy) reader.

pyffi 2.2.3 cannot parse the *meshed* NetImmerse 4.x files used by DAoC
(Ettin, fox_pet, old items, ...) while it parses 4.x KFA files fine. This
module is an independent reader for the 4.x subset DAoC uses, written from
format facts cross-checked against pyffi's nif.xml and DAOC_Navigator's
NIFLib (behavioral reference only; no code copied — this file is MIT).

Legacy layout: blocks as [u32 type_len][type_name][block data], references
by block index (u32, 0xFFFFFFFF = null), header = version string + u32
version + u32 num_blocks.

Version notes (verified against the DAoC client corpus, versions 4.2.1.0
and 4.2.2.0):

- ``NiKeyframeData`` stores a ``rotation_type`` u32 whenever
  ``num_rotation_keys > 0``. Key types 1/2/3 (linear/quadratic/TBC) all use
  the same 20-byte ``QuatKey`` (time + wxyz) before 10.1.0.0; type 4 (XYZ)
  stores an unknown float followed by three per-axis ``KeyGroup<float>``.
- ``KeyGroup`` (mesh files, 4.2.2.0) has no interpolation field while
  animation files and some 4.2.1.0 meshes do: the reader peeks the count
  and treats the next u32 as interpolation when it is a plausible
  ``KeyType`` (1..7) and the count is non-zero.
- ``NiTexturingProperty`` has an apply mode, a texture count and seven
  texture slots (base, dark, detail, gloss, glow, bump, decal0). The
  ``TexDesc`` of each slot carries an extra trailing u16 in 4.1.0.12
  (22-byte descs; 4.2.x descs are 20 bytes) — verified by brute-force
  layout fitting over every 4.x texturing property in the client.
- Property/controller booleans are single bytes from 4.1.0.1 on.

`LegacyData` exposes `version`, `blocks`, `roots`; blocks satisfy the
attribute expectations of `nifutil`/`gltf` (`.name`, `.children`,
`.translation/.rotation/.scale`, `.data.vertices/...`) so downstream
code paths work unchanged.
"""
from __future__ import annotations

import os
import struct

__all__ = ["LegacyData", "load_legacy", "is_legacy", "LegacyFormatError"]

NULL_REF = 0xFFFFFFFF

# Types carrying a NiAVObject body (objectnet + transform).
_NODE_TYPES = ("NiNode", "NiBillboardNode", "NiLODNode", "NiSwitchNode")
_GEOM_TYPES = ("NiTriShape", "NiTriStrips",
               "NiAutoNormalParticles", "NiRotatingParticles",
               "NiParticleMeshes")
_EFFECT_TYPES = ("NiAmbientLight", "NiDirectionalLight", "NiPointLight",
                 "NiTextureEffect", "NiCamera")

_GEOM_SHAPE = ("NiTriShapeData",)
_GEOM_STRIPS = ("NiTriStripsData",)
_PARTICLES_DATA = ("NiAutoNormalParticlesData", "NiRotatingParticlesData")

# interpolation values accepted by the KeyGroup heuristic
_VALID_KEY_TYPES = frozenset(range(1, 8))


class LegacyFormatError(ValueError):
    """Raised when a legacy 4.x file cannot be parsed."""


def is_legacy(data) -> bool:
    """True if `data` came from the legacy reader (not pyffi)."""
    return hasattr(data, "legacy") and data.legacy


class LegacyBlock:
    """Attribute-bag block. The class name carries the NIF block type name,
    so `type(block).__name__` matches downstream expectations."""

    __slots__ = ("attrs",)

    def __init__(self, **kw) -> None:
        self.attrs = kw

    def __getattr__(self, key):
        try:
            return self.attrs[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def __setattr__(self, key, value) -> None:
        if key == "attrs":
            object.__setattr__(self, key, value)
        else:
            self.attrs[key] = value

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} {getattr(self, 'name', b'')!r}>"


_BLOCK_CLASSES: dict[str, type] = {}


def _block_class(name: str) -> type:
    cls = _BLOCK_CLASSES.get(name)
    if cls is None:
        cls = type(name, (LegacyBlock,), {"__repr__": LegacyBlock.__repr__})
        _BLOCK_CLASSES[name] = cls
    return cls


def _strip_triangles(points) -> list[tuple[int, int, int]]:
    """Decode triangle strips into a triangle list (standard NetImmerse rule)."""
    out = []
    for strip in points:
        n = len(strip)
        for i in range(n - 2):
            a, b, c = strip[i], strip[i + 1], strip[i + 2]
            out.append((a, b, c) if i % 2 == 0 else (c, b, a))
    return out


class _Reader:
    """Bounds-checked little-endian reader."""

    def __init__(self, buf: bytes, version: int, path: str) -> None:
        self.buf = buf
        self.pos = 0
        self.version = version
        self.path = path
        self.blocks: list = []

    def _need(self, n: int) -> None:
        if self.pos + n > len(self.buf):
            raise LegacyFormatError(
                f"{os.path.basename(self.path)}: read past end of file "
                f"(offset 0x{self.pos:x} + {n})")

    def u8(self) -> int:
        self._need(1)
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        self._need(2)
        v = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        self._need(4)
        v = struct.unpack_from("<f", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def bool_(self) -> bool:
        # NetImmerse stored booleans as u32 before 4.1.0.1, and some
        # DAoC exporters kept writing 0xFFFFFFFF/new 0xFF for "false" even
        # after the switch to single bytes: treat 0x00 and 0xFF as false.
        if self.version < 0x04010001:
            v = self.u32()
            return v not in (0x00, 0xFFFFFFFF)
        v = self.u8()
        return v not in (0x00, 0xFF)

    def bool_u32(self) -> bool:
        """u32 boolean with the same 0xFF/0xFFFFFFFF false convention."""
        return self.u32() not in (0x00, 0xFFFFFFFF)

    def bool_u8(self) -> bool:
        v = self.u8()
        return v not in (0x00, 0xFF)

    def vec3(self) -> tuple[float, float, float]:
        return (self.f32(), self.f32(), self.f32())

    def vec4(self) -> tuple[float, float, float, float]:
        return (self.f32(), self.f32(), self.f32(), self.f32())

    def mat33(self) -> list[list[float]]:
        # stored column-major; exposed row-major m[row][col]
        vals = [self.f32() for _ in range(9)]
        return [[vals[c * 3 + r] for c in range(3)] for r in range(3)]

    def string(self) -> bytes:
        n = self.u32()
        if n > 4 * 1024 * 1024:  # generous: DAoC optimizer strings are large
            raise LegacyFormatError(
                f"{os.path.basename(self.path)}: string too long ({n}) at 0x{self.pos:x}")
        self._need(n)
        s = self.buf[self.pos:self.pos + n]
        self.pos += n
        return s

    def ref(self) -> int:
        return self.u32()

    # -- base structures ---------------------------------------------------
    def objectnet(self, b: LegacyBlock) -> None:
        b.name = self.string()
        b.extra_data = self.ref()      # 3.0..4.2.2.0: exactly one ref
        b.controller = self.ref()

    def transform(self, b: LegacyBlock) -> None:
        b.flags = self.u16()
        b.translation = self.vec3()
        b.rotation = self.mat33()
        b.scale = self.f32()
        b.velocity = self.vec3()       # <= 4.2.2.0
        nprops = self.u32()
        b.properties = [self.ref() for _ in range(nprops)]
        if self.bool_():               # has bounding box
            b.bounding_box = (self.vec3(), self.mat33())

    # -- key groups --------------------------------------------------------
    def _keygroup(self, payload_floats: int) -> list:
        """KeyGroup: u32 count + [u32 interpolation if count and the next
        u32 is a plausible KeyType] + count keys.

        Key size depends on interpolation: linear adds nothing, quadratic
        adds forward/backward tangents (2 x payload), TBC adds three floats.
        """
        n = self.u32()
        interp = 0
        if n > 0 and self._valid_key_type():
            interp = self.u32()
        if interp == 2:
            key_floats = 1 + payload_floats * 3   # time + value + fwd + back
        elif interp == 3:
            key_floats = 4 + payload_floats       # time + value + TBC floats
        else:
            key_floats = 1 + payload_floats
        keys = []
        for _ in range(n):
            t = self.f32()
            payload = tuple(self.f32() for _ in range(key_floats - 1))
            keys.append((t, payload))
        return keys

    def _valid_key_type(self) -> bool:
        if self.pos + 4 > len(self.buf):
            return False
        return struct.unpack_from("<I", self.buf, self.pos)[0] in _VALID_KEY_TYPES

    # -- block bodies ------------------------------------------------------
    def block(self, tname: str) -> LegacyBlock:
        b = _block_class(tname)()
        if tname in _NODE_TYPES or tname in _GEOM_TYPES or tname in _EFFECT_TYPES:
            self.objectnet(b)
            self.transform(b)
            if tname in _NODE_TYPES:
                if tname == "NiSwitchNode":
                    b.switch_index = self.u32()
                nkids = self.u32()
                b.children = [self.ref() for _ in range(nkids)]
                neff = self.u32()
                b.effects = [self.ref() for _ in range(neff)]
                if tname == "NiLODNode":
                    # NiLODNode derives from NiSwitchNode in 4.x: its fields
                    # come AFTER the NiNode children/effects lists, starting
                    # with the switch index (verified on
                    # items/hceltmerwepn.nif: index=0, center=(0,0,0), 2 LOD
                    # ranges with ascending extents).
                    b.switch_index = self.u32()
                    b.lod_center = self.vec3()
                    n = self.u32()
                    b.lod_levels = [(self.f32(), self.f32()) for _ in range(n)]
            elif tname in _GEOM_TYPES:
                b.data_ref = self.ref()      # NiGeometry
                b.skin_instance = self.ref()  # skin ref (may be null)
            else:  # effects (NiDynamicEffect subclasses, NiCamera)
                if tname == "NiCamera":
                    for _ in range(6):
                        self.f32()            # frustum
                    for _ in range(4):
                        self.f32()            # viewport
                    self.f32()                # LOD adjust
                    self.ref()                # unknown link
                    self.u32()                # unknown int 1
                    self.u32()                # unknown int 2 (>= 4.2.1.0)
                elif tname == "NiTextureEffect":
                    self.mat33()
                    self.vec3()
                    self.u32()                # texture filtering
                    self.u32()                # texture clamping
                    self.u32()                # texture type
                    self.u32()                # coord gen type
                    self.ref()                # source texture
                    self.u8()                 # clipping plane
                    self.vec3()
                    self.f32()
                    self.u16()                # PS2 L
                    self.u16()                # PS2 K
                    if self.version <= 0x0401000C:
                        self.u16()            # extra u16 (like 4.1.0.12 TexDesc)
                else:  # lights
                    self.f32()                # dimmer
                    for _ in range(3):
                        self.vec3()           # ambient, diffuse, specular
                    if tname == "NiPointLight":
                        self.f32()            # constant attenuation
                        self.f32()            # linear attenuation
                        self.f32()            # quadratic attenuation
        elif tname in _GEOM_SHAPE:
            self._geometry_common(b)
            b.num_triangles = self.u16()
            self.u32()                    # num triangle points
            b.triangles = [(self.u16(), self.u16(), self.u16())
                           for _ in range(b.num_triangles)]
            ngroups = self.u16()          # match groups (>= 3.1)
            b.match_groups = [[self.u16() for _ in range(self.u16())]
                              for _ in range(ngroups)]
            b.get_triangles = lambda: list(b.triangles or [])
        elif tname in _GEOM_STRIPS:
            self._geometry_common(b)
            b.num_triangles = self.u16()  # NiTriBasedGeomData, as in NiTriShapeData
            nstrips = self.u16()
            lengths = [self.u16() for _ in range(nstrips)]
            b.points = [[self.u16() for _ in range(lengths[i])]
                        for i in range(nstrips)]
            b.get_triangles = lambda: _strip_triangles(b.points or [])
        elif tname in _PARTICLES_DATA:
            self._geometry_common(b)
            b.particle_radius = self.f32()
            b.num_active = self.u16()
            if self.bool_():              # has sizes
                b.sizes = [self.f32() for _ in range(b.num_vertices)]
            else:
                b.sizes = None
            if tname == "NiRotatingParticlesData":
                if self.bool_():          # has rotations 2 (<= 4.2.2.0)
                    b.rotations = [self.vec4() for _ in range(b.num_vertices)]
                else:
                    b.rotations = None
        elif tname == "NiSkinInstance":
            b.skin_data = self.ref()      # no partition field (< 10.2)
            b.skeleton_root = self.ref()
            n = self.u32()
            b.bones = [self.ref() for _ in range(n)]
        elif tname == "NiSkinData":
            b.skin_transform = (self.mat33(), self.vec3(), self.f32())
            nbones = self.u32()
            b.skin_partition = self.ref()  # 4.0.0.2..10.1.0.0
            if self.version >= 0x04020100:
                has_weights = self.bool_()  # field added in 4.2.1.0
            else:
                has_weights = True          # <= 4.1.0.12: weights always stored
            entries = []
            for _ in range(nbones):
                entry = {
                    "transform": (self.mat33(), self.vec3(), self.f32()),
                    "bsphere": (self.vec3(), self.f32()),
                    "num_vertices": self.u16(),
                    "weights": None,
                }
                if has_weights:
                    entry["weights"] = [
                        (self.u16(), self.f32())
                        for _ in range(entry["num_vertices"])
                    ]
                entries.append(entry)
            b.bone_list = entries
        elif tname == "NiSkinPartition":
            nparts = self.u32()
            parts = []
            for _ in range(nparts):
                p = {
                    "num_vertices": self.u16(),
                    "num_triangles": self.u16(),
                    "num_bones": self.u16(),
                    "num_strips": self.u16(),
                    "num_weights_per_vertex": self.u16(),
                }
                p["bones"] = [self.u16() for _ in range(p["num_bones"])]
                p["vertex_map"] = [self.u16() for _ in range(p["num_vertices"])]
                p["vertex_weights"] = [
                    [self.f32() for _ in range(p["num_weights_per_vertex"])]
                    for _ in range(p["num_vertices"])
                ]
                p["strip_lengths"] = [self.u16() for _ in range(p["num_strips"])]
                if p["num_strips"]:
                    p["strips"] = [
                        [self.u16() for _ in range(p["strip_lengths"][i])]
                        for i in range(p["num_strips"])
                    ]
                else:
                    p["triangles"] = [
                        (self.u16(), self.u16(), self.u16())
                        for _ in range(p["num_triangles"])
                    ]
                if self.bool_():          # has bone indices
                    p["bone_indices"] = [
                        [self.u8() for _ in range(p["num_weights_per_vertex"])]
                        for _ in range(p["num_vertices"])
                    ]
                parts.append(p)
            b.partitions = parts
        elif tname == "NiMaterialProperty":
            self.objectnet(b)
            b.flags = self.u16()          # <= 10.0.1.2
            b.ambient_color = self.vec3()
            b.diffuse_color = self.vec3()
            b.specular_color = self.vec3()
            b.emissive_color = self.vec3()
            b.glossiness = self.f32()
            b.alpha = self.f32()
        elif tname == "NiAlphaProperty":
            self.objectnet(b)
            b.flags = self.u16()
            b.threshold = self.u8()
        elif tname == "NiStencilProperty":
            # DAoC stencils carry the younger 31-byte layout for this version:
            # flags u16 + enabled u8 + 7 u32s (function, ref, mask, actions).
            self.objectnet(b)
            b.flags = self.u16()
            self.u8()                     # stencil enabled
            b.stencil_function = self.u32()
            b.stencil_ref = self.u32()
            b.stencil_mask = self.u32()
            b.fail_action = self.u32()
            b.zfail_action = self.u32()
            b.pass_action = self.u32()
            b.draw_mode = self.u32()
        elif tname in ("NiDitherProperty", "NiWireframeProperty",
                       "NiSpecularProperty", "NiShadeProperty"):
            self.objectnet(b)
            b.flags = self.u16()
        elif tname == "NiZBufferProperty":
            self.objectnet(b)
            b.flags = self.u16()          # <= 10.0.1.2
            b.function = self.u32()       # 4.1.0.12..20.0.0.5
        elif tname == "NiVertexColorProperty":
            self.objectnet(b)
            b.flags = self.u16()
            b.vertex_mode = self.u32()    # <= 20.0.0.5
            b.lighting_mode = self.u32()
        elif tname == "NiTexturingProperty":
            self.objectnet(b)
            b.flags = self.u16()          # <= 10.0.1.2
            b.apply_mode = self.u32()     # <= 20.0.0.5
            b.texture_count = self.u32()  # Always 7 before 20.0.0.4
            b.textures = {}
            for slot in ("base", "dark", "detail", "gloss", "glow"):
                if self.bool_():
                    b.textures[slot] = self._tex_desc()
                else:
                    b.textures[slot] = None
            if self.bool_():              # bump map: desc + extras
                d = self._tex_desc()
                d["luma_scale"] = self.f32()
                d["luma_offset"] = self.f32()
                d["matrix22"] = [[self.f32() for _ in range(2)] for _ in range(2)]
                b.textures["bump"] = d
            else:
                b.textures["bump"] = None
            if self.bool_():
                b.textures["decal0"] = self._tex_desc()
            else:
                b.textures["decal0"] = None
            b.has_base_texture = b.textures["base"] is not None
            if b.textures["base"] is not None:
                b.base_texture = b.textures["base"]
        elif tname == "NiSourceTexture":
            self.objectnet(b)
            if self.bool_():              # use external
                b.file_name = self.string()
            else:
                # Embedded pixel data: no file name before 10.1.0.0.
                self.u8()                 # unknown byte (<= 10.0.1.0)
                b.internal_texture = self.ref()
            b.pixel_layout = self.u32()
            b.use_mipmaps = self.u32()
            b.alpha_format = self.u32()
            b.is_static = self.u8()
        elif tname == "NiPixelData":
            # ATextureRenderData (<= 10.2.0.0) + Num Pixels + raw blob.
            b.pixel_format = self.u32()
            b.red_mask = self.u32()
            b.green_mask = self.u32()
            b.blue_mask = self.u32()
            b.alpha_mask = self.u32()
            self.u8()                     # bits per pixel
            for _ in range(3):
                self.u8()                 # unknown 3 bytes
            for _ in range(8):
                self.u8()                 # unknown 8 bytes
            self.u32()                    # unknown int (seen in DAoC 4.2.x)
            b.num_mipmaps = self.u32()
            b.bytes_per_pixel = self.u32()
            b.mipmaps = [(self.u32(), self.u32(), self.u32())
                         for _ in range(b.num_mipmaps)]
            b.num_pixels = self.u32()
            self._need(b.num_pixels)
            b.pixels = self.buf[self.pos:self.pos + b.num_pixels]
            self.pos += b.num_pixels
        elif tname == "NiParticleMeshesData":
            # Empirical (absent from pyffi): geometry-like header, then
            # particle extras (radius, active count, sizes, rotations).
            self._geometry_common(b)
            b.particle_radius = self.f32()
            b.num_active = self.u16()
            if self.bool_():              # has sizes
                b.sizes = [self.f32() for _ in range(b.num_vertices)]
            else:
                b.sizes = None
            if self.bool_():              # has rotations
                b.rotations = [self.vec4() for _ in range(b.num_vertices)]
            else:
                b.rotations = None
            b.tail = self.u32()           # trailing value/ref (unverified)
        elif tname == "NiStringExtraData":
            # 4.2.x NiExtraData: no name field -- a next-extra-data ref and a
            # "bytes remaining" u32 precede the payload string (ver2=4.2.2.0).
            b.next_extra_data = self.ref()
            b.bytes_remaining = self.u32()
            b.string_data = self.string()
        elif tname in ("NiKeyframeController", "NiMaterialColorController",
                       "NiAlphaController", "NiVisController",
                       "NiLightColorController"):
            self._time_controller(b)
            b.data = self.ref()
        elif tname == "NiUVController":
            self._time_controller(b)
            self.u16()                    # uv group (always 0?)
            b.data = self.ref()
        elif tname == "NiGeomMorpherController":
            self._time_controller(b)
            b.data = self.ref()           # NiMorphData
            b.always_update = self.u8()
        elif tname == "NiPathController":
            self._time_controller(b)
            b.unknown_int = self.u32()
            self.f32()
            self.f32()
            self.u16()
            b.pos_data = self.ref()
            b.float_data = self.ref()
        elif tname == "NiLookAtController":
            self._time_controller(b)
            b.look_at_node = self.ref()
        elif tname == "NiKeyframeData":
            self._keyframe_data(b)
        elif tname == "NiColorData":
            b.data = self._keygroup(4)    # KeyGroup<Color4>
        elif tname == "NiFloatData":
            b.data = self._keygroup(1)    # KeyGroup<float>
        elif tname == "NiPosData":
            b.data = self._keygroup(3)    # KeyGroup<Vector3>
        elif tname == "NiUVData":
            # Four per-axis groups (U trans, V trans, U scale, V scale),
            # each a KeyGroup of single floats.
            b.uv_groups = [self._keygroup(1) for _ in range(4)]
        elif tname == "NiVisData":
            n = self.u32()
            b.keys = [(self.f32(), self.u8()) for _ in range(n)]
        elif tname == "NiMorphData":
            nmorphs = self.u32()
            nverts = self.u32()
            self.u8()                     # relative targets (often 1)
            morphs = []
            for _ in range(nmorphs):
                nkeys = self.u32()
                interp = self.u32()       # always present (<= 10.1.0.0)
                if interp == 2:
                    key_floats = 4        # time + value + fwd + back
                elif interp == 3:
                    key_floats = 5        # time + value + TBC floats
                else:
                    key_floats = 2
                keys = [(self.f32(), tuple(self.f32() for _ in range(key_floats - 1)))
                        for _ in range(nkeys)]
                morphs.append({
                    "num_keys": nkeys,
                    "interpolation": interp,
                    "keys": keys,
                    "vectors": [self.vec3() for _ in range(nverts)],
                })
            b.num_morphs = nmorphs
            b.num_vertices = nverts
            b.morphs = morphs
        elif tname == "NiParticleSystemController":
            self._time_controller(b)
            self.f32()                    # speed
            self.f32()                    # speed random
            self.f32()                    # vertical direction
            self.f32()                    # vertical angle
            self.f32()                    # horizontal direction
            self.f32()                    # horizontal angle
            self.vec3()                   # unknown normal
            self.vec4()                   # unknown color
            self.f32()                    # size
            self.f32()                    # emit start time
            self.f32()                    # emit stop time
            self.u8()                     # unknown byte (=0)
            self.f32()                    # emit rate
            self.f32()                    # lifetime
            self.f32()                    # lifetime random
            self.u16()                    # emit flags
            self.vec3()                   # start random
            self.ref()                    # emitter
            self.u16()                    # unknown short (=0)
            self.f32()                    # unknown float (=1.0)
            self.u32()                    # unknown int 1 (=1)
            self.u32()                    # unknown int 2 (=0)
            self.u16()                    # unknown short 3 (=0)
            nparticles = self.u16()
            nvalid = self.u16()
            particles = []
            for _ in range(nparticles):
                vel = self.vec3()
                unk = self.vec3()
                life = self.f32()
                lifespan = self.f32()
                ts = self.f32()
                ushort = self.u16()
                vid = self.u16()
                particles.append((vel, unk, life, lifespan, ts, ushort, vid))
            b.num_particles = nparticles
            b.num_valid = nvalid
            b.particles = particles
            b.unknown_link = self.ref()
            b.particle_extra = self.ref()
            b.unknown_link_2 = self.ref()
            self.u8()                     # trailer byte
        elif tname in ("NiGravity", "NiParticleBomb", "NiParticleColorModifier",
                       "NiParticleGrowFade", "NiParticleRotation",
                       "NiParticleMeshModifier", "NiPlanarCollider",
                       "NiSphericalCollider"):
            self._particle_modifier(b, tname)
        elif tname == "NiTextureTransformController":
            self._time_controller(b)
            self.u8()                     # unknown
            self.u32()                    # texture slot
            self.u32()                    # operation
            b.data = self.ref()
        else:
            raise LegacyFormatError(
                f"{os.path.basename(self.path)}: unsupported legacy block type "
                f"'{tname}' (offset 0x{self.pos:x})")
        return b

    def _tex_desc(self) -> dict:
        """TexDesc: source ref + clamp + filter + uv set + ps2_l + ps2_k.

        4.1.0.12 appends an extra u16 (observed 0x0100) after ps2_k;
        4.2.x descs end at ps2_k. (ps2_k itself looks like uninitialized
        exporter memory in DAoC files: 0xff8d, 0xff7e, ...)
        """
        d = {
            "source": self.ref(),
            "clamp": self.u32(),
            "filter": self.u32(),
            "uv_set": self.u32(),
            "ps2_l": self.u16(),
            "ps2_k": self.u16(),
        }
        if self.version <= 0x0401000C:
            d["unknown1"] = self.u16()
        return d

    def _time_controller(self, b: LegacyBlock) -> None:
        """NiTimeController base (4.2.x)."""
        b.next_controller = self.ref()
        b.flags = self.u16()
        self.f32()                        # frequency
        self.f32()                        # phase
        self.f32()                        # start time
        self.f32()                        # stop time
        b.target = self.ref()

    def _particle_modifier(self, b: LegacyBlock, tname: str) -> None:
        b.next_modifier = self.ref()
        b.controller = self.ref()
        if tname == "NiGravity":
            self.f32()                    # unknown float 1 (>= 4.0.0.2)
            b.force = self.f32()
            b.field_type = self.u32()
            b.position = self.vec3()
            b.direction = self.vec3()
        elif tname == "NiParticleBomb":
            self.f32()                    # decay
            self.f32()                    # duration
            self.f32()                    # delta v
            self.f32()                    # start
            b.decay_type = self.u32()
            b.symmetry_type = self.u32()  # >= 4.1.0.12
            b.position = self.vec3()
            b.direction = self.vec3()
        elif tname == "NiParticleColorModifier":
            b.color_data = self.ref()
        elif tname == "NiParticleGrowFade":
            b.grow = self.f32()
            b.fade = self.f32()
        elif tname == "NiParticleRotation":
            self.u8()                     # random initial axis
            b.initial_axis = self.vec3()
            b.rotation_speed = self.f32()
        elif tname == "NiParticleMeshModifier":
            n = self.u32()
            b.particle_meshes = [self.ref() for _ in range(n)]
        elif tname == "NiPlanarCollider":
            self.f32()                    # unknown float 1
            self.f32()                    # unknown float 2
            if self.version == 0x04020200:
                self.u16()                # unknown short (4.2.2.0 only)
            for _ in range(14):
                self.f32()
        elif tname == "NiSphericalCollider":
            self.f32()                    # unknown float 1
            self.u16()                    # unknown short 1
            self.f32()                    # unknown float 2
            if self.version >= 0x04020100:
                self.f32()                # unknown float 3 (>= 4.2.1.0)
            self.f32()                    # unknown float 4
            self.f32()                    # unknown float 5

    def _keyframe_data(self, b: LegacyBlock) -> None:
        nrot = self.u32()
        b.num_rotation_keys = nrot
        if nrot > 0:
            rtype = self.u32()
            b.rotation_type = rtype
            if rtype == 4:                # XYZ rotations
                self.f32()                # unknown float (<= 10.1.0.0)
                b.xyz_rotations = [self._keygroup(1) for _ in range(3)]
            else:
                # QuatKey before 10.1.0.0: time + wxyz (20 B); TBC keys
                # (type 3) additionally carry three TBC floats (32 B).
                # Quadratic keys never have tangents here.
                tbc = rtype == 3
                b.rotation_keys = [
                    (self.f32(), self.vec4())
                    if not tbc else
                    (self.f32(), self.vec4(), (self.f32(), self.f32(), self.f32()))
                    for _ in range(nrot)
                ]
        b.translations = self._keygroup(3)
        b.scales = self._keygroup(1)

    def _geometry_common(self, b: LegacyBlock) -> None:
        b.num_vertices = self.u16()
        if self.bool_():
            b.vertices = [self.vec3() for _ in range(b.num_vertices)]
        else:
            b.vertices = None
        if self.bool_():
            b.normals = [self.vec3() for _ in range(b.num_vertices)]
        else:
            b.normals = None
        b.center = self.vec3()
        b.radius = self.f32()
        if self.bool_():
            b.vertex_colors = [self.vec4() for _ in range(b.num_vertices)]
        else:
            b.vertex_colors = None
        # <= 4.2.2.0: UV count (u16, low 6 bits) read AFTER vertex colors;
        # the t-space flag byte is skipped.
        num_uv_sets = self.u8() & 63
        self.u8()  # t-space flag
        if num_uv_sets:
            b.uv_sets = [
                [(self.f32(), self.f32()) for _ in range(b.num_vertices)]
                for _ in range(num_uv_sets)
            ]
        else:
            b.uv_sets = []

    def read_blocks(self, num_blocks: int) -> None:
        for _ in range(num_blocks):
            typelen = self.u32()
            if not (4 <= typelen <= 60):
                raise LegacyFormatError(
                    f"{os.path.basename(self.path)}: invalid block type length "
                    f"({typelen}) at 0x{self.pos - 4:x}")
            self._need(typelen)
            tname = self.buf[self.pos:self.pos + typelen].decode("latin1")
            self.pos += typelen
            self.blocks.append(self.block(tname))

    def read_footer(self) -> list[int]:
        nroots = self.u32()
        return [self.ref() for _ in range(nroots)]


class LegacyData:
    """Result of a legacy load: version / version_string / blocks / roots."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.legacy = True
        self.writable = False
        with open(path, "rb") as fh:
            buf = fh.read()
        version_string, version, num_blocks, data_pos = _read_header(buf, path)
        self.version_string = version_string
        self.version = version
        self.num_blocks = num_blocks
        r = _Reader(buf[data_pos:], version, path)
        r.read_blocks(num_blocks)
        self.blocks = r.blocks
        self.roots = self._resolve(r.read_footer())

    def _resolve(self, root_refs: list[int]) -> list:
        def obj(ref: int):
            return self.blocks[ref] if ref != NULL_REF and ref < len(self.blocks) else None

        for b in self.blocks:
            t = type(b).__name__
            if t in _NODE_TYPES:
                if t == "NiLODNode":
                    pass  # children already raw refs -- resolve below
                kids = []
                for r in b.children:
                    c = obj(r)
                    kids.append(c)
                    if c is not None:
                        c.parent = b
                b.children = kids
                b.effects = [obj(r) for r in b.effects]
            elif t in _GEOM_TYPES:
                b.data = obj(b.data_ref)
                b.skin_instance = obj(b.skin_instance)
            elif t == "NiSkinInstance":
                b.skin_data = obj(b.skin_data)
                b.skeleton_root = obj(b.skeleton_root)
                b.bones = [obj(r) for r in b.bones]
            elif t == "NiSkinData":
                b.skin_partition = obj(b.skin_partition)
            if hasattr(b, "properties"):
                b.properties = [obj(r) for r in b.properties]
        return [obj(ref) for ref in root_refs]


def _read_header(buf: bytes, path: str):
    nl = buf.find(b"\n")
    if nl < 0:
        raise LegacyFormatError(f"{os.path.basename(path)}: no header newline")
    version_string = buf[:nl].decode("latin1", "replace")
    if "File Format" not in version_string:
        raise LegacyFormatError(f"{os.path.basename(path)}: not a NetImmerse file")
    p = nl + 1
    if p + 8 > len(buf):
        raise LegacyFormatError("truncated header")
    version = struct.unpack_from("<I", buf, p)[0]
    num_blocks = struct.unpack_from("<I", buf, p + 4)[0]
    return version_string, version, num_blocks, p + 8


def load_legacy(path: str) -> LegacyData:
    return LegacyData(path)