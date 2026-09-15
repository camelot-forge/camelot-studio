import * as THREE from 'three';
import { DDSLoader } from 'three/addons/loaders/DDSLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { parseCharacter } from '$lib/character';

// Sidecar emitted by `camelot gltf` (per-mesh bone names + per-vertex weights).
export interface SkinMesh {
	name: string;
	joints: string[];
	vertices: Array<Array<[number, number]>>;
}
export interface SkinJson {
	meshes: SkinMesh[];
}

export interface LoadedTexture {
	name: string;
	bytes: ArrayBuffer;
	// Alpha-cut sheets (Catacombs hair): the viewer alpha-tests them.
	alpha?: boolean;
	// Fig3 hair meshes are authored double-sided (fig3parts "Double Sided").
	doubleSided?: boolean;
}

// Player base figures stack every equipment variant as separate meshes; the
// rig resolves the mesh-name classification to scene objects and the viewer
// applies a default selection (one variant per slot). A variant can cover
// several meshes (a head pairs its face and hair sheets). Variants with no
// object (hidden import) are kept with an empty list so indices stay stable.
export interface CharacterVariant {
	label: string;
	meshes: THREE.Object3D[];
}
export interface CharacterRigSlot {
	id: string;
	label: string;
	variants: CharacterVariant[];
	defaultIndex: number; // -1 = hidden by default (LOD)
}
export interface CharacterRig {
	slots: CharacterRigSlot[];
}

export interface LoadedModel {
	scene: THREE.Group;
	name: string;
	meshCount: number;
	triangleCount: number;
	skinJson: SkinJson | null;
	meshTextures: Map<THREE.Mesh, LoadedTexture>;
	character: CharacterRig | null;
	swapSlotTexture?: (slot: string, sourceUrl: string) => Promise<void>;
}

// Decode DDS bytes into the viewer's texture (DDSLoader TexData ->
// CompressedTexture, sRGB): the single decode path shared by the GLB
// pipeline (Viewer.svelte) and the per-slot texture swap below. Sheets
// arrive either as raw DXT or as the server's uncompressed A8R8G8B8 DDS
// when they carry alpha (DDSLoader maps DXT1 to an opaque RGB format).
export function textureFromDds(bytes: ArrayBuffer): THREE.Texture {
	const data = new DDSLoader().parse(bytes, true);
	const tex = new THREE.CompressedTexture(
		data.mipmaps,
		data.width,
		data.height,
		data.format as THREE.CompressedPixelFormat,
		THREE.UnsignedByteType
	);
	tex.colorSpace = THREE.SRGBColorSpace;
	// DAoC/Gamebryo wraps texture coordinates; some fig3 hair and beard
	// vertices carry UVs outside [0,1] (e.g. Highlander Male's beard at
	// v=-0.8). The three.js clamp default would smear the edge texel there.
	tex.wrapS = THREE.RepeatWrapping;
	tex.wrapT = THREE.RepeatWrapping;
	tex.anisotropy = 4;
	tex.needsUpdate = true;
	return tex;
}

// Re-apply a single slot's texture from a URL: the builder's fine-grained
// update path (DAoC Tools swaps the *texture* too when only the hair colour
// changes — no full model reload). Throws on HTTP failure so the caller can
// fall back to a full rebuild.
async function loadDdsIntoMesh(mesh: THREE.Mesh, url: string): Promise<void> {
	const res = await fetch(url);
	if (!res.ok) throw new Error(`texture swap failed (${res.status})`);
	const tex = textureFromDds(await res.arrayBuffer());
	const mat = mesh.material as THREE.MeshStandardMaterial;
	mat.map?.dispose();
	mat.map = tex;
	mat.needsUpdate = true;
}

// Apply one variant of a slot: every other variant is hidden. -1 hides the
// whole slot (naked, or LOD levels off).
export function setCharacterVariant(
	rig: CharacterRig,
	slotIndex: number,
	variantIndex: number
): void {
	const slot = rig.slots[slotIndex];
	if (!slot) return;
	for (let vi = 0; vi < slot.variants.length; vi++) {
		for (const mesh of slot.variants[vi].meshes) mesh.visible = vi === variantIndex;
	}
}

// Classify the scene's meshes and enforce the default selection (P0: a player
// figure must show one variant per slot, not all 49 stacked). Returns null for
// figures that are not player bases (monsters keep every mesh visible).
//
// Names come from the glTF document, not the THREE objects: GLTFLoader
// sanitizes node names and strips the NIF shape suffix separator
// ("Body1:0" -> "Body10"), which would make the digits ambiguous.
function buildCharacter(
	scene: THREE.Object3D,
	parser: GltfResult['parser'],
	gltfMeshNames: Array<string | undefined> | undefined
): CharacterRig | null {
	const byIndex = new Map<number, THREE.Object3D[]>();
	scene.traverse((obj) => {
		if (!(obj as THREE.Mesh).isMesh) return;
		const mi = parser.associations.get(obj)?.meshes;
		if (mi === undefined) return;
		const list = byIndex.get(mi) ?? [];
		list.push(obj);
		byIndex.set(mi, list);
	});
	const names = (gltfMeshNames ?? []).map((name) => name ?? '');
	const parsed = parseCharacter(names.filter(Boolean));
	if (!parsed) return null;
	const meshIndex = new Map(names.map((name, i) => [name, i]));
	const rig: CharacterRig = {
		slots: parsed.map((slot) => ({
			id: slot.id,
			label: slot.label,
			defaultIndex: slot.defaultIndex,
			variants: slot.variants.map((variant) => ({
				label: variant.label,
				meshes: variant.names.flatMap((name) => byIndex.get(meshIndex.get(name) ?? -1) ?? [])
			}))
		}))
	};
	for (let si = 0; si < rig.slots.length; si++)
		setCharacterVariant(rig, si, rig.slots[si].defaultIndex);
	return rig;
}

function ext(name: string): string {
	const i = name.lastIndexOf('.');
	return i >= 0 ? name.slice(i + 1).toLowerCase() : '';
}

function triangleCount(root: THREE.Object3D): number {
	let n = 0;
	root.traverse((obj) => {
		const geo = (obj as THREE.Mesh).geometry;
		if (geo) {
			const idx = geo.index;
			n += idx ? idx.count / 3 : geo.attributes.position.count / 3;
		}
	});
	return Math.round(n);
}

function meshCount(root: THREE.Object3D): number {
	let n = 0;
	root.traverse((obj) => {
		if ((obj as THREE.Mesh).isMesh) n++;
	});
	return n;
}

// Embed a buffer as a base64 data URI. Deterministic, works in-browser and in
// node, and independent of blob-URL/fetch support.
export function asDataUri(buf: ArrayBuffer): string {
	const bytes = new Uint8Array(buf);
	let bin = '';
	for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
	return `data:application/octet-stream;base64,${btoa(bin)}`;
}

// The core exporter links the .bin by name (no `uri` on the buffer): rewrite
// each non-inline buffer's `uri` to an embedded data URI from the provided
// .bin blobs (matched by uri basename, or the .gltf basename + ".bin").
export function embedBuffers(
	json: { buffers?: Array<{ uri?: string }> },
	name: string,
	bins: Map<string, ArrayBuffer>
): void {
	const base = name.slice(0, name.lastIndexOf('.')).toLowerCase();
	for (const buf of json.buffers ?? []) {
		if (buf.uri?.startsWith('data:')) continue;
		const rel = (buf.uri?.split('/').pop() ?? `${base}.bin`).toLowerCase();
		const bin = bins.get(rel);
		if (bin) buf.uri = asDataUri(bin);
	}
}

// Parse a .gltf JSON + its .bin blobs with three.js.
interface GltfResult {
	scene: THREE.Group;
	parser: { associations: Map<object, { meshes?: number }> };
}

async function loadGltfPair(
	json: Record<string, unknown>,
	name: string,
	bins: Map<string, ArrayBuffer>
): Promise<GltfResult> {
	embedBuffers(json as { buffers?: Array<{ uri?: string }> }, name, bins);
	return await new Promise<GltfResult>((resolve, reject) => {
		new GLTFLoader().parse(
			JSON.stringify(json),
			'',
			(gltf) => resolve({ scene: gltf.scene, parser: gltf.parser }),
			reject
		);
	});
}

// Extract the CS_camelot_texture DDS blobs per glTF mesh. Each is a bufferView
// into the .bin; GLTFLoader ignores those orphan bufferViews. The mesh index
// lets the viewer map each THREE.Mesh to its own skin-slot texture.
export function extractMeshTextures(
	json: {
		meshes?: Array<Record<string, unknown>>;
		bufferViews?: Array<{ byteOffset?: number; byteLength: number }>;
	},
	binBytes: ArrayBuffer
): Map<number, LoadedTexture> {
	const out = new Map<number, LoadedTexture>();
	json.meshes?.forEach((mesh, mi) => {
		const ext = (
			mesh.extensions as Record<
				string,
				{ bufferView: number; name?: string; alpha?: boolean; doubleSided?: boolean }
			>
		)?.CS_camelot_texture;
		if (!ext) return;
		const bv = json.bufferViews?.[ext.bufferView];
		if (!bv) return;
		const off = bv.byteOffset ?? 0;
		out.set(mi, {
			name: ext.name ?? 'texture',
			bytes: binBytes.slice(off, off + bv.byteLength),
			alpha: ext.alpha,
			doubleSided: ext.doubleSided
		});
	});
	return out;
}

// Map each THREE.Mesh in the scene to its glTF mesh index via the parser.
function bindMeshTextures(
	scene: THREE.Object3D,
	parser: GltfResult['parser'],
	meshTextures: Map<number, LoadedTexture>
): Map<THREE.Mesh, LoadedTexture> {
	const out = new Map<THREE.Mesh, LoadedTexture>();
	scene.traverse((obj) => {
		const mesh = obj as THREE.Mesh;
		if (!mesh.isMesh) return;
		const mi = parser.associations.get(mesh)?.meshes;
		const tex = mi !== undefined ? meshTextures.get(mi) : undefined;
		if (tex) out.set(mesh, tex);
	});
	return out;
}

// Re-bind every SkinnedMesh with its live bind world.
//
// three.js r186 GLTFLoader binds skins with an identity bind matrix, while
// SkinnedMesh.updateMatrixWorld keeps refreshing bindMatrixInverse from the
// live world (AttachedBindMode). For meshes parented under transformed nodes
// (our Body offset + __axes__ conversion) that pairing drops the mesh world
// transform and the model renders in the wrong frame. Re-binding with the
// real bind world restores the standard identity
// (world = boneWorld * invBind at bind) — must run after the scene is
// assembled and positioned, before the first render.
export function rebindSkinnedMeshes(scene: THREE.Object3D): number {
	let count = 0;
	scene.traverse((obj) => {
		const skinned = obj as THREE.SkinnedMesh;
		if (!skinned.isSkinnedMesh) return;
		// Explicit bindMatrix: `bind(skeleton)` without it also calls
		// skeleton.calculateInverses(), overwriting the GLB's
		// inverseBindMatrices with inverses of the *current* pose (bind at
		// load, but a sampled/posed skeleton later — the engine-exact
		// stored-bind rigs would silently lose their fix).
		skinned.bind(skinned.skeleton, skinned.matrixWorld);
		count++;
	});
	return count;
}

// Bind-pose world bounds of a loaded model.
//
// Unlike THREE.Box3.setFromObject (whose SkinnedMesh path folds the live
// skeleton into the measurement), this uses raw bind-geometry positions
// transformed by mesh world matrices only — exactly what static grounding
// and camera framing must be computed from.
export function bindBoxOf(root: THREE.Object3D): THREE.Box3 {
	root.updateMatrixWorld(true);
	const box = new THREE.Box3();
	const v = new THREE.Vector3();
	root.traverse((obj) => {
		const mesh = obj as THREE.Mesh;
		const pos = mesh.geometry?.attributes.position as THREE.BufferAttribute | undefined;
		// Hidden meshes must not drive grounding/framing: on player figures
		// only the selected equipment variant is visible (the rest would
		// still inflate the box, e.g. the four whole-body LOD levels).
		if (!mesh.isMesh || !pos || !mesh.visible) return;
		const skinned = obj as THREE.SkinnedMesh;
		if (skinned.isSkinnedMesh && skinned.skeleton) {
			// Measure the *engine bind*: the exact deformation the GPU will
			// apply at rest. Some rigs store NiSkinData binds that disagree
			// with their node tree (AfancB: a 302-unit shift), so raw
			// geometry world bounds are not where the model renders.
			// Rebinding first makes bindMatrix the live world, which makes
			// applyBoneTransform + matrixWorld equal
			// boneWorld * inverseBind * meshWorld * v (explicit bindMatrix:
			// never let three.js recompute the GLB's inverse bind matrices).
			skinned.bind(skinned.skeleton, skinned.matrixWorld);
			for (let i = 0; i < pos.count; i++) {
				v.fromBufferAttribute(pos, i);
				skinned.applyBoneTransform(i, v);
				v.applyMatrix4(skinned.matrixWorld);
				box.expandByPoint(v);
			}
		} else {
			for (let i = 0; i < pos.count; i++) {
				v.fromBufferAttribute(pos, i).applyMatrix4(obj.matrixWorld);
				box.expandByPoint(v);
			}
		}
	});
	return box;
}
// Split a GLB container into its JSON document and binary chunk. The serve
// daemon returns GLB (single response) but the CS_camelot_texture extension
// lives in orphan bufferViews inside the BIN chunk, so we need both parts.
export function parseGlbContainer(buf: ArrayBuffer): {
	json: Record<string, unknown>;
	bin: ArrayBuffer | null;
} {
	const dv = new DataView(buf);
	if (dv.getUint32(0, true) !== 0x46546c67) throw new Error('Not a GLB file');
	let off = 12;
	let json: Record<string, unknown> | null = null;
	let bin: ArrayBuffer | null = null;
	const decoder = new TextDecoder();
	while (off + 8 <= buf.byteLength) {
		const len = dv.getUint32(off, true);
		const type = dv.getUint32(off + 4, true);
		off += 8;
		const slice = buf.slice(off, off + len);
		if (type === 0x4e4f534a) json = JSON.parse(decoder.decode(slice));
		else if (type === 0x004e4942) bin = slice;
		off += len;
	}
	if (!json) throw new Error('GLB has no JSON chunk');
	return { json, bin };
}

function binNameFor(name: string): string {
	const i = name.lastIndexOf('.');
	return `${(i >= 0 ? name.slice(0, i) : name).toLowerCase()}.bin`;
}

// Load a GLB served by `camelot serve` (or dropped): same pipeline as a
// .gltf+.bin pair, reading the buffer from the container's BIN chunk.
export async function loadFromGlb(buf: ArrayBuffer, name: string): Promise<LoadedModel> {
	const { json, bin } = parseGlbContainer(buf);
	const bins = new Map<string, ArrayBuffer>();
	if (bin) bins.set(binNameFor(name), bin);
	const { scene, parser } = await loadGltfPair(json, name, bins);
	const raw = bin ? extractMeshTextures(json, bin) : new Map<number, LoadedTexture>();
	const meshTextures = bindMeshTextures(scene, parser, raw);
	const meshes = (json.meshes ?? []) as Array<{ name?: string }>;
	const model: LoadedModel = {
		scene,
		name,
		meshCount: meshCount(scene),
		triangleCount: triangleCount(scene),
		skinJson: null,
		meshTextures,
		character: buildCharacter(
			scene,
			parser,
			meshes.map((m) => m.name)
		),
		swapSlotTexture: () => Promise.reject(new Error('pending'))
	};
	// slot naming: the export maps glossy into a CS_camelot_texture ext per
	// slot; the swap must hit the mesh whose name matches the slot exactly
	model.swapSlotTexture = async (slot, url) => {
		const mesh = scene.getObjectByName(slot) as THREE.Mesh | null;
		if (!mesh) throw new Error(`slot mesh ${slot} not found`);
		await loadDdsIntoMesh(mesh, url);
	};
	return model;
}

export async function loadFromFiles(files: File[]): Promise<LoadedModel> {
	const glb = files.find((f) => ext(f.name) === 'glb');
	if (glb) {
		return await loadFromGlb(await glb.arrayBuffer(), glb.name);
	}

	const gltf = files.find((f) => ext(f.name) === 'gltf');
	if (!gltf) throw new Error('No .glb or .gltf file provided');
	const json = JSON.parse(await gltf.text()) as {
		nodes?: Array<Record<string, unknown>>;
		meshes?: Array<{ name?: string }>;
		bufferViews?: Array<{ byteOffset?: number; byteLength: number }>;
		buffers?: Array<{ uri?: string }>;
	};
	const bins = new Map<string, ArrayBuffer>();
	for (const f of files) {
		if (ext(f.name) === 'bin') bins.set(f.name.toLowerCase(), await f.arrayBuffer());
	}
	const { scene, parser } = await loadGltfPair(json, gltf.name, bins);
	const binBytes = bins.get(`${gltf.name.slice(0, gltf.name.lastIndexOf('.')).toLowerCase()}.bin`);
	const meshTexturesRaw = binBytes ? extractMeshTextures(json, binBytes) : new Map();
	const meshTextures = bindMeshTextures(scene, parser, meshTexturesRaw);

	let skinJson: SkinJson | null = null;
	const skin = files.find((f) => ext(f.name) === 'json');
	if (skin) {
		try {
			skinJson = JSON.parse(await skin.text()) as SkinJson;
		} catch {
			skinJson = null;
		}
	}
	return {
		scene,
		name: gltf.name,
		meshCount: meshCount(scene),
		triangleCount: triangleCount(scene),
		skinJson,
		meshTextures,
		character: buildCharacter(
			scene,
			parser,
			(json.meshes ?? []).map((m) => m.name)
		)
	};
}
