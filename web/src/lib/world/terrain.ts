// The client's world geometry, in TypeScript: the terrain binary `CAMT`,
// the client's own height sampling, and the patch meshes the viewer builds.
//
// The sampling here must stay bit-for-bit the Python model
// (`camelot.zones.Zone.height_at`): a point on the rendered mesh, a ground
// placement from the editor, and the value the server writes into a `mob`
// row all have to agree. The test vectors in `terrain.test.ts` were
// generated from that implementation, not re-derived.

export const ZONE_UNIT = 8192;
export const ZONE_SIZE = 65536;
/** The ground-texture grid: 8×8 cells, one `texXX-YY.dds` per cell. */
export const PATCH_CELLS = 8;
/** One `terrain.pcx` pixel per 256-unit subtile of the 65536-unit zone. */
export const SUBTILES = 256;
export const UNITS_PER_SUBTILE = ZONE_SIZE / SUBTILES;
export const SUBTILES_PER_PATCH = SUBTILES / PATCH_CELLS;

export interface TerrainData {
	width: number;
	height: number;
	terrainFactor: number;
	offsetFactor: number;
	terrain: Uint8Array;
	offset: Uint8Array;
	/** The ground height at a zone-local point, as the client samples it. */
	heightAt(x: number, y: number): number;
}

const MAGIC = 0x544d4143; // 'CAMT' little-endian

export function parseTerrain(buffer: ArrayBuffer): TerrainData {
	const view = new DataView(buffer);
	if (view.byteLength < 18 || view.getUint32(0, true) !== MAGIC) {
		throw new Error('not a CAMT terrain payload');
	}
	const version = view.getUint16(4, true);
	if (version !== 1) throw new Error(`unsupported terrain version ${version}`);
	const width = view.getUint16(6, true);
	const height = view.getUint16(8, true);
	const terrainFactor = view.getFloat32(10, true);
	const offsetFactor = view.getFloat32(14, true);
	const length = width * height;
	if (view.byteLength !== 18 + length * 2) {
		throw new Error(`terrain payload is ${view.byteLength} bytes, expected ${18 + length * 2}`);
	}
	const terrain = new Uint8Array(buffer, 18, length);
	const offset = new Uint8Array(buffer, 18 + length, length);

	// The client's sampling: row-major, bilinear across each subtile's
	// diagonal (xPercent + yPercent <= 1 picks the first triangle). Same
	// arithmetic order as `height_at` — the copies must not drift.
	function pixel(ix: number, iy: number): number {
		ix = Math.min(Math.max(ix, 0), width - 1);
		iy = Math.min(Math.max(iy, 0), height - 1);
		const index = iy * width + ix;
		return terrain[index] * terrainFactor + offset[index] * offsetFactor;
	}

	function heightAt(x: number, y: number): number {
		const fx = Math.min((Math.max(x, 0) * width) / ZONE_SIZE, width - 1);
		const fy = Math.min((Math.max(y, 0) * height) / ZONE_SIZE, height - 1);
		const left = Math.floor(fx);
		const top = Math.floor(fy);
		const right = Math.min(left + 1, width - 1);
		const bottom = Math.min(top + 1, height - 1);
		let xPercent = fx - left;
		let yPercent = fy - top;
		if (xPercent + yPercent <= 1.0) {
			const edge = pixel(left, top);
			const first = edge + (pixel(left, bottom) - edge) * yPercent;
			const second = pixel(right, top) + (pixel(left, bottom) - pixel(right, top)) * yPercent;
			return first + (second - first) * xPercent;
		}
		xPercent = 1.0 - xPercent;
		yPercent = 1.0 - yPercent;
		const edge = pixel(right, bottom);
		const first = edge + (pixel(right, top) - edge) * yPercent;
		const second = pixel(left, bottom) + (pixel(right, top) - pixel(left, bottom)) * yPercent;
		return first + (second - first) * xPercent;
	}

	return { width, height, terrainFactor, offsetFactor, terrain, offset, heightAt };
}

/** A zone-local point in scene space.
 *
 * The client's zone frame is left-handed: `+x` is east, **`+y` is south**,
 * `+z` up (the server's own compass, `Point2D.GetPointFromHeading`: heading
 * 0 at `+y`, `where.cs` calling it "South"; 1024 at `-x`, "West"). A
 * right-handed three.js scene therefore cannot use a rotation to show it —
 * that mirrors the world. Keep `x` and `up`, flip `y`: DAoC's `+y` (south)
 * is scene **+Z**, and a camera south of its target looks north with north
 * up, east right — the client's own map orientation. The zone meshes are
 * exported with the same reflection (`nifutil.WORLD_AXIS_CONVERSION`). */
export function zoneToScene(x: number, y: number, z: number): [number, number, number] {
	return [x, z, y];
}

/** A fixture's heading as a scene yaw, radians about +Y.
 *
 * The corpus settles the unit: `fixtures.csv`'s `A` runs 0..359 with every
 * human value on top (0/90/180/45/270/315) — **degrees**, not the server's
 * 12-bit grid (`door.Heading` is 0..4095; 1024 = 90° there). The sense is
 * counter-clockwise in DAoC's (x, y) plane, measured on the bridges: each
 * one's long axis (from its mesh's own vertices) lies along its river's
 * bank-to-bank direction, and `A` matches that rotation sign across a dozen
 * rows in six zones (`notes/heading-convention.py`). `A = 0` faces
 * `+y` = south — the client's compass — and 90° faces `-x` = west.
 *
 * The plane is mirrored into the scene (`+y` -> `+Z`), which reverses the
 * rotation's sense: the scene yaw is `-A` about +Y, which sends the mesh's
 * own `+y` (its forward) to `+Z` at `A = 0` and to `-X` (west) at `A = 90`,
 * as the client places it. */
export function headingYaw(angle: number): number {
	const yaw = -(((angle % 360) * Math.PI * 2) / 360);
	// -0 is a different value to Object.is; callers compare yaws exactly.
	return yaw === 0 ? 0 : yaw;
}

/** The Z a fixture is drawn at.
 *
 * `Ground = 1` means "stand it on the terrain" — the client drops the row
 * wherever its authored `Z` sits, and the corpus shows why it must: Camelot
 * Hills ships its pines at `Z = 1600` where the terrain is 1984 (384 *under*
 * the ground), and in game they stand on the ground (user comparison,
 * 2026-10-11). `Ground = 0` rows carry a deliberate height (walls, decks)
 * and keep their `Z`.
 */
export function fixtureZ(
	data: TerrainData,
	fixture: { x: number; y: number; z: number; ground: boolean }
): number {
	if (fixture.ground) return data.heightAt(fixture.x, fixture.y);
	return fixture.z;
}

export interface PatchGeometry {
	positions: Float32Array;
	uvs: Float32Array;
	indices: Uint32Array;
}

/** One patch's terrain mesh: 33×33 vertices, two triangles per subtile along
 * the client's diagonal (the one `heightAt` samples across), so a rendered
 * point and a height query agree by construction. */
export function patchGeometry(data: TerrainData, patchX: number, patchY: number): PatchGeometry {
	const side = SUBTILES_PER_PATCH + 1;
	const positions = new Float32Array(side * side * 3);
	const uvs = new Float32Array(side * side * 2);
	const baseX = patchX * SUBTILES_PER_PATCH;
	const baseY = patchY * SUBTILES_PER_PATCH;
	for (let j = 0; j < side; j++) {
		for (let i = 0; i < side; i++) {
			const x = (baseX + i) * UNITS_PER_SUBTILE;
			const y = (baseY + j) * UNITS_PER_SUBTILE;
			const h = data.heightAt(x, y);
			const v = j * side + i;
			positions[v * 3] = x;
			positions[v * 3 + 1] = h;
			positions[v * 3 + 2] = y;
			// uv within the patch; the sheet is "the ground seen from above"
			// (north up), so image row 0 is the patch's north edge — its lowest
			// y, since `+y` is south. Row 0 lands here on the `v = 1` side.
			uvs[v * 2] = (x - patchX * ZONE_UNIT) / ZONE_UNIT;
			uvs[v * 2 + 1] = 1 - (y - patchY * ZONE_UNIT) / ZONE_UNIT;
		}
	}
	const indices = new Uint32Array(SUBTILES_PER_PATCH * SUBTILES_PER_PATCH * 6);
	let o = 0;
	for (let j = 0; j < SUBTILES_PER_PATCH; j++) {
		for (let i = 0; i < SUBTILES_PER_PATCH; i++) {
			const a = j * side + i;
			const b = a + 1;
			const c = a + side;
			const d = c + 1;
			// Counter-clockwise seen from +Y in scene space (the terrain face
			// points up); the diagonal is b–c, the one the client's sampling
			// splits on. The winding is the mirror of the client's own (x, y)
			// winding, as `zoneToScene` mirrors the plane.
			indices[o++] = a;
			indices[o++] = c;
			indices[o++] = b;
			indices[o++] = b;
			indices[o++] = c;
			indices[o++] = d;
		}
	}
	return { positions, uvs, indices };
}

/** A fixture's placement: scene position, yaw and scale (percent → factor). */
export function fixturePlacement(
	data: TerrainData,
	fixture: { x: number; y: number; z: number; angle: number; scale: number; ground: boolean }
): { position: [number, number, number]; yaw: number; scale: number } {
	return {
		position: zoneToScene(fixture.x, fixture.y, fixtureZ(data, fixture)),
		yaw: headingYaw(fixture.angle),
		scale: (fixture.scale || 100) / 100
	};
}
