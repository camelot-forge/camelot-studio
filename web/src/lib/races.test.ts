import { describe, expect, it } from 'vitest';
import { RACES } from './races';

describe('RACES', () => {
	it('lists the 18 playable races', () => {
		expect(RACES).toHaveLength(18);
	});

	it('covers both fig3 rows per race, ids 1..36 with no duplicate', () => {
		const ids = RACES.flatMap((race) => [race.male, race.female]);
		expect(new Set(ids).size).toBe(36);
		expect(Math.min(...ids)).toBe(1);
		expect(Math.max(...ids)).toBe(36);
		for (const race of RACES) expect(race.female).toBe(race.male + 1);
	});

	it('has six races per realm', () => {
		for (const realm of ['Albion', 'Midgard', 'Hibernia'] as const) {
			expect(RACES.filter((race) => race.realm === realm)).toHaveLength(6);
		}
	});

	it('includes the races the client exposes beyond the first 15', () => {
		const names = RACES.map((race) => race.name);
		expect(names).toEqual(expect.arrayContaining(['Troll', 'Celt', 'Sylvan']));
	});
});
