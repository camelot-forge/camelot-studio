/** The gizmo's own math (the drafts' Unity-style arrows), kept free of three.js
 * so it can be tested: which way each axis points in the *scene's* frame, and
 * where a pointer ray meets an axis line.
 *
 * The scene renders zone-local `(x, y, z)` as three.js `(x, z, y)` — the zone is
 * `(x, y)` horizontal with `z` up (DAoC), three.js is `y` up. The mapping lives
 * here, named, once. */
export type GizmoAxis = 'x' | 'y' | 'z';

export interface GizmoAxisSpec {
	axis: GizmoAxis;
	/** Unit direction in scene space. */
	dir: [number, number, number];
	/** The drafts' axis colours (X red, Y green, Z blue). */
	color: number;
}

export const GIZMO_AXES: readonly GizmoAxisSpec[] = [
	{ axis: 'x', dir: [1, 0, 0], color: 0xe5484d },
	// the zone's `y` (north) is scene +Z
	{ axis: 'y', dir: [0, 0, 1], color: 0x46a758 },
	// the zone's `z` (height) is scene +Y — the vertical arrow
	{ axis: 'z', dir: [0, 1, 0], color: 0x0090ff }
];

export interface Vec3 {
	x: number;
	y: number;
	z: number;
}

/** The parameter `s` of the point on the line `a + s·e` closest to the ray
 * `o + t·d` — what a drag along one axis measures. Null when the two are
 * parallel (a pointer looking straight down an arrow has nothing to grab). */
export function axisParam(o: Vec3, d: Vec3, a: Vec3, e: Vec3): number | null {
	const wx = o.x - a.x;
	const wy = o.y - a.y;
	const wz = o.z - a.z;
	const dd = d.x * d.x + d.y * d.y + d.z * d.z;
	const de = d.x * e.x + d.y * e.y + d.z * e.z;
	const ee = e.x * e.x + e.y * e.y + e.z * e.z;
	const dw = d.x * wx + d.y * wy + d.z * wz;
	const ew = e.x * wx + e.y * wy + e.z * wz;
	const denom = dd * ee - de * de;
	if (Math.abs(denom) < 1e-9 * dd * ee) return null;
	return (dd * ew - de * dw) / denom;
}
