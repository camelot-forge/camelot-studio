import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { embedBuffers, extractMeshTextures } from './load';

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
