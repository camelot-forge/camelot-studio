import { describe, expect, it } from 'vitest';
import type { WorldCatalogEntry } from '$lib/api/client';
import { filterCatalog } from './catalog';

const entry = (
	id: number,
	name: string,
	filename: string,
	ground: boolean,
	mesh: boolean
): WorldCatalogEntry => ({
	id,
	name,
	filename,
	ground,
	radius: 0,
	min_scale: 0,
	max_scale: 0,
	min_angle: 0,
	max_angle: 0,
	mesh
});

const entries = [
	entry(1, 'Pine', 'bpinea.nif', true, true),
	entry(2, 'Farm House', 'bfarmhouse.nif', true, true),
	entry(3, 'Dungeon Door', 'bdoor.nif', false, true),
	entry(4, 'Old Stump', 'bstump.nif', true, false)
];

describe('the catalog filter', () => {
	it('searches name, file and id', () => {
		expect(filterCatalog(entries, 'pine').map((row) => row.id)).toEqual([1]);
		expect(filterCatalog(entries, 'bfarmhouse').map((row) => row.id)).toEqual([2]);
		expect(filterCatalog(entries, '3').map((row) => row.id)).toEqual([3]);
		expect(filterCatalog(entries, 'nothing').map((row) => row.id)).toEqual([]);
	});

	it('filters by ground and by an exported mesh', () => {
		expect(filterCatalog(entries, '', 'ground').map((row) => row.id)).toEqual([2, 4, 1]);
		expect(filterCatalog(entries, '', 'mesh').map((row) => row.id)).toEqual([3, 2, 1]);
	});

	it('sorts by display name and caps the list', () => {
		expect(filterCatalog(entries, '').map((row) => row.id)).toEqual([3, 2, 4, 1]);
		expect(filterCatalog(entries, '', 'all', 2)).toHaveLength(2);
	});
});
