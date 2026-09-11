import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { embedBuffers, extractMeshTextures, parseGlbContainer } from './load';

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
