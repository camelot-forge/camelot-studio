// The rows a fixture move carries (B2), in the browser's own arithmetic.
//
// The daemon is the authority (`serve/editor.py`'s `_move_with_fixture`), and
// this is the same arithmetic, kept in one place so the drag can show what the
// server will hold. The two are held together by `carried.test.ts`, which
// asserts numbers measured on the real Keep and its doors (Camelot Hills,
// server rows 67140be0 and 0678f11d).

/** A fixture's placement as this maths needs it: zone-local map `x`/`y`, the
 * `z` the viewer draws it at, and `A` in degrees. */
export interface CarryFrame {
	x: number;
	y: number;
	z: number;
	angle: number;
}

/** The part of a server row a move rewrites. */
export interface CarryRow {
	x: number;
	y: number;
	z: number;
	heading?: number;
}

/** A fixture's own frame as map vectors: its local `+x` and `+y`. Measured
 * conventions: `A` is degrees with 0 = the mesh's front to the north, 90 =
 * east (the keep's gatehouse is north at `A = 0`), and the map is `(x east,
 * y south)`. So `+y` maps to `(sin A, -cos A)`. */
export function frame(angle: number): [[number, number], [number, number]] {
	const radians = (angle * Math.PI) / 180;
	return [
		[Math.cos(radians), Math.sin(radians)],
		[Math.sin(radians), -Math.cos(radians)]
	];
}

/** One row after the fixture moved: its position re-projected through the
 * fixture's frame (so a rotation turns it about the fixture exactly), its
 * height kept relative to the fixture, and its 12-bit heading turned with it —
 * both compasses run clockwise on a north-up map, so the turn carries over
 * with the unit change alone. Scaling a fixture does not scale its rows, and
 * that is written down rather than guessed at. */
export function carryRow(from: CarryFrame, to: CarryFrame, row: CarryRow): CarryRow {
	const [oldX, oldY] = frame(from.angle);
	const [newX, newY] = frame(to.angle);
	const dx = row.x - from.x;
	const dy = row.y - from.y;
	const localX = dx * oldX[0] + dy * oldX[1];
	const localY = dx * oldY[0] + dy * oldY[1];
	const moved: CarryRow = {
		x: to.x + localX * newX[0] + localY * newY[0],
		y: to.y + localX * newX[1] + localY * newY[1],
		z: Math.round(row.z - from.z + to.z)
	};
	if (row.heading !== undefined) {
		const turn = (((to.angle - from.angle) % 360) + 360) % 360;
		moved.heading = Math.round(row.heading + (turn * 4096) / 360) % 4096;
	}
	return moved;
}
