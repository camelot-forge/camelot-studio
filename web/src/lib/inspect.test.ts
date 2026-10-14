import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import type { LoadedTexture, SkinJson } from './gltf/load';
import {
	canonicalJoints,
	figureJoints,
	filterJoints,
	rigBones,
	sceneBoneCount,
	skinSlots
} from './inspect';

const skin = (meshes: Array<[string, string[]]>): SkinJson => ({
	meshes: meshes.map(([name, joints]) => ({ name, joints, vertices: [] }))
});

describe('figureJoints', () => {
	it('lists each joint once, in skin order', () => {
		const joints = figureJoints(
			skin([
				['Body', ['Bip01', 'Bip01 Pelvis', 'Bip01 Spine']],
				['Head', ['Bip01 Spine', 'Bip01 Head']]
			])
		);
		expect(joints.map((j) => j.name)).toEqual([
			'Bip01',
			'Bip01 Pelvis',
			'Bip01 Spine',
			'Bip01 Head'
		]);
	});

	it('carries the canonical channel when the name is one we know', () => {
		const byName = new Map(
			figureJoints(skin([['Body', ['Bip01', 'Weird Bone']]])).map((j) => [j.name, j.index])
		);
		expect(byName.get('Bip01')).toBe(0);
		expect(byName.get('Weird Bone')).toBeNull();
	});

	it('says nothing without a sidecar', () => {
		expect(figureJoints(null)).toEqual([]);
	});
});

describe('filterJoints', () => {
	const joints = figureJoints(
		skin([
			['Body', ['Bip01', 'Bip01 Spine1', 'Bip01 R Finger0']],
			['Head', ['Bip01 Head']]
		])
	);

	it('returns everything for an empty query', () => {
		expect(filterJoints(joints, '  ')).toHaveLength(4);
	});

	it('matches a name substring, case-insensitively', () => {
		expect(filterJoints(joints, 'finger').map((j) => j.name)).toEqual(['Bip01 R Finger0']);
	});

	it('matches a canonical index whole', () => {
		// 13 is `Bip01 R Finger0`: an index is asked for in full, never as a
		// digit substring (a "1" query would otherwise answer half the rig).
		expect(filterJoints(joints, '13')).toEqual([{ index: 13, name: 'Bip01 R Finger0' }]);
	});
});

describe('rigBones', () => {
	const empty = new THREE.Group();

	it('prefers the sidecar: it is the NIF’s own rig', () => {
		const scene = new THREE.Group();
		scene.add(new THREE.Bone(), new THREE.Bone(), new THREE.Bone());
		expect(sceneBoneCount(scene)).toBe(3);
		expect(rigBones(scene, skin([['Body', ['Bip01', 'Bip01 Pelvis']]]))).toBe(2);
	});

	it('falls back to the bones the glTF carries', () => {
		const scene = new THREE.Group();
		scene.add(new THREE.Bone(), new THREE.Bone());
		expect(rigBones(scene, null)).toBe(2);
	});

	it('is zero for a static figure', () => {
		expect(rigBones(empty, null)).toBe(0);
	});
});

describe('skinSlots', () => {
	it('names the slots the way the exporter wrote them', () => {
		const tex = (name: string) => ({ name }) as LoadedTexture;
		expect(skinSlots([tex('boar_body.dds'), tex('boar_head.dds')])).toEqual([
			{ label: 'skin 0', name: 'boar_body.dds' },
			{ label: 'skin 1', name: 'boar_head.dds' }
		]);
	});
});

describe('canonicalJoints', () => {
	it('walks the verified table in index order', () => {
		const joints = canonicalJoints();
		expect(joints[0]).toEqual({ index: 0, name: 'Bip01' });
		expect(joints.map((j) => j.index)).toEqual(joints.map((_j, i) => i));
	});
});
