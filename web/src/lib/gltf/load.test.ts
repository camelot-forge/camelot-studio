import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { embedBuffers } from './load';

// Corpus-style: reads the exported Boar artifacts if present (game-derived,
// never committed). Point CAMELOT_GLTF_DIR at the export directory, or it
// falls back to the exploratory workspace. Skips when the files are absent.
const DIR =
	process.env.CAMELOT_GLTF_DIR ??
	'/Users/you/Documents/Git/camelot-forge/workspace/work';
const present = existsSync(join(DIR, 'boar.gltf'));

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
});
