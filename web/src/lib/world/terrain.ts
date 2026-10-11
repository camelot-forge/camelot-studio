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
	normals: Float32Array;
	indices: Uint32Array;
}

/** One patch's terrain mesh: 33×33 vertices, two triangles per subtile along
 * the client's diagonal (the one `heightAt` samples across), so a rendered
 * point and a height query agree by construction. */
/** One patch's terrain mesh: 33×33 vertices, two triangles per subtile along
 * the client's diagonal (the one `heightAt` samples across), so a rendered
 * point and a height query agree by construction.
 *
 * The normals come from `zoneNormals` — the whole zone's triangle normals,
 * averaged per grid vertex — so two patches share the normals along what would
 * be their seam: computing them per patch leaves a shading step on every
 * boundary of the 8×8 grid, and the client's terrain has none. */
export function patchGeometry(
	data: TerrainData,
	patchX: number,
	patchY: number,
	normals: Float32Array = zoneNormals(data)
): PatchGeometry {
	const side = SUBTILES_PER_PATCH + 1;
	const positions = new Float32Array(side * side * 3);
	const uvs = new Float32Array(side * side * 2);
	const normalArr = new Float32Array(side * side * 3);
	const baseX = patchX * SUBTILES_PER_PATCH;
	const baseY = patchY * SUBTILES_PER_PATCH;
	const stride = SUBTILES + 1;
	for (let j = 0; j < side; j++) {
		for (let i = 0; i < side; i++) {
			const x = (baseX + i) * UNITS_PER_SUBTILE;
			const y = (baseY + j) * UNITS_PER_SUBTILE;
			const h = data.heightAt(x, y);
			const v = j * side + i;
			positions[v * 3] = x;
			positions[v * 3 + 1] = h;
			positions[v * 3 + 2] = y;
			const grid = (baseY + j) * stride + (baseX + i);
			normalArr[v * 3] = normals[grid * 3];
			normalArr[v * 3 + 1] = normals[grid * 3 + 1];
			normalArr[v * 3 + 2] = normals[grid * 3 + 2];
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
	return { positions, uvs, normals: normalArr, indices };
}

/** The zone's vertex normals, one per height-grid node (`SUBTILES + 1`
 * squared): every subtile triangle's face normal accumulated into its three
 * corners, in the same triangulation `patchGeometry` draws (a–c–b, b–c–d).
 * Shared by all 64 patches, which is what keeps their shading continuous. */
export function zoneNormals(data: TerrainData): Float32Array {
	const stride = SUBTILES + 1;
	const heights = new Float32Array(stride * stride);
	for (let j = 0; j < stride; j++) {
		for (let i = 0; i < stride; i++) {
			heights[j * stride + i] = data.heightAt(i * UNITS_PER_SUBTILE, j * UNITS_PER_SUBTILE);
		}
	}
	const out = new Float32Array(stride * stride * 3);
	const unit = UNITS_PER_SUBTILE;
	for (let j = 0; j < SUBTILES; j++) {
		for (let i = 0; i < SUBTILES; i++) {
			const a = j * stride + i;
			const b = a + 1;
			const c = a + stride;
			const d = c + 1;
			// Two triangles, as drawn: (a, c, b) and (b, c, d), in the (x, y, z)
			// grid with +z up — the face normal is (dy1*dz2 - dz1*dy2, ...) over
			// the edges; written out for the axis-aligned subtile.
			const ha = heights[a];
			const hb = heights[b];
			const hc = heights[c];
			const hd = heights[d];
			// (a, c, b): u = c - a = (0, hc - ha, unit), v = b - a = (unit, hb - ha, 0)
			let nx = (hc - ha) * 0 - unit * (hb - ha);
			let ny = unit * unit - 0 * 0;
			let nz = 0 * (hb - ha) - (hc - ha) * unit;
			add(out, a, nx, ny, nz);
			add(out, c, nx, ny, nz);
			add(out, b, nx, ny, nz);
			// (b, c, d): u = c - b = (-unit, hc - hb, 0), v = d - b = (0, hd - hb, unit)
			nx = (hc - hb) * unit - 0 * (hd - hb);
			ny = 0 * 0 - -unit * unit;
			nz = -unit * (hd - hb) - (hc - hb) * 0;
			add(out, b, nx, ny, nz);
			add(out, c, nx, ny, nz);
			add(out, d, nx, ny, nz);
		}
	}
	for (let v = 0; v < stride * stride; v++) {
		const x = out[v * 3];
		const y = out[v * 3 + 1];
		const z = out[v * 3 + 2];
		const length = Math.hypot(x, y, z) || 1;
		out[v * 3] = x / length;
		out[v * 3 + 1] = y / length;
		out[v * 3 + 2] = z / length;
	}
	return out;
}

function add(out: Float32Array, vertex: number, x: number, y: number, z: number): void {
	out[vertex * 3] += x;
	out[vertex * 3 + 1] += y;
	out[vertex * 3 + 2] += z;
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
