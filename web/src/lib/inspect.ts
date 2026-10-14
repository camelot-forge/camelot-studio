// What the inspector shows, as pure rules: a figure's joints, the search box's
// own matching, and how the mesh slots are named. Kept out of the cards so the
// behaviour can be tested without a canvas (the drafts' right dock).
import type * as THREE from 'three';
import type { LoadedTexture, SkinJson } from './gltf/load';
import { BONE_INDEX } from './skeleton';

export interface Joint {
	/** The canonical KFA channel index, when the name is one we know. */
	index: number | null;
	name: string;
}

const INDEX_BY_NAME: Record<string, number> = Object.fromEntries(
	Object.entries(BONE_INDEX).map(([i, name]) => [name, Number(i)])
);

/** The figure's own joints, in skin order, each name once: what the
 * `.skin.json` sidecar names, with its canonical channel where we know it. */
export function figureJoints(skin: SkinJson | null): Joint[] {
	const seen = new Set<string>();
	const out: Joint[] = [];
	for (const mesh of skin?.meshes ?? []) {
		for (const name of mesh.joints) {
			if (seen.has(name)) continue;
			seen.add(name);
			out.push({ index: INDEX_BY_NAME[name] ?? null, name });
		}
	}
	return out;
}

/** The canonical table itself (`figures/animnode.dat`'s verified half). */
export function canonicalJoints(): Joint[] {
	return Object.entries(BONE_INDEX).map(([i, name]) => ({ index: Number(i), name }));
}

/** The search box's rule: a name substring (case-insensitive), or the whole
 * canonical index — "13" is how an operator asks for channel 13. */
export function filterJoints(joints: Joint[], query: string): Joint[] {
	const q = query.trim().toLowerCase();
	if (!q) return joints;
	return joints.filter(
		(joint) => joint.name.toLowerCase().includes(q) || String(joint.index ?? '') === q
	);
}

/** Bones the glTF itself carries (no sidecar loaded): the scene is what is
 * rigged, whatever the NIF's own table says. */
export function sceneBoneCount(root: THREE.Object3D): number {
	let bones = 0;
	root.traverse((obj) => {
		if ((obj as THREE.Bone).isBone) bones++;
	});
	return bones;
}

/** The Model card's bone count: the sidecar's joints are the NIF's own rig
 * (the engine's answer), the exported scene is the fallback for a figure the
 * viewer was handed without one. */
export function rigBones(scene: THREE.Object3D, skin: SkinJson | null): number {
	const named = figureJoints(skin).length;
	return named > 0 ? named : sceneBoneCount(scene);
}

export interface SkinSlot {
	/** The exporter's mesh slot: "skin 0" is the first mesh that carries one. */
	label: string;
	name: string;
}

/** The sheet each mesh slot carries, in the exporter's own order. */
export function skinSlots(textures: LoadedTexture[]): SkinSlot[] {
	return textures.map((tex, i) => ({ label: `skin ${i}`, name: tex.name }));
}
