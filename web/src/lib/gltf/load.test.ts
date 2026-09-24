import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
	embedBuffers,
	extractMeshTextures,
	parseGlbContainer,
	setCharacterVariant,
	textureFromDds,
	textureSide,
	type CharacterRig
} from './load';

function makeGlb(json: object, bin?: Uint8Array): ArrayBuffer {
	const enc = new TextEncoder();
	let jsonBytes = enc.encode(JSON.stringify(json));
	while (jsonBytes.length % 4) jsonBytes = new Uint8Array([...jsonBytes, 0x20]);
	const chunks: Array<{ type: number; data: Uint8Array }> = [{ type: 0x4e4f534a, data: jsonBytes }];
	if (bin) {
		let b = bin;
		while (b.length % 4) b = new Uint8Array([...b, 0]);
		chunks.push({ type: 0x004e4942, data: b });
	}
	const total = 12 + chunks.reduce((n, c) => n + 8 + c.data.length, 0);
	const out = new Uint8Array(total);
	const dv = new DataView(out.buffer);
	dv.setUint32(0, 0x46546c67, true);
	dv.setUint32(4, 2, true);
	dv.setUint32(8, total, true);
	let off = 12;
	for (const c of chunks) {
		dv.setUint32(off, c.data.length, true);
		dv.setUint32(off + 4, c.type, true);
		off += 8;
		out.set(c.data, off);
		off += c.data.length;
	}
	return out.buffer;
}

describe('parseGlbContainer', () => {
	it('splits the JSON and BIN chunks', () => {
		const { json, bin } = parseGlbContainer(makeGlb({ a: 1 }, new Uint8Array([1, 2, 3])));
		expect(json).toEqual({ a: 1 });
		expect(new Uint8Array(bin!).slice(0, 3)).toEqual(new Uint8Array([1, 2, 3]));
	});

	it('rejects a non-GLB buffer', () => {
		expect(() => parseGlbContainer(new Uint8Array([0, 0, 0, 0]).buffer)).toThrow();
	});
});

// Corpus-style: reads the exported Boar artifacts if present (game-derived,
// never committed). Point CAMELOT_GLTF_DIR at the export directory, or it
// falls back to the exploratory workspace. Skips when the files are absent.
const DIR =
	process.env.CAMELOT_GLTF_DIR ??
	'/Users/you/Documents/Git/camelot-forge/workspace/work';
const present = existsSync(join(DIR, 'boar.gltf'));
const textured = existsSync(join(DIR, 'model_Boar_tex.gltf'));

describe.skipIf(!present)('glTF loading', () => {
	it('links a node to the mesh (core exports a renderable mesh)', () => {
		const gltf = JSON.parse(readFileSync(join(DIR, 'boar.gltf'), 'utf8'));
		const nodeHasMesh = gltf.nodes.some((n: Record<string, unknown>) => 'mesh' in n);
		expect(nodeHasMesh).toBe(true);
	});

	it('does not emit null attribute accessors', () => {
		const gltf = JSON.parse(readFileSync(join(DIR, 'boar.gltf'), 'utf8'));
		for (const mesh of gltf.meshes) {
			for (const prim of mesh.primitives) {
				for (const value of Object.values(prim.attributes)) {
					expect(value).not.toBeNull();
				}
			}
		}
	});

	it('embeds the .bin as a data URI when no uri is present', () => {
		const gltf = JSON.parse(readFileSync(join(DIR, 'boar.gltf'), 'utf8'));
		expect(gltf.buffers[0].uri).toBeTruthy();
		const bin = readFileSync(join(DIR, 'boar.bin'));
		embedBuffers(gltf, 'boar.gltf', new Map([['boar.bin', bin.buffer]]));
		expect(gltf.buffers[0].uri.startsWith('data:application/octet-stream;base64,')).toBe(true);
	});

	describe.skipIf(!textured)('textured export', () => {
		it('embeds the DDS via the CS_camelot_texture extension and extracts it', () => {
			const gltf = JSON.parse(readFileSync(join(DIR, 'model_Boar_tex.gltf'), 'utf8'));
			expect(gltf.extensionsUsed).toContain('CS_camelot_texture');
			const binBytes = readFileSync(join(DIR, 'model_Boar_tex.bin'));
			const texs = extractMeshTextures(gltf, binBytes.buffer);
			expect(texs.size).toBeGreaterThan(0);
			const first = [...texs.values()][0];
			expect(first.name).toMatch(/\.(dds|tga)$/);
			// starts with the DDS magic
			expect(new Uint8Array(first.bytes).slice(0, 4)).toEqual(
				new Uint8Array([0x44, 0x44, 0x53, 0x20])
			);
		});
	});
});

// Minimal uncompressed A8R8G8B8 DDS, byte-for-byte the layout the server's
// `dxt.encode_a8r8g8b8` emits (masks DDSLoader reads back as RGBAFormat).
function makeA8r8g8b8Dds(width: number, height: number): ArrayBuffer {
	const header = new ArrayBuffer(128);
	const dv = new DataView(header);
	const u32 = (off: number, value: number) => dv.setUint32(off, value, true);
	dv.setUint8(0, 0x44);
	dv.setUint8(1, 0x44);
	dv.setUint8(2, 0x53);
	dv.setUint8(3, 0x20);
	u32(4, 124);
	u32(8, 0x1 | 0x2 | 0x4 | 0x1000);
	u32(12, height);
	u32(16, width);
	u32(20, width * 4);
	u32(76, 32);
	u32(80, 0x1 | 0x40);
	u32(88, 32);
	u32(92, 0x00ff0000);
	u32(96, 0x0000ff00);
	u32(100, 0x000000ff);
	u32(104, 0xff000000);
	u32(108, 0x1000);
	const pixels = new Uint8Array(width * height * 4);
	const out = new Uint8Array(header.byteLength + pixels.length);
	out.set(new Uint8Array(header), 0);
	out.set(pixels, 128);
	return out.buffer;
}

describe('extractMeshTextures', () => {
	it('reads the engine tint out of CS_camelot_texture', () => {
		const bin = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
		const gltf = {
			meshes: [
				{
					extensions: {
						CS_camelot_texture: {
							bufferView: 0,
							name: 'face.dds',
							doubleSided: false,
							tint: [223, 207, 166]
						}
					}
				}
			],
			bufferViews: [{ byteOffset: 2, byteLength: 4 }]
		};
		const out = extractMeshTextures(gltf, bin.buffer);
		expect(out.get(0)?.tint).toEqual([223, 207, 166]);
		expect(out.get(0)?.doubleSided).toBe(false);
		expect([...new Uint8Array(out.get(0)!.bytes)]).toEqual([3, 4, 5, 6]);
	});
});

describe('textureSide', () => {
	it('honors an explicit single-sided flag and preserves the default', () => {
		expect(textureSide(false)).toBe(THREE.FrontSide);
		expect(textureSide(true)).toBe(THREE.DoubleSide);
		expect(textureSide()).toBe(THREE.DoubleSide);
	});
});

describe('textureFromDds', () => {
	it('decodes the server A8R8G8B8 DDS into an uncompressed RGBA texture', () => {
		const tex = textureFromDds(makeA8r8g8b8Dds(4, 4));
		const mip = tex.mipmaps[0] as unknown as {
			width: number;
			height: number;
			data: Uint8Array;
		};
		expect(mip.width).toBe(4);
		expect(mip.height).toBe(4);
		expect(mip.data.length).toBe(64);
		expect(tex.format).toBe(THREE.RGBAFormat);
		expect(tex.colorSpace).toBe(THREE.SRGBColorSpace);
		// fig3 hair/beard UVs can leave [0,1] (Highlander Male: v=-0.9):
		// clamping would smear the edge texel over the beard.
		expect(tex.wrapS).toBe(THREE.RepeatWrapping);
		expect(tex.wrapT).toBe(THREE.RepeatWrapping);
	});
});

function rigOf(variantCounts: number[]): CharacterRig {
	return {
		slots: variantCounts.map((count, si) => ({
			id: `s${si}`,
			label: `S${si}`,
			defaultIndex: 0,
			variants: Array.from({ length: count }, (_, vi) => ({
				label: `S${si}V${vi}`,
				meshes: [{ visible: true } as unknown as THREE.Object3D]
			}))
		}))
	};
}

describe('setCharacterVariant', () => {
	it('shows exactly one variant per slot', () => {
		const rig = rigOf([3, 2]);
		setCharacterVariant(rig, 0, 2);
		setCharacterVariant(rig, 1, 0);
		expect(rig.slots[0].variants.map((v) => v.meshes[0].visible)).toEqual([false, false, true]);
		expect(rig.slots[1].variants.map((v) => v.meshes[0].visible)).toEqual([true, false]);
	});

	it('hides the whole slot with -1 (LOD default)', () => {
		const rig = rigOf([2]);
		setCharacterVariant(rig, 0, -1);
		expect(rig.slots[0].variants.map((v) => v.meshes[0].visible)).toEqual([false, false]);
	});

	it('leaves other slots untouched', () => {
		const rig = rigOf([2, 2]);
		setCharacterVariant(rig, 1, -1);
		expect(rig.slots[0].variants.map((v) => v.meshes[0].visible)).toEqual([true, true]);
	});
});
