// The client's ground composition, as one generated material per patch.
//
// A patch is not drawn from its pre-baked `texXX-YY` sheet: the client stacks
// up to nine layers (`ter/textures.csv` — a shared `TerrainTex` sheet, a uv
// transform, a repeat count) and blends them with the masks
// `patchXXYY-K.dds`, whose R/G/B channels carry layers 3K/3K+1/3K+2's
// weights. The composition below is *measured* against the client's own bake
// (`texXX-YY.dds`, the client's composite of those layers): layer 0 first,
// then every later layer lerped over the accumulated colour in ascending
// order; a sheet repeats `tiles` times **across the zone** — the layer uv is
// `((patch + uv) / 8) * tiles`, so `tiles = 256` is a 256-unit tile (a 512²
// sheet at ~0.5 units per texel, the pebbles and grass blades the game
// shows) and the untiled `CamHills-Small` row (`tiles = 1`) is exactly one
// patch window of the zone-wide sheet. The other variants score worse
// against that bake: descending order (layers *under* the base) 1.4–2×,
// patch-local tiling (32-unit grass tiles — 8× too dense, and what the first
// cut drew) 1.15–1.4×, a v flip 1.3–1.8×.
//
// Two orientations meet here, neither a guess:
// - the masks and the baked sheet are "north up" images (row 0 = the zone's
//   lowest y). The viewer's patch uv is `v = 1` at the *north* edge and
//   three.js `flipY` is on, so `vGroundUv` samples them correctly;
// - the layer sheets are read with `flipY = false` and their own coordinates
//   `(u, 1 - v)` — the axis the Python model sampled them on.
//
// Everything that differs between two patches is a uniform, so a zone
// compiles a handful of programs (one per layer count) instead of sixty-four.

import * as THREE from 'three';
import type { WorldTextureLayer } from '$lib/api/client';

/** The zone's ground grid: 8×8 patches — the stride of the zone-wide sheets. */
export const PATCH_GRID = 8;
/** The corpus' deepest patch stack (measured: 9 layers, 3 masks). */
export const MAX_LAYERS = 9;
/** One mask file per three layers: layers 3K/3K+1/3K+2 in R/G/B. */
export const MAX_MASKS = 3;

/** One layer of a patch, resolved to what the shader needs. */
export interface GroundSlot {
	/** `zones/TerrainTex/<sheet>.dds`, the shared layer sheet. */
	sheet: string;
	/** The mask entry carrying this layer's weight (`patchXXYY-K.dds`). */
	mask: string;
	/** Mask sampler index: 0..2, one per `patchXXYY-K.dds`. */
	maskIndex: number;
	/** Weight channel in the mask: 0 = R (layer 3K), 1 = G, 2 = B. */
	channel: number;
	tiles: number;
	u: number;
	v: number;
	uScale: number;
	vScale: number;
	/** Radians, about the uv centre. */
	rotate: number;
}

/** The patch's blend slots, in file order — `null` when it has no layers.
 *
 * `visible = 0` rows are dropped, but each drawn layer keeps the mask channel
 * its file index gives it: the masks' weights belong to positions, not to the
 * rows that happen to be drawn (layer 0's channel is 255 everywhere in the
 * corpus, which is why it needs no weight at all — it is the base).
 *
 * `tileable = 0` needs no special case: every such row in the corpus carries
 * `tiles = 1`, i.e. one window of the zone-wide sheet, which is what the
 * shared formula draws anyway.
 */
export function groundSlots(layers: WorldTextureLayer[], masks: string[]): GroundSlot[] | null {
	const slots: GroundSlot[] = [];
	for (let index = 0; index < layers.length; index++) {
		const row = layers[index];
		if (!row.visible) continue;
		const maskIndex = Math.floor(index / 3);
		const mask = masks[maskIndex];
		if (!mask) return null; // a layer without its mask cannot be drawn
		slots.push({
			sheet: row.texture,
			mask,
			maskIndex,
			channel: index % 3,
			tiles: row.tiles,
			u: row.u,
			v: row.v,
			uScale: row.u_scale,
			vScale: row.v_scale,
			rotate: (row.rotate * Math.PI) / 180
		});
	}
	return slots.length ? slots : null;
}

interface SlotTextures {
	slot: GroundSlot;
	sheet: THREE.Texture;
	mask: THREE.Texture;
}

export interface GroundTextures {
	sheet(slot: GroundSlot): THREE.Texture | null;
	mask(name: string): THREE.Texture | null;
}

/** The GLSL a ground material injects: declarations plus the colour body.
 *
 * The declarations are constant (the samplers for the deepest stack the
 * corpus ships; the driver drops the unused ones); the body has one `mix`
 * per layer past the base, so the program depends on the layer count and on
 * which mask file each layer reads — a handful per zone, not one per patch.
 */
export function groundShader(
	layerCount: number,
	maskIndices: number[]
): { declarations: string; body: string } {
	const declarations = [
		...Array.from({ length: MAX_LAYERS }, (_v, i) => `uniform sampler2D uGroundSheet${i};`),
		...Array.from({ length: MAX_MASKS }, (_v, i) => `uniform sampler2D uGroundMask${i};`),
		...Array.from({ length: MAX_LAYERS }, (_v, i) => `uniform vec4 uGroundTransform${i};`),
		...Array.from({ length: MAX_LAYERS }, (_v, i) => `uniform vec3 uGroundShape${i};`),
		...Array.from({ length: MAX_LAYERS }, (_v, i) => `uniform float uGroundChannel${i};`),
		'uniform vec2 uGroundPatch;',
		'varying vec2 vGroundUv;',
		// The sheet axis: row 0 north (flipY is off for the layer sheets), and
		// the *zone's* uv — a layer repeats across the zone, not the patch.
		`vec2 groundSheetUv() { return (vec2(vGroundUv.x, 1.0 - vGroundUv.y) + uGroundPatch) / ${PATCH_GRID}.0; }`,
		// The layer sheet: ((patch + uv) * scale + translate) * tiles, turned
		// about the uv centre when the row carries an angle (none does in the
		// corpus). tiles = 1 is the untiled row: one window of the zone sheet.
		'vec2 groundLayerUv(vec4 transform, vec3 shape) {',
		'\tvec2 t = groundSheetUv() * transform.xy + transform.zw;',
		'\tif (shape.x != 0.0) {',
		'\t\tvec2 c = t - 0.5;',
		'\t\tfloat s = sin(shape.x);',
		'\t\tfloat o = cos(shape.x);',
		'\t\tt = vec2(c.x * o - c.y * s, c.x * s + c.y * o) + 0.5;',
		'\t}',
		'\treturn t * shape.y;',
		'}',
		// channel: 0 = R, 1 = G, 2 = B (the masks carry layers 3K/3K+1/3K+2).
		'float groundWeight(vec3 rgb, float channel) {',
		'\treturn channel < 0.5 ? rgb.r : (channel < 1.5 ? rgb.g : rgb.b);',
		'}'
	].join('\n');

	const lines: string[] = [
		'vec3 groundColor = texture2D(uGroundSheet0, groundLayerUv(uGroundTransform0, uGroundShape0)).rgb;'
	];
	for (let index = 1; index < layerCount; index++) {
		lines.push(
			`groundColor = mix(groundColor, ` +
				`texture2D(uGroundSheet${index}, groundLayerUv(uGroundTransform${index}, uGroundShape${index})).rgb, ` +
				`groundWeight(texture2D(uGroundMask${maskIndices[index]}, vGroundUv).rgb, ` +
				`uGroundChannel${index}));`
		);
	}
	lines.push('diffuseColor.rgb *= groundColor;');
	return { declarations, body: lines.join('\n') };
}

/** The patch's material: a Lambert whose diffuse comes from its layer stack.
 *
 * `null` when a sheet or mask the patch needs is missing — the caller then
 * keeps the client's own pre-baked sheet, which is the same composition at
 * lower resolution, rather than silently dropping a layer.
 */
export function groundMaterial(
	slots: GroundSlot[],
	textures: GroundTextures,
	patch: [number, number]
): THREE.MeshLambertMaterial | null {
	const used: SlotTextures[] = [];
	for (const slot of slots) {
		const sheet = textures.sheet(slot);
		const mask = textures.mask(slot.mask);
		if (!sheet || !mask) return null;
		used.push({ slot, sheet, mask });
	}
	if (!used.length) return null;

	const { declarations, body } = groundShader(
		used.length,
		used.map((entry) => entry.slot.maskIndex)
	);
	const material = new THREE.MeshLambertMaterial({ color: 0xffffff });
	const uniforms: Record<string, THREE.IUniform> = {
		uGroundPatch: { value: new THREE.Vector2(patch[0], patch[1]) }
	};
	used.forEach((entry, index) => {
		uniforms[`uGroundSheet${index}`] = { value: entry.sheet };
		uniforms[`uGroundTransform${index}`] = {
			value: new THREE.Vector4(entry.slot.uScale, entry.slot.vScale, entry.slot.u, entry.slot.v)
		};
		// shape = (angle, tiles, unused).
		uniforms[`uGroundShape${index}`] = {
			value: new THREE.Vector3(entry.slot.rotate, entry.slot.tiles, 0)
		};
		uniforms[`uGroundChannel${index}`] = { value: entry.slot.channel };
		// A layer reads its own mask file: index i -> mask floor(i / 3).
		uniforms[`uGroundMask${entry.slot.maskIndex}`] = { value: entry.mask };
	});
	material.onBeforeCompile = (shader) => {
		for (const [name, uniform] of Object.entries(uniforms)) {
			shader.uniforms[name] = uniform;
		}
		shader.fragmentShader = shader.fragmentShader
			.replace('#include <common>', `#include <common>\n${declarations}`)
			.replace('#include <map_fragment>', body);
		shader.vertexShader = shader.vertexShader
			.replace('#include <common>', '#include <common>\nvarying vec2 vGroundUv;')
			.replace('#include <begin_vertex>', '#include <begin_vertex>\nvGroundUv = uv;');
	};
	// Three caches programs per material type plus this key: patches whose
	// stacks agree on layer count and mask file share one program (every
	// differing value is a uniform).
	material.customProgramCacheKey = () =>
		`ground:${used.length}:${used.map((entry) => entry.slot.maskIndex).join('')}`;
	return material;
}
