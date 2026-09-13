import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { rebindSkinnedMeshes } from './load';

// Regression test for the r186 GLTFLoader identity-bind trap: skins bound
// with an identity matrix render parented meshes (Body offset + __axes__)
// in the wrong frame. Re-binding with the live bind world restores the
// standard identity (world = boneWorld * invBind at bind).
describe('rebindSkinnedMeshes', () => {
	function makeRig() {
		const scene = new THREE.Scene();
		const axes = new THREE.Group();
		axes.rotation.set(Math.PI / 4, 0, 0);
		scene.add(axes);
		const holder = new THREE.Group();
		holder.position.set(-0.0688, -39.5774, 24.8029);
		axes.add(holder);
		const bone = new THREE.Bone();
		bone.position.set(1, 2, 3);
		holder.add(bone);
		const geo = new THREE.BufferGeometry();
		geo.setAttribute('position', new THREE.Float32BufferAttribute([2.14, 25.73, -16.06], 3));
		geo.setAttribute('skinIndex', new THREE.Uint16BufferAttribute([0, 0, 0, 0], 4));
		geo.setAttribute('skinWeight', new THREE.Float32BufferAttribute([1, 0, 0, 0], 4));
		const mesh = new THREE.SkinnedMesh(geo, new THREE.MeshBasicMaterial()) as THREE.SkinnedMesh;
		holder.add(mesh);
		const skeleton = new THREE.Skeleton([bone]);
		// what GLTFLoader r186 does: identity bind matrix
		mesh.bind(skeleton, new THREE.Matrix4());
		scene.updateMatrixWorld(true);
		return { scene, mesh, bone };
	}

	it('binds with the live mesh world instead of identity', () => {
		const { scene, mesh } = makeRig();
		expect(mesh.bindMatrix.equals(new THREE.Matrix4())).toBe(true);
		const count = rebindSkinnedMeshes(scene);
		expect(count).toBe(1);
		const expected = mesh.matrixWorld.clone();
		expect(mesh.bindMatrix.equals(expected)).toBe(true);
		const inv = expected.clone().invert();
		expect(mesh.bindMatrixInverse.equals(inv)).toBe(true);
	});

	it('renders skinned bind vertices at the mesh world', () => {
		const { scene, mesh } = makeRig();
		rebindSkinnedMeshes(scene);
		scene.updateMatrixWorld(true);
		mesh.skeleton.update();
		// single full-weight bone at bind: skinned world must equal mesh world * v
		const v = new THREE.Vector3(2.14, 25.73, -16.06);
		const skinM = new THREE.Matrix4().fromArray(
			(mesh.skeleton.boneMatrices as unknown as Float32Array).slice(0, 16)
		);
		const out = v
			.clone()
			.applyMatrix4(mesh.bindMatrix)
			.applyMatrix4(skinM)
			.applyMatrix4(mesh.bindMatrixInverse)
			.applyMatrix4(mesh.matrixWorld);
		const want = new THREE.Vector3(2.14, 25.73, -16.06).applyMatrix4(mesh.matrixWorld);
		expect(out.distanceTo(want)).toBeLessThan(1e-4);
	});
});
