import { describe, expect, it } from 'vitest';
import type { WorldSpawns } from '$lib/api/client';
import { entitiesFromSpawns, filterEntities } from './entities';

const spawns = {
	source: 'test',
	counts: { mob: 3, worldobject: 1, door: 1, path: 0, pathpoints: 0 },
	mobs: [
		{
			id: 'mob-a',
			class_type: 'DOL.GS.GameNPC',
			name: 'Viking Archer',
			suffix: '',
			guild: 'Vikings',
			x: 100,
			y: 200,
			z: 10,
			heading: 0,
			level: 12,
			realm: 1,
			model: 501,
			size: 50,
			gender: 0,
			flags: 0,
			aggro_level: 0,
			aggro_range: 0,
			path: null,
			npc_template: 1
		},
		{
			id: 'mob-b',
			class_type: 'DOL.GS.GameMerchant',
			name: 'Weapon Merchant',
			suffix: '',
			guild: '',
			x: 400,
			y: 400,
			z: 10,
			heading: 0,
			level: 5,
			realm: 1,
			model: 99,
			size: 50,
			gender: 0,
			flags: 0,
			aggro_level: 0,
			aggro_range: 0,
			path: null,
			npc_template: 2
		},
		{
			id: 'mob-c',
			class_type: 'DOL.GS.GameNPC',
			name: 'Boar',
			suffix: '',
			guild: '',
			x: 300,
			y: 100,
			z: 10,
			heading: 0,
			level: 2,
			realm: 0,
			model: 99,
			size: 50,
			gender: 0,
			flags: 0,
			aggro_level: 0,
			aggro_range: 0,
			path: null,
			npc_template: 3
		}
	],
	objects: [
		{
			id: 'obj-a',
			class_type: 'DOL.GS.GameStaticItem',
			name: 'Albion Banner',
			x: 150,
			y: 250,
			z: 10,
			heading: 0,
			model: 464,
			emblem: 0,
			realm: 0
		}
	],
	doors: [
		{
			id: 'door-a',
			name: 'Keep Door',
			type: 0,
			x: 120,
			y: 220,
			z: 10,
			heading: 0,
			level: 0,
			realm: 0,
			is_postern: false,
			locked: 0
		}
	],
	paths: [],
	warnings: []
} as WorldSpawns;

describe('the zone entity list', () => {
	it('lists every table with its own fields', () => {
		const rows = entitiesFromSpawns(spawns);
		expect(rows).toHaveLength(5);
		expect(rows.find((row) => row.id === 'mob-a')?.level).toBe(12);
		expect(rows.find((row) => row.id === 'obj-a')?.model).toBe(464);
		expect(rows.find((row) => row.id === 'door-a')?.table).toBe('door');
		expect(entitiesFromSpawns(null)).toEqual([]);
	});

	it('searches name, guild, id and model with every token', () => {
		const rows = entitiesFromSpawns(spawns);
		expect(filterEntities(rows, { query: 'viking archer' }).map((row) => row.id)).toEqual([
			'mob-a'
		]);
		expect(filterEntities(rows, { query: 'vikings' }).map((row) => row.id)).toEqual(['mob-a']);
		expect(filterEntities(rows, { query: 'obj-a' }).map((row) => row.id)).toEqual(['obj-a']);
		expect(filterEntities(rows, { query: '99' }).map((row) => row.id)).toEqual(['mob-c', 'mob-b']);
		expect(filterEntities(rows, { query: '99 boar' }).map((row) => row.id)).toEqual(['mob-c']);
	});

	it('filters by table and town NPCs', () => {
		const rows = entitiesFromSpawns(spawns);
		expect(filterEntities(rows, { table: 'mob' })).toHaveLength(3);
		expect(filterEntities(rows, { table: 'worldobject' }).map((row) => row.id)).toEqual(['obj-a']);
		expect(filterEntities(rows, { townOnly: true }).map((row) => row.id)).toEqual(['mob-a']);
	});

	it('sorts by name, level or distance from an origin', () => {
		const rows = entitiesFromSpawns(spawns);
		expect(filterEntities(rows, { sort: 'level' }).map((row) => row.id)).toEqual([
			'obj-a',
			'door-a',
			'mob-c',
			'mob-b',
			'mob-a'
		]);
		const near = filterEntities(rows, { sort: 'distance', origin: { x: 100, y: 200 } });
		expect(near[0].id).toBe('mob-a');
		expect(near.every((row) => typeof row.distance === 'number')).toBe(true);
	});

	it('caps the list', () => {
		expect(filterEntities(entitiesFromSpawns(spawns), { limit: 2 })).toHaveLength(2);
	});
});
