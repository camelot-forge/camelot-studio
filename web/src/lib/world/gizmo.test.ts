import { describe, expect, it } from 'vitest';
import { GIZMO_AXES, axisParam } from './gizmo';

describe('axisParam', () => {
	it('reads the coordinate the ray stands over, along the axis', () => {
		// a ray falling straight down at x = 5, onto the x axis
		const s = axisParam(
			{ x: 5, y: 10, z: 0 },
			{ x: 0, y: -1, z: 0 },
			{ x: 0, y: 0, z: 0 },
			{ x: 1, y: 0, z: 0 }
		);
		expect(s).toBeCloseTo(5, 9);
	});

	it('follows the scene frame: the zone’s y is scene +Z', () => {
		// a ray falling at scene z = 7, onto the zone-y axis (scene +Z)
		const s = axisParam(
			{ x: 0, y: 10, z: 7 },
			{ x: 0, y: -1, z: 0 },
			{ x: 0, y: 0, z: 0 },
			{ x: 0, y: 0, z: 1 }
		);
		expect(s).toBeCloseTo(7, 9);
	});

	it('finds the height when the ray is horizontal (the vertical arrow)', () => {
		const s = axisParam(
			{ x: 2, y: 3, z: 0 },
			{ x: 0, y: 0, z: -1 },
			{ x: 0, y: 0, z: 0 },
			{ x: 0, y: 1, z: 0 }
		);
		expect(s).toBeCloseTo(3, 9);
	});

	it('measures from the axis’ own origin', () => {
		const s = axisParam(
			{ x: -4, y: 8, z: 0 },
			{ x: 0, y: -1, z: 0 },
			{ x: 6, y: 0, z: 0 },
			{ x: 1, y: 0, z: 0 }
		);
		expect(s).toBeCloseTo(-10, 9);
	});

	it('refuses a ray parallel to the axis (nothing to grab)', () => {
		const s = axisParam(
			{ x: 0, y: 0, z: 0 },
			{ x: 0, y: 0, z: 1 },
			{ x: 3, y: 0, z: 0 },
			{ x: 0, y: 0, z: 1 }
		);
		expect(s).toBeNull();
	});
});

describe('GIZMO_AXES', () => {
	it('points the vertical arrow up, and the zone’s north along scene +Z', () => {
		const byAxis = Object.fromEntries(GIZMO_AXES.map((spec) => [spec.axis, spec.dir]));
		expect(byAxis.x).toEqual([1, 0, 0]);
		expect(byAxis.y).toEqual([0, 0, 1]);
		expect(byAxis.z).toEqual([0, 1, 0]);
	});

	it('wears the drafts’ own colours (X red, Y green, Z blue)', () => {
		expect(GIZMO_AXES.map((spec) => spec.color)).toEqual([0xe5484d, 0x46a758, 0x0090ff]);
	});
});
