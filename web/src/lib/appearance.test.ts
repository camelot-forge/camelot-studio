import { describe, expect, it } from 'vitest';
import {
	appearanceRows,
	clampAppearance,
	combinationCount,
	pieceSlots,
	randomAppearance,
	type AppearanceTables
} from './appearance';

const tables = (over: Partial<AppearanceTables> = {}): AppearanceTables => ({
	faces: 7,
	hair_styles: 4,
	hair_colors: 16,
	eye_colors: 8,
	skin_tones: 8,
	cloak_variants: 6,
	...over
});

describe('combinationCount', () => {
	it('counts the picks the steppers can reach', () => {
		// 7 faces × 4 styles × 16 colours × 8 eyes × 8 tones × (6 cloaks + none)
		expect(combinationCount(tables())).toBe(7 * 4 * 16 * 8 * 8 * 7);
	});

	it('reads a table the race does not carry as its one legal pick', () => {
		expect(combinationCount(tables({ faces: 0, cloak_variants: 0 }))).toBe(1 * 4 * 16 * 8 * 8 * 1);
	});
});

describe('appearanceRows', () => {
	it('walks the draft’s order, cloak last', () => {
		expect(appearanceRows(tables()).map((r) => [r.label, r.min, r.max])).toEqual([
			['Face', 1, 7],
			['Hair style', 1, 4],
			['Hair color', 1, 16],
			['Eye color', 1, 8],
			['Skin tone', 1, 8],
			['Cloak', 0, 6]
		]);
	});

	it('drops the cloak row when the race offers none', () => {
		expect(appearanceRows(tables({ cloak_variants: 0 })).map((r) => r.key)).not.toContain('cloak');
	});
});

describe('randomAppearance', () => {
	it('stays inside every row, for the edges of rand', () => {
		for (const value of [0, 0.5, 0.999999, 1]) {
			const pick = randomAppearance(tables(), () => value);
			for (const row of appearanceRows(tables())) {
				expect(pick[row.key]).toBeGreaterThanOrEqual(row.min);
				expect(pick[row.key]).toBeLessThanOrEqual(row.max);
			}
		}
	});

	it('reaches both ends of the space', () => {
		const low = randomAppearance(tables(), () => 0);
		expect(low).toEqual({
			face: 1,
			hairStyle: 1,
			hairColor: 1,
			eyeColor: 1,
			skinTone: 1,
			cloak: 0
		});
		const high = randomAppearance(tables(), () => 0.999999);
		expect(high).toEqual({
			face: 7,
			hairStyle: 4,
			hairColor: 16,
			eyeColor: 8,
			skinTone: 8,
			cloak: 6
		});
	});

	it('never picks a cloak when the race has none', () => {
		expect(randomAppearance(tables({ cloak_variants: 0 }), () => 0.99).cloak).toBe(0);
	});
});

describe('pieceSlots', () => {
	it('names each piece once, in the document’s order', () => {
		expect(pieceSlots(['Eyes', 'Head', 'Body', 'Body', 'Cloak'])).toEqual([
			'Eyes',
			'Head',
			'Body',
			'Cloak'
		]);
	});

	it('drops the exporter’s own shape suffix', () => {
		expect(pieceSlots(['Body1:0', 'Body1b:17'])).toEqual(['Body1', 'Body1b']);
	});

	it('says nothing about a figure with no named mesh', () => {
		expect(pieceSlots(['', '  ', ':3'])).toEqual([]);
	});
});

describe('clampAppearance', () => {
	it('pulls a pick into a tighter race', () => {
		const pick = { face: 7, hairStyle: 4, hairColor: 16, eyeColor: 8, skinTone: 8, cloak: 6 };
		expect(clampAppearance(tables({ faces: 5, cloak_variants: 2 }), pick)).toEqual({
			face: 5,
			hairStyle: 4,
			hairColor: 16,
			eyeColor: 8,
			skinTone: 8,
			cloak: 2
		});
	});

	it('leaves a pick that already fits alone', () => {
		const pick = { face: 3, hairStyle: 2, hairColor: 7, eyeColor: 3, skinTone: 5, cloak: 1 };
		expect(clampAppearance(tables(), pick)).toEqual(pick);
	});

	it('brings a zero cloak back to zero, never to a cloak', () => {
		expect(
			clampAppearance(tables({ cloak_variants: 4 }), {
				face: 1,
				hairStyle: 1,
				hairColor: 1,
				eyeColor: 1,
				skinTone: 1,
				cloak: 0
			}).cloak
		).toBe(0);
	});
});
