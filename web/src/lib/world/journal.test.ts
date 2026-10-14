import { describe, expect, it } from 'vitest';
import { journalView, type JournalEntry } from './journal';

// The shapes are the daemon's own (measured on a live journal):
//   set    {action, id, columns:{X:3,…}, values:{X:'…'}, expect:{X:'…'}}
//   add    {action, id, columns:{…}, values:{'NIF #','Textual Name',X,Y,Z,…}}
//   remove {action, id, columns:{…}, expect:{X:'…',Y:'…'}}
const zone = (id: number, ops: Array<Record<string, unknown>>) => ({ id, ops });
const rowOp = (patches: Array<Record<string, unknown>>) => ({ zone: 1, patches });
const columns = { X: 3, Y: 4, Z: 5, A: 6, Scale: 7 };

const only = (entry: JournalEntry | undefined) => {
	expect(entry).toBeDefined();
	return entry as JournalEntry;
};

describe('journalView — fixture ops', () => {
	it('shows both sides of a move, in the file’s own column order', () => {
		const view = journalView({
			zones: [
				zone(1, [
					{
						action: 'set',
						id: 1,
						columns,
						values: { X: '25472', Y: '47104' },
						expect: { X: '25344', Y: '47104' }
					}
				])
			],
			rows: []
		});
		const entry = only(view.zones[0].entries[0]);
		expect(entry.kind).toBe('move');
		expect(entry.target).toBe('#1');
		expect(entry.before).toBe('X 25344 · Y 47104');
		expect(entry.after).toBe('X 25472 · Y 47104');
		expect(entry.columns).toEqual(['X', 'Y']);
		expect(entry.note).toMatch(/ground row/);
	});

	it('drops the ground note when an explicit z was written (a lift)', () => {
		const entry = only(
			journalView({
				zones: [
					zone(1, [
						{
							action: 'set',
							id: 2,
							columns,
							values: { X: '10', Y: '20', Z: '2135' },
							expect: { X: '10', Y: '20', Z: '1879' }
						}
					])
				],
				rows: []
			}).zones[0].entries[0]
		);
		expect(entry.kind).toBe('move');
		expect(entry.note).toBeUndefined();
		expect(entry.before).toBe('X 10 · Y 20 · Z 1879');
		expect(entry.after).toBe('X 10 · Y 20 · Z 2135');
	});

	it('reads a turn as a turn, from the angle column it wrote', () => {
		const entry = only(
			journalView({
				zones: [
					zone(1, [
						{
							action: 'set',
							id: 7,
							columns,
							values: { X: '512', Y: '256', A: '90' },
							expect: { X: '512', Y: '256', A: '0' }
						}
					])
				],
				rows: []
			}).zones[0].entries[0]
		);
		expect(entry.kind).toBe('rotate');
		expect(entry.before).toBe('X 512 · Y 256 · A 0');
		expect(entry.after).toBe('X 512 · Y 256 · A 90');
	});

	it('reads a scale as the factor it wrote', () => {
		const entry = only(
			journalView({
				zones: [
					zone(1, [
						{
							action: 'set',
							id: 7,
							columns,
							values: { Scale: '110' },
							expect: { Scale: '100' }
						}
					])
				],
				rows: []
			}).zones[0].entries[0]
		);
		expect([entry.kind, entry.before, entry.after]).toEqual(['scale', 'Scale 100', 'Scale 110']);
	});

	it('names an addition by its catalog entry', () => {
		const entry = only(
			journalView({
				zones: [
					zone(1, [
						{
							action: 'add',
							id: 900,
							columns,
							values: {
								'NIF #': '88',
								'Textual Name': 'Swamp tree',
								X: '1',
								Y: '2',
								'Unique Id': '900'
							}
						}
					])
				],
				rows: []
			}).zones[0].entries[0]
		);
		expect(entry.target).toBe('Swamp tree');
		expect(entry.before).toBeNull();
		expect(entry.after).toBe('at 1, 2');
		expect(entry.note).toBe('catalog nif 88');
	});

	it('reads a removal with what it held', () => {
		const entry = only(
			journalView({
				zones: [
					zone(1, [
						{
							action: 'remove',
							id: 42,
							columns,
							expect: { X: '25600', Y: '47104' }
						}
					])
				],
				rows: []
			}).zones[0].entries[0]
		);
		expect([entry.kind, entry.target, entry.after]).toEqual(['remove', '#42', 'removed']);
		expect(entry.before).toBe('X 25600 · Y 47104');
	});

	it('keeps an action it does not know, so the count stays honest', () => {
		const view = journalView({
			zones: [zone(1, [{ action: 'warp', id: 3, columns, values: { X: '1' } }])],
			rows: []
		});
		expect(view.count).toBe(1);
		expect(only(view.zones[0].entries[0]).kind).toBe('warp');
	});
});

describe('journalView — server rows', () => {
	it('shows a patch’s columns, both sides', () => {
		const view = journalView({
			zones: [],
			rows: [
				rowOp([
					{
						table: 'mob',
						key: '8acf21ea99',
						values: { X: '512', Y: '256', Z: '2429', Heading: '984' },
						originals: { X: '25344', Y: '19968', Z: '2429', Heading: '984' }
					}
				])
			]
		});
		const entry = only(view.rows[0]);
		expect(entry.target).toBe('mob 8acf21ea');
		expect(entry.before).toBe('X 25344 · Y 19968 · Z 2429 · Heading 984');
		expect(entry.after).toBe('X 512 · Y 256 · Z 2429 · Heading 984');
		expect(entry.columns).toEqual(['X', 'Y', 'Z', 'Heading']);
	});

	it('reads a dropped row as a removal', () => {
		const entry = only(
			journalView({
				zones: [],
				rows: [rowOp([{ table: 'door', key: 'ab', deleted: true }])]
			}).rows[0]
		);
		expect([entry.target, entry.after]).toEqual(['door ab', 'removed']);
	});
});

describe('journalView — the whole thing', () => {
	it('counts every entry, fixtures and rows together', () => {
		const view = journalView({
			zones: [
				zone(1, [{ action: 'set', id: 1, columns, values: { X: '0', Y: '0' } }]),
				zone(2, [
					{ action: 'set', id: 2, columns, values: { X: '0', Y: '0' } },
					{ action: 'remove', id: 3 }
				])
			],
			rows: [rowOp([{ table: 'mob', key: 'a', values: { X: '1' }, originals: { X: '0' } }])]
		});
		expect(view.count).toBe(4);
		expect(view.zones.map((z) => z.id)).toEqual([1, 2]);
	});

	it('says nothing, cleanly, when the journal is empty', () => {
		const view = journalView({ zones: [], rows: [] });
		expect(view).toEqual({ zones: [], rows: [], count: 0 });
	});
});
