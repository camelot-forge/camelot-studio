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

export interface LoadedModel {
	scene: THREE.Group;
	name: string;
	meshCount: number;
	triangleCount: number;
	skinJson: SkinJson | null;
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

// Parse a .gltf JSON + its .bin blobs with three.js, returning the scene.
async function loadGltfPair(
	name: string,
	gltfJson: string,
	bins: Map<string, ArrayBuffer>
): Promise<THREE.Group> {
	const json = JSON.parse(gltfJson) as { buffers?: Array<{ uri?: string }> };
	embedBuffers(json, name, bins);
	return await new Promise<THREE.Group>((resolve, reject) => {
		new GLTFLoader().parse(JSON.stringify(json), '', (gltf) => resolve(gltf.scene), reject);
	});
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
			skinJson: null
		};
	}

	const gltf = files.find((f) => ext(f.name) === 'gltf');
	if (!gltf) throw new Error('No .glb or .gltf file provided');
	const json = await gltf.text();
	const bins = new Map<string, ArrayBuffer>();
	for (const f of files) {
		if (ext(f.name) === 'bin') bins.set(f.name.toLowerCase(), await f.arrayBuffer());
	}
	const scene = await loadGltfPair(gltf.name, json, bins);

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
		skinJson
	};
}
