// The carried-row arithmetic, pinned to the daemon's own numbers.
//
// Every expectation here is a *measured* value, not a hand calculation: the
// first two are what the daemon's `_move_with_fixture` produced from the real
// Keep of Camelot Hills and its two doors (server rows `67140be0` and
// `0678f11d`, read from MySQL where the zone's world rectangle is
// 548864/483328); the turn case is the daemon test's own example
// (`test_a_rotation_carries_the_rows_around_the_fixture`). If the two sides
// ever drift, one of them fails here.

import { describe, expect, it } from 'vitest';
import { carryRow, frame, type CarryFrame } from './carried';

// The real Keep: fixture 1 at 25344, 47104 (`A = 0`), its doors as the server
// holds them, all in zone-local coordinates.
const KEEP: CarryFrame = { x: 25344, y: 47104, z: 2896, angle: 0 };
const GATE: { x: number; y: number; z: number; heading: number } = {
	x: 25112,
	y: 46957,
	z: 2928,
	heading: 4067
};
const POSTERN = { x: 25099, y: 47593, z: 2933, heading: 22 };

describe('frame', () => {
	it('is the client’s own: A = 0 faces north, 90 east, on a map (x east, y south)', () => {
		expect(frame(0)).toEqual([
			[1, 0],
			[0, -1]
		]);
		const [x, y] = frame(90);
		expect(x[0]).toBeCloseTo(0);
		expect(x[1]).toBeCloseTo(1);
		expect(y[0]).toBeCloseTo(1);
		expect(y[1]).toBeCloseTo(0);
	});
});

describe('carryRow', () => {
	it('moves the keep’s doors with the keep, exactly as the daemon does', () => {
		const after: CarryFrame = { ...KEEP, x: KEEP.x - 2048 };
		expect(carryRow(KEEP, after, GATE)).toEqual({
			x: KEEP.x - 2048 - (KEEP.x - GATE.x),
			y: GATE.y,
			z: GATE.z,
			heading: GATE.heading
		});
		expect(carryRow(KEEP, after, POSTERN).x).toBe(23051);
		expect(carryRow(KEEP, after, GATE).x).toBe(23064);
	});

	it('turns a door about the keep, heading and all', () => {
		// the daemon's own example: a door 500 north of a fixture at A = 0 ends
		// up 500 east of it after a quarter turn, its heading +1024
		const before: CarryFrame = { x: 19660, y: 19660, z: 0, angle: 0 };
		const after: CarryFrame = { ...before, angle: 90 };
		const moved = carryRow(before, after, {
			x: 19660,
			y: 19160,
			z: 10,
			heading: 0
		});
		expect(Math.round(moved.x)).toBe(20160);
		expect(Math.round(moved.y)).toBe(19660);
		expect(moved.z).toBe(10);
		expect(moved.heading).toBe(1024);
	});

	it('keeps a row’s height relative to the fixture', () => {
		const before: CarryFrame = { x: 0, y: 0, z: 100, angle: 0 };
		const after: CarryFrame = { x: 0, y: 0, z: 350, angle: 0 };
		expect(carryRow(before, after, { x: 0, y: 0, z: 140 }).z).toBe(390);
	});
});
