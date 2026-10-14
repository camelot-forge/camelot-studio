// Exporting the figure on screen as a file. The viewer and the builder both
// offer it, so the two rules it has to respect live here, once.
import type { Mesh, MeshStandardMaterial, Object3D } from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';

/**
 * Write `scene` as a `.glb` download.
 *
 * The exported node tree is a *clean* one: a clone (skeletons included, via
 * `SkeletonUtils`) with the viewer's own normalisation — the scale and offset
 * it grounds every figure with — taken back off, so the file carries the
 * figure, not our camera's arithmetic.
 *
 * The client's sheets are BC-compressed DDS, which a glTF writer cannot
 * re-encode: the clone goes out without them (the material keeps its own
 * colour), never with a half-written texture.
 */
export async function exportFigureGlb(scene: Object3D, name: string): Promise<void> {
	const copy = cloneSkinned(scene);
	copy.position.set(0, 0, 0);
	copy.rotation.set(0, 0, 0);
	copy.scale.setScalar(1);
	copy.traverse((obj) => {
		const mesh = obj as Mesh;
		if (!mesh.isMesh) return;
		const own = (mesh.material as MeshStandardMaterial).clone();
		own.map = null;
		mesh.material = own;
	});
	const out = (await new GLTFExporter().parseAsync(copy, {
		binary: true,
		onlyVisible: false
	})) as ArrayBuffer;
	const url = URL.createObjectURL(new Blob([out], { type: 'model/gltf-binary' }));
	const link = document.createElement('a');
	link.href = url;
	link.download = `${name.replace(/\.\w+$/, '')}.glb`;
	link.click();
	URL.revokeObjectURL(url);
}
