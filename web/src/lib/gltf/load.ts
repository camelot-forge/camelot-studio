import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

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
}

export interface LoadedModel {
	scene: THREE.Group;
	name: string;
	meshCount: number;
	triangleCount: number;
	skinJson: SkinJson | null;
	meshTextures: Map<THREE.Mesh, LoadedTexture>;
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
		const ext = (mesh.extensions as Record<string, { bufferView: number; name?: string }>)
			?.CS_camelot_texture;
		if (!ext) return;
		const bv = json.bufferViews?.[ext.bufferView];
		if (!bv) return;
		const off = bv.byteOffset ?? 0;
		out.set(mi, { name: ext.name ?? 'texture', bytes: binBytes.slice(off, off + bv.byteLength) });
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

export async function loadFromFiles(files: File[]): Promise<LoadedModel> {
	const glb = files.find((f) => ext(f.name) === 'glb');
	if (glb) {
		const buf = await glb.arrayBuffer();
		const scene = await new Promise<THREE.Group>((resolve, reject) => {
			new GLTFLoader().parse(buf, '', (gltf) => resolve(gltf.scene), reject);
		});
		return {
			scene,
			name: glb.name,
			meshCount: meshCount(scene),
			triangleCount: triangleCount(scene),
			skinJson: null,
			meshTextures: new Map()
		};
	}

	const gltf = files.find((f) => ext(f.name) === 'gltf');
	if (!gltf) throw new Error('No .glb or .gltf file provided');
	const json = JSON.parse(await gltf.text()) as {
		nodes?: Array<Record<string, unknown>>;
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
		meshTextures
	};
}
