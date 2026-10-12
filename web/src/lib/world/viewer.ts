// The `/world` scene: the client's terrain, its fixtures, and the overlays.
//
// Everything here consumes the daemon's artefacts (`/api/v1/world/…`): the
// CAMT terrain planes, glTF meshes with the sheets the NIF itself names, and
// the DDS→PNG ground sheets. Nothing parses a client file in the browser.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import {
	api,
	type SpawnRow,
	type SpawnTable,
	type WorldFixture,
	type WorldSpawns,
	type WorldTextureLayer,
	type WorldZone
} from '$lib/api/client';
import { loadFromGlb, textureFromDds, tintColor, textureSide } from '$lib/gltf/load';
import { groundMaterial, groundSlots, type GroundSlot } from './ground';
import { carryRow, type CarryFrame } from './carried';
import {
	PATCH_CELLS,
	ZONE_SIZE,
	ZONE_UNIT,
	fixturePlacement,
	fixtureZ,
	parseTerrain,
	patchGeometry,
	zoneNormals,
	type TerrainData
} from './terrain';

/** Bounds/water/jumps sit a hair above the ground so they never z-fight it. */
const OVERLAY_LIFT = 8;

/** The soft dot the row points are drawn with (one texture, shared). */
function dotSprite(): THREE.Texture {
	const size = 64;
	const canvas = document.createElement('canvas');
	canvas.width = size;
	canvas.height = size;
	const context = canvas.getContext('2d')!;
	const gradient = context.createRadialGradient(
		size / 2,
		size / 2,
		0,
		size / 2,
		size / 2,
		size / 2
	);
	gradient.addColorStop(0, 'rgba(255,255,255,1)');
	gradient.addColorStop(0.55, 'rgba(255,255,255,0.85)');
	gradient.addColorStop(1, 'rgba(255,255,255,0)');
	context.fillStyle = gradient;
	context.fillRect(0, 0, size, size);
	const texture = new THREE.CanvasTexture(canvas);
	texture.needsUpdate = true;
	return texture;
}

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

export interface WorldToggles {
	fixtures: boolean;
	ground: boolean;
	spawns: boolean;
	statics: boolean;
	doors: boolean;
	patrols: boolean;
	bounds: boolean;
	water: boolean;
	jumps: boolean;
}

interface LoadedMesh {
	model: Awaited<ReturnType<typeof loadFromGlb>>;
}

/** One instanced group: the mesh drawn, the fixtures it stands for (in
 * instance order — what a click raycasts against), and the mesh's own local
 * matrix, kept so a moved instance can be recomposed exactly (6.3). */
interface PickGroup {
	mesh: THREE.InstancedMesh;
	fixtures: WorldFixture[];
	local: THREE.Matrix4;
}

export type EditMode = 'off' | 'translate' | 'rotate' | 'scale' | 'add';
export type EditKind = 'move' | 'rotate' | 'scale';

/** Snap steps: the zone's own subtile (65536 / 256, measured from the
 * terrain grid), 15 degrees and 5 percent — editor conveniences, named. */
//: the zone's own subtile (65536 / 256, measured from the
//: terrain grid), 15 degrees and 5 percent — editor conveniences, named.
const SNAP_POSITION = 256;
const SNAP_ANGLE = (15 * Math.PI) / 180;
const SNAP_SCALE = 0.05;

/** The server's `Heading` is a 12-bit compass (measured): 4096 steps to the
 * turn, clockwise from north — the same way `fixtures.csv`'s `A` runs. */
const HEADING_UNITS = 4096;

function readAngle(yaw: number): number {
	const degrees = (-yaw * 360) / (Math.PI * 2);
	return ((degrees % 360) + 360) % 360;
}

/** A row's 12-bit heading as the scene's yaw (the fixtures' own convention). */
function headingYaw(heading: number): number {
	return -(heading * Math.PI * 2) / HEADING_UNITS;
}

export interface Picked {
	fixture: WorldFixture;
	point: THREE.Vector3;
}

/** One server row the viewer can select (`mob`, `worldobject` or `door` — the
 * three tables the daemon patches, keyed by the server's own ids). */
export interface RowPick {
	table: SpawnTable;
	id: string;
}

/** A row proposed to travel with a fixture (B2): the daemon validates the key
 * against the database before it writes anything. */
export interface RowProposal {
	table: SpawnTable;
	key: string;
	name: string;
	distance: number;
}

/** One row cloud: the points drawn, the rows behind them (in buffer order —
 * what a click raycasts against), and the layer the toggle governs. */
interface RowCloud {
	table: SpawnTable;
	group: THREE.Group;
	rows: SpawnRow[];
	cloud: THREE.Points;
	size: number;
}

export class WorldViewer {
	private canvas: HTMLCanvasElement;
	private renderer: THREE.WebGLRenderer;
	private scene = new THREE.Scene();
	private camera: THREE.PerspectiveCamera;
	private controls: OrbitControls;
	private groups = {
		ground: new THREE.Group(),
		fixtures: new THREE.Group(),
		spawns: new THREE.Group(),
		statics: new THREE.Group(),
		doors: new THREE.Group(),
		patrols: new THREE.Group(),
		bounds: new THREE.Group(),
		water: new THREE.Group(),
		jumps: new THREE.Group()
	};
	private dot: THREE.Texture;
	private spawnCounts = { mobs: 0, statics: 0, doors: 0, patrols: 0, patrolPoints: 0 };
	private meshCache = new Map<string, LoadedMesh>();
	private groundTextures = new Map<string, THREE.Texture>();
	private terrain: TerrainData | null = null;
	private texturedPatches = 0;
	private composedPatches = 0;
	private picks: PickGroup[] = [];
	private rowClouds: RowCloud[] = [];
	private selectedRow: RowPick | null = null;
	/** The height a row keeps *above the ground* while it is dragged: the same
	 * rule the daemon applies to a `row-move` (a spawn on the ground stays on
	 * it, a gate keeps sitting on its wall). */
	private rowOffset = 0;
	/** The rows a selected fixture carries: proposed against the payload as it
	 * stood when it was selected, so the drag that follows uses the pairs the
	 * user was shown (a moved fixture has left its doors behind). */
	private carried: RowProposal[] = [];
	private carriedFor: number | null = null;
	/** A drag's own start: the fixture placement and the carried rows as they
	 * stood, so every frame re-projects them from there — the daemon's own
	 * arithmetic, never an accumulation of deltas. */
	private dragFrom: CarryFrame | null = null;
	private dragRows: Array<{
		table: SpawnTable;
		row: SpawnRow;
		from: { x: number; y: number; z: number; heading?: number };
	}> = [];
	private marker = new THREE.Group();
	private transform: TransformControls | null = null;
	private proxy = new THREE.Object3D();
	private grid: THREE.GridHelper | null = null;
	private editMode: EditMode = 'off';
	private dragging = false;
	/** The page's hooks: a finished drag, an add-mode ground click, and a
	 * finished row drag (the page journals the `row-move`). */
	onEdit: ((kind: EditKind, fixture: WorldFixture) => void) | null = null;
	onPick: ((point: { x: number; y: number; z: number }) => void) | null = null;
	onRowMove: ((pick: RowPick) => void) | null = null;
	private selected: WorldFixture | null = null;
	private token = 0;
	private frame = 0;

	constructor(canvas: HTMLCanvasElement) {
		this.canvas = canvas;
		this.dot = dotSprite();
		this.scene.background = new THREE.Color(0x0f1418);
		this.scene.fog = new THREE.Fog(0x0f1418, 60000, 160000);
		this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
		this.camera = new THREE.PerspectiveCamera(50, 1, 10, 400000);
		this.controls = new OrbitControls(this.camera, canvas);
		this.controls.enableDamping = true;
		this.controls.dampingFactor = 0.08;
		this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
		this.scene.add(
			new THREE.HemisphereLight(0xdfeaff, 0x3a3f35, 1.0),
			new THREE.DirectionalLight(0xffffff, 1.6)
		);
		const sun = this.scene.children[this.scene.children.length - 1] as THREE.DirectionalLight;
		sun.position.set(0.55, 1, 0.35).multiplyScalar(50000);
		for (const group of Object.values(this.groups)) this.scene.add(group);
		this.scene.add(this.proxy); // TransformControls refuses an unattached object
		this.marker.visible = false;
		this.buildMarker();
		this.scene.add(this.marker);
		this.resize();
	}

	/** True while a gizmo handle is under the pointer or being dragged — a
	 * click then belongs to the gizmo, not to selection. */
	get editing(): boolean {
		return this.dragging || (this.transform?.axis ?? null) !== null;
	}

	/** Arm the editing tools (6.3). `translate`/`rotate`/`scale` attach a gizmo
	 * to the selected fixture; `add` only arms the ground click. */
	setEditMode(mode: EditMode, snap = true): void {
		this.editMode = mode;
		if (mode === 'translate' || mode === 'rotate' || mode === 'scale') {
			if (!this.transform) {
				this.transform = new TransformControls(this.camera, this.canvas);
				this.transform.addEventListener('dragging-changed', (event) => {
					this.dragging = Boolean((event as { value: unknown }).value);
					this.controls.enabled = !this.dragging;
					if (this.dragging) {
						this.beginDrag();
						return;
					}
					if (this.selectedRow) this.onRowMove?.(this.selectedRow);
					else if (this.selected) this.onEdit?.(this.editKind(), this.selected);
				});
				this.transform.addEventListener('objectChange', () => this.dragApply());
				this.scene.add(this.transform.getHelper());
			}
			this.transform.setMode(mode);
			this.transform.setTranslationSnap(snap ? SNAP_POSITION : null);
			this.transform.setRotationSnap(snap ? SNAP_ANGLE : null);
			this.transform.setScaleSnap(snap ? SNAP_SCALE : null);
			this.attachProxy();
			this.showGrid(true);
			return;
		}
		this.transform?.detach();
		this.showGrid(mode === 'add');
	}

	private editKind(): EditKind {
		return this.editMode === 'rotate' ? 'rotate' : this.editMode === 'scale' ? 'scale' : 'move';
	}

	private attachProxy(): void {
		if (!this.transform || !this.terrain) return;
		if (this.selectedRow) {
			const row = this.rowAt(this.selectedRow);
			// a server row is a position, not a size: the scale gizmo would lie
			if (!row || this.editMode === 'scale') {
				this.transform.detach();
				return;
			}
			this.proxy.position.set(row.x, row.z, row.y);
			this.proxy.rotation.set(0, row.heading === undefined ? 0 : headingYaw(row.heading), 0);
			this.proxy.scale.setScalar(1);
			this.transform.attach(this.proxy);
			return;
		}
		if (!this.selected) {
			this.transform.detach();
			return;
		}
		const placement = fixturePlacement(this.terrain, this.selected);
		this.proxy.position.set(...placement.position);
		this.proxy.rotation.set(0, placement.yaw, 0);
		this.proxy.scale.setScalar(placement.scale);
		this.transform.attach(this.proxy);
	}

	/** The gizmo moved: write it back onto the fixture (and its instance) live.
	 * A `Ground = 1` row follows the terrain — the client discards its Z
	 * (W-3), so the gizmo must not pretend otherwise. */
	private dragApply(): void {
		if (!this.terrain) return;
		if (this.selectedRow) {
			this.dragRow(this.selectedRow);
			return;
		}
		if (!this.selected) return;
		const fixture = this.selected;
		fixture.x = this.proxy.position.x;
		fixture.y = this.proxy.position.z;
		fixture.z = this.proxy.position.y;
		if (this.editMode === 'rotate') fixture.angle = readAngle(this.proxy.rotation.y);
		if (this.editMode === 'scale') fixture.scale = Math.round(this.proxy.scale.x * 100);
		if (fixture.ground || this.editMode === 'scale') {
			this.proxy.position.y = fixtureZ(this.terrain, fixture);
			fixture.z = this.proxy.position.y;
		}
		this.previewRows(fixture);
		this.updateFixture(fixture);
	}

	private beginDrag(): void {
		this.dragFrom = null;
		this.dragRows = [];
		if (!this.selected || !this.terrain) return;
		const fixture = this.selected;
		this.dragFrom = {
			x: fixture.x,
			y: fixture.y,
			z: fixturePlacement(this.terrain, fixture).position[1],
			angle: fixture.angle
		};
		for (const proposal of this.carried) {
			const row = this.rowAt({ table: proposal.table, id: proposal.key });
			if (row) {
				this.dragRows.push({
					table: proposal.table,
					row,
					from: { x: row.x, y: row.y, z: row.z, heading: row.heading }
				});
			}
		}
	}

	/** The rows a fixture move carries, re-projected live through the fixture's
	 * frame — `carryRow` is the daemon's own arithmetic (`_move_with_fixture`),
	 * so the drag shows what the server will hold: a row keeps its height
	 * relative to the fixture, and a rotation turns it about the fixture,
	 * heading included. */
	private previewRows(fixture: WorldFixture): void {
		const from = this.dragFrom;
		if (!from || !this.dragRows.length || !this.terrain) return;
		const after: CarryFrame = {
			x: fixture.x,
			y: fixture.y,
			z: fixturePlacement(this.terrain, fixture).position[1],
			angle: fixture.angle
		};
		for (const entry of this.dragRows) {
			const moved = carryRow(from, after, entry.from);
			entry.row.x = moved.x;
			entry.row.y = moved.y;
			entry.row.z = moved.z;
			if (moved.heading !== undefined) entry.row.heading = moved.heading;
			this.updateRow({ table: entry.table, id: entry.row.id });
		}
	}

	/** A server row moved by the gizmo — or by a ground click. The row keeps
	 * its height above the terrain (the daemon's own `row-move` rule), so what
	 * the user drags is what the server will hold. */
	private dragRow(pick: RowPick, point?: { x: number; y: number }): void {
		const row = this.rowAt(pick);
		if (!row || !this.terrain) return;
		if (point) {
			row.x = point.x;
			row.y = point.y;
		} else {
			row.x = this.proxy.position.x;
			row.y = this.proxy.position.z;
			if (this.editMode === 'rotate' && row.heading !== undefined) {
				row.heading =
					Math.round((readAngle(this.proxy.rotation.y) * HEADING_UNITS) / 360) % HEADING_UNITS;
			}
		}
		row.z = this.terrain.heightAt(row.x, row.y) + this.rowOffset;
		this.proxy.position.set(row.x, row.z, row.y);
		this.updateRow(pick);
	}

	/** Drop a row at a zone-local point (the click-to-place path). */
	moveRow(pick: RowPick, point: { x: number; y: number }): void {
		this.dragRow(pick, point);
	}

	/** Write one row back into its cloud (the drag's live preview). Found by
	 * id, never by object identity: the page's `$state` proxies and the
	 * payload's plain rows are two views of the same row. */
	updateRow(pick: RowPick): void {
		const entry = this.rowClouds.find((cloud) => cloud.table === pick.table);
		const index = entry ? entry.rows.findIndex((row) => row.id === pick.id) : -1;
		if (!entry || index < 0) return;
		const row = entry.rows[index];
		const attribute = entry.cloud.geometry.getAttribute('position') as THREE.BufferAttribute;
		attribute.setXYZ(index, row.x, row.z, row.y);
		attribute.needsUpdate = true;
		if (
			this.selectedRow &&
			this.selectedRow.table === pick.table &&
			this.selectedRow.id === pick.id
		) {
			this.marker.position.set(row.x, row.z, row.y);
		}
	}

	private rowAt(pick: RowPick): SpawnRow | undefined {
		return this.rowClouds
			.find((cloud) => cloud.table === pick.table)
			?.rows.find((row) => row.id === pick.id);
	}

	/** Recompose one fixture's instance from its fields (move/rotate/scale).
	 * Found by id, never by object identity: the page's `$state` proxies and
	 * the payload's plain objects are two views of the same row. */
	updateFixture(fixture: WorldFixture): void {
		if (!this.terrain) return;
		const placement = fixturePlacement(this.terrain, fixture);
		const matrix = new THREE.Matrix4();
		for (const group of this.picks) {
			const index = group.fixtures.findIndex((row) => row.id === fixture.id);
			if (index < 0) continue;
			matrix.compose(
				new THREE.Vector3(...placement.position),
				new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
				new THREE.Vector3(placement.scale, placement.scale, placement.scale)
			);
			matrix.multiply(group.local);
			group.mesh.setMatrixAt(index, matrix);
			group.mesh.instanceMatrix.needsUpdate = true;
		}
		if (this.selected && this.selected.id === fixture.id) this.select(fixture);
		if (!this.dragging) this.attachProxy();
	}

	/** The ground under a pointer, zone-local (the add-mode click). */
	pickGround(ndcX: number, ndcY: number): { x: number; y: number; z: number } | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
		const hits = ray.intersectObjects(this.groups.ground.children, false);
		if (!hits.length) return null;
		const point = hits[0].point;
		return { x: point.x, y: point.z, z: point.y };
	}

	private showGrid(on: boolean): void {
		if (on && !this.grid) {
			this.grid = new THREE.GridHelper(ZONE_SIZE, 32, 0x2dd4a7, 0x2a4a42);
			const material = this.grid.material as THREE.LineBasicMaterial;
			material.transparent = true;
			material.opacity = 0.35;
			material.depthWrite = false;
			this.grid.position.set(ZONE_SIZE / 2, 0, ZONE_SIZE / 2);
			this.scene.add(this.grid);
		}
		if (this.grid) this.grid.visible = on;
	}

	/** The fixtures group, rebuilt (an add or a removal changed the row set).
	 * Meshes stay cached; only the instancing is redone. */
	async rebuildFixtures(zoneId: number, zone: WorldZone): Promise<void> {
		if (!this.terrain) return;
		this.clearFixtures();
		await this.buildFixtures(zoneId, zone, this.terrain, this.token);
	}

	private clearFixtures(): void {
		this.picks = [];
		for (const child of [...this.groups.fixtures.children]) {
			this.groups.fixtures.remove(child);
			const mesh = child as THREE.Mesh;
			mesh.geometry?.dispose?.();
			const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
			if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
			else material?.dispose?.();
		}
	}

	private buildMarker(): void {
		const ring = new THREE.Mesh(
			new THREE.RingGeometry(150, 210, 40),
			new THREE.MeshBasicMaterial({
				color: 0x67e8f9,
				side: THREE.DoubleSide,
				transparent: true,
				opacity: 0.9,
				depthTest: false
			})
		);
		ring.rotation.x = -Math.PI / 2;
		ring.position.y = 12;
		const pin = new THREE.Mesh(
			new THREE.ConeGeometry(70, 280, 12),
			new THREE.MeshBasicMaterial({ color: 0x67e8f9, depthTest: false })
		);
		pin.position.y = 1000;
		pin.rotation.x = Math.PI; // point down, at the object
		this.marker.add(ring, pin);
	}

	setToggles(toggles: WorldToggles): void {
		this.groups.fixtures.visible = toggles.fixtures;
		this.groups.ground.visible = toggles.ground;
		this.groups.spawns.visible = toggles.spawns;
		this.groups.statics.visible = toggles.statics;
		this.groups.doors.visible = toggles.doors;
		this.groups.patrols.visible = toggles.patrols;
		this.groups.bounds.visible = toggles.bounds;
		this.groups.water.visible = toggles.water;
		this.groups.jumps.visible = toggles.jumps;
	}

	/** What the scene currently holds — the diagnostic hook the screenshots read. */
	stats(): Record<string, number | string> {
		const instances = this.groups.fixtures.children.reduce(
			(sum, child) => sum + ((child as THREE.InstancedMesh).count ?? 0),
			0
		);
		return {
			ground: this.groups.ground.children.length,
			fixtureGroups: this.groups.fixtures.children.length,
			fixtureInstances: instances,
			mobs: this.spawnCounts.mobs,
			statics: this.spawnCounts.statics,
			doors: this.spawnCounts.doors,
			patrols: this.spawnCounts.patrols,
			patrolPoints: this.spawnCounts.patrolPoints,
			bounds: this.groups.bounds.children.length,
			water: this.groups.water.children.length,
			jumps: this.groups.jumps.children.length,
			sheets: this.texturedPatches,
			composed: this.composedPatches
		};
	}

	// -- selection and focus (the map-editor ergonomics, 6.3's first slice) -----

	/** The fixture under a click, in normalised device coordinates. */
	pick(ndcX: number, ndcY: number): Picked | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
		const hits = ray.intersectObjects(this.groups.fixtures.children, false);
		for (const hit of hits) {
			const group = this.picks.find((entry) => entry.mesh === hit.object);
			if (!group || hit.instanceId === undefined) continue;
			const fixture = group.fixtures[hit.instanceId];
			if (fixture) return { fixture, point: hit.point.clone() };
		}
		return null;
	}

	/** The server row under a click. A point cloud has no surface, so the
	 * raycaster matches within a radius — each cloud is probed with its own dot
	 * size, a door being easier to hit than a mob exactly as it is drawn. */
	pickRow(ndcX: number, ndcY: number): RowPick | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
		let best: { distance: number; pick: RowPick } | null = null;
		for (const entry of this.rowClouds) {
			if (!entry.cloud.visible || !entry.group.visible) continue;
			ray.params.Points.threshold = entry.size * 2.5;
			const hits = ray.intersectObject(entry.cloud, false);
			const row = hits.length ? entry.rows[hits[0].index ?? -1] : undefined;
			if (!row) continue;
			if (!best || hits[0].distance < best.distance) {
				best = { distance: hits[0].distance, pick: { table: entry.table, id: row.id } };
			}
		}
		return best?.pick ?? null;
	}

	/** Mark a fixture: a ring at its feet and a pin above it, so a small
	 * object stays findable from a zone-wide camera. */
	select(fixture: WorldFixture | null): void {
		this.selected = fixture;
		this.selectedRow = null;
		if (this.carriedFor !== (fixture?.id ?? null)) {
			// The pairs are proposed against the rows as they stand now: a fixture
			// that has just been dragged has left its doors behind, and those are
			// exactly the rows the next drag carries.
			this.carried = fixture ? this.proposalsFor(fixture.id) : [];
			this.carriedFor = fixture ? fixture.id : null;
		}
		if (!fixture || !this.terrain) {
			this.marker.visible = false;
			this.transform?.detach();
			return;
		}
		const placement = fixturePlacement(this.terrain, fixture);
		this.marker.position.set(...placement.position);
		this.marker.visible = true;
		this.attachProxy();
	}

	/** Mark a server row, the way a fixture is marked (same ring and pin). */
	selectRow(pick: RowPick | null): void {
		const row = pick ? this.rowAt(pick) : undefined;
		const terrain = this.terrain;
		if (pick && (!row || !terrain)) return;
		this.selectedRow = pick;
		if (pick && row && terrain) {
			this.selected = null;
			this.rowOffset = row.z - terrain.heightAt(row.x, row.y);
			this.marker.position.set(row.x, row.z, row.y);
			this.marker.visible = true;
		} else if (!this.selected) {
			this.marker.visible = false;
		}
		this.attachProxy();
	}

	/** The server rows a fixture would carry if it moved now (B2): the pairs
	 * the page sends with the op, and the ones the daemon validates live. */
	carriedRows(fixtureId: number): RowProposal[] {
		if (this.carriedFor === fixtureId) return this.carried;
		return this.proposalsFor(fixtureId);
	}

	/** A fixture's own rows: every row that stands *inside one of its meshes'
	 * boxes*, measured in the fixture's frame. A proposal, not a measurement of
	 * the walls — the boxes are the glTF's, so a row inside a courtyard is
	 * carried and one beyond its walls is not; the daemon checks every key
	 * against the database before it writes. */
	private proposalsFor(fixtureId: number): RowProposal[] {
		if (!this.terrain) return [];
		const found = new Map<string, RowProposal>();
		const matrix = new THREE.Matrix4();
		const inverse = new THREE.Matrix4();
		const box = new THREE.Box3();
		const size = new THREE.Vector3();
		const point = new THREE.Vector3();
		for (const group of this.picks) {
			const index = group.fixtures.findIndex((row) => row.id === fixtureId);
			if (index < 0) continue;
			const fixture = group.fixtures[index];
			const placement = fixturePlacement(this.terrain, fixture);
			matrix.compose(
				new THREE.Vector3(...placement.position),
				new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
				new THREE.Vector3(placement.scale, placement.scale, placement.scale)
			);
			matrix.multiply(group.local);
			inverse.copy(matrix).invert();
			const geometry = group.mesh.geometry;
			if (!geometry.boundingBox) geometry.computeBoundingBox();
			if (!geometry.boundingBox) continue;
			box.copy(geometry.boundingBox);
			// A small margin: a row sits at the terrain height under itself, the
			// mesh's box is the modeller's own — a wall's threshold must count.
			box.expandByScalar(Math.max(32, box.getSize(size).length() * 0.02));
			for (const cloud of this.rowClouds) {
				for (const row of cloud.rows) {
					point.set(row.x, row.z, row.y).applyMatrix4(inverse);
					if (!box.containsPoint(point)) continue;
					const distance = Math.hypot(row.x - placement.position[0], row.y - placement.position[2]);
					const key = `${cloud.table}:${row.id}`;
					const seen = found.get(key);
					if (!seen || distance < seen.distance) {
						found.set(key, { table: cloud.table, key: row.id, name: row.name ?? '', distance });
					}
				}
			}
		}
		return [...found.values()].sort((a, b) => a.distance - b.distance);
	}

	get selection(): WorldFixture | null {
		return this.selected;
	}

	/** Recentre the camera on a fixture (Unity's F): close, slightly above. */
	focusFixture(fixture: WorldFixture, distance = 1400): void {
		if (!this.terrain) return;
		const placement = fixturePlacement(this.terrain, fixture);
		const target = new THREE.Vector3(...placement.position);
		this.controls.target.copy(target);
		this.camera.position.set(
			target.x + distance * 0.8,
			target.y + distance * 0.55,
			target.z + distance
		);
		this.controls.update();
	}

	resize(): void {
		const parent = this.canvas.parentElement;
		if (!parent) return;
		const width = parent.clientWidth || 1;
		const height = parent.clientHeight || 1;
		this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
		this.renderer.setSize(width, height, false);
		this.camera.aspect = width / height;
		this.camera.updateProjectionMatrix();
	}

	start(): void {
		const loop = () => {
			this.frame = requestAnimationFrame(loop);
			this.controls.update();
			this.renderer.render(this.scene, this.camera);
		};
		loop();
	}

	dispose(): void {
		cancelAnimationFrame(this.frame);
		this.clear();
		this.transform?.dispose();
		this.grid?.geometry.dispose();
		this.controls.dispose();
		this.renderer.dispose();
	}

	private clear(): void {
		this.picks = [];
		this.rowClouds = [];
		this.selected = null;
		this.selectedRow = null;
		this.carried = [];
		this.carriedFor = null;
		this.dragFrom = null;
		this.dragRows = [];
		this.marker.visible = false;
		this.transform?.detach();
		this.dragging = false;
		this.texturedPatches = 0;
		this.composedPatches = 0;
		this.spawnCounts = { mobs: 0, statics: 0, doors: 0, patrols: 0, patrolPoints: 0 };
		for (const texture of this.groundTextures.values()) texture.dispose();
		this.groundTextures.clear();
		for (const group of Object.values(this.groups)) {
			for (const child of [...group.children]) {
				group.remove(child);
				const mesh = child as THREE.Mesh;
				mesh.geometry?.dispose?.();
				const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
				if (Array.isArray(material)) material.forEach((m) => m.dispose());
				else material?.dispose?.();
			}
		}
	}

	/** Load one zone; a second call supersedes the first mid-flight. */
	async show(zoneId: number, zone: WorldZone): Promise<void> {
		const token = ++this.token;
		this.clear();
		this.meshCache.clear();
		const response = await fetch(api.worldTerrainUrl(zoneId));
		if (!response.ok) throw new Error(`terrain fetch failed (${response.status})`);
		const data = parseTerrain(await response.arrayBuffer());
		if (token !== this.token) return;
		this.terrain = data;
		this.buildGround(zoneId, zone, data, token);
		await this.buildFixtures(zoneId, zone, data, token);
		if (token !== this.token) return;
		this.buildOverlays(zone, data);
		this.frameZone(data);
	}

	/** The server's rows for the zone just loaded (spec §11 B1): mobs, statics,
	 * doors and patrols, in the same zone-local space as the fixtures. Drawn as
	 * points — a mob row is a position the server owns, not an actor with a
	 * pose — and a patrol as its whole line (the daemon keeps it unclipped).
	 * Call after `show`, which clears the groups; a second call replaces the
	 * clouds, so a re-read after an edit shows the journal's own patches. */
	showSpawns(spawns: WorldSpawns): void {
		this.clearSpawns();
		this.spawnCounts.mobs = spawns.mobs.length;
		this.spawnCounts.statics = spawns.objects.length;
		this.spawnCounts.doors = spawns.doors.length;
		this.addRows('mob', 'spawns', spawns.mobs, 0xe0b23e, 90);
		this.addRows('worldobject', 'statics', spawns.objects, 0x64b5f6, 150);
		this.addRows('door', 'doors', spawns.doors, 0xef5350, 220);
		for (const path of spawns.paths) {
			if (path.points.length < 2) continue;
			const line = new THREE.Line(
				new THREE.BufferGeometry().setFromPoints(
					path.points.map((point) => new THREE.Vector3(point.x, point.z, point.y))
				),
				new THREE.LineBasicMaterial({ color: 0x8fd07a })
			);
			line.name = `patrol-${path.name}`;
			line.frustumCulled = false;
			this.groups.patrols.add(line);
			this.spawnCounts.patrols += 1;
			this.spawnCounts.patrolPoints += path.points.length;
		}
	}

	private addRows(
		table: SpawnTable,
		group: 'spawns' | 'statics' | 'doors',
		rows: SpawnRow[],
		color: number,
		size: number
	): void {
		const cloud = this.points(rows, color, size);
		if (!cloud) return;
		this.groups[group].add(cloud);
		this.rowClouds.push({ table, group: this.groups[group], rows, cloud, size });
	}

	private clearSpawns(): void {
		this.rowClouds = [];
		this.carried = [];
		this.carriedFor = null;
		this.dragFrom = null;
		this.dragRows = [];
		this.spawnCounts = { mobs: 0, statics: 0, doors: 0, patrols: 0, patrolPoints: 0 };
		for (const key of ['spawns', 'statics', 'doors', 'patrols'] as const) {
			const group = this.groups[key];
			for (const child of [...group.children]) {
				group.remove(child);
				const cloud = child as THREE.Points;
				cloud.geometry?.dispose?.();
				(cloud.material as THREE.Material | undefined)?.dispose?.();
			}
		}
	}

	private points(
		rows: Array<{ x: number; y: number; z: number }>,
		color: number,
		size: number
	): THREE.Points | null {
		if (!rows.length) return null;
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute(
			'position',
			new THREE.Float32BufferAttribute(
				rows.flatMap((row) => [row.x, row.z, row.y]),
				3
			)
		);
		const cloud = new THREE.Points(
			geometry,
			new THREE.PointsMaterial({
				color,
				size,
				sizeAttenuation: true,
				map: this.dot,
				transparent: true,
				alphaTest: 0.1,
				depthWrite: false
			})
		);
		cloud.frustumCulled = false; // the points span the zone
		return cloud;
	}

	private buildGround(zoneId: number, zone: WorldZone, data: TerrainData, token: number): void {
		const entries = new Set((zone.archives.tex?.entries ?? []).map((entry) => entry.toLowerCase()));
		// One normal field for the whole zone: the patches then share their
		// boundary normals and the grid does not show as 64 shaded tiles.
		const normals = zoneNormals(data);
		const patches = zone.patches;
		const layersByPatch = new Map<string, WorldTextureLayer[]>();
		for (const layer of patches?.layers ?? []) {
			const key = `${layer.patch_x},${layer.patch_y}`;
			const list = layersByPatch.get(key);
			if (list) list.push(layer);
			else layersByPatch.set(key, [layer]);
		}
		for (let py = 0; py < PATCH_CELLS; py++) {
			for (let px = 0; px < PATCH_CELLS; px++) {
				const patch = patchGeometry(data, px, py, normals);
				const geometry = new THREE.BufferGeometry();
				geometry.setAttribute('position', new THREE.BufferAttribute(patch.positions, 3));
				geometry.setAttribute('normal', new THREE.BufferAttribute(patch.normals, 3));
				geometry.setAttribute('uv', new THREE.BufferAttribute(patch.uvs, 2));
				geometry.setIndex(new THREE.BufferAttribute(patch.indices, 1));
				const material = new THREE.MeshLambertMaterial({ color: 0x66705a });
				const mesh = new THREE.Mesh(geometry, material);
				mesh.name = `patch-${px}-${py}`;
				this.groups.ground.add(mesh);
				// The client's own ground: its layer stack (up to 9 layers over the
				// masks) when the zone ships every file those layers name, the
				// pre-baked `texXX-YY` sheet (the same composition, 512²) else.
				const slots = groundSlots(
					layersByPatch.get(`${px},${py}`) ?? [],
					patches?.masks?.[`${px},${py}`] ?? []
				);
				const shippable =
					slots !== null && slots.every((slot) => (patches?.available ?? []).includes(slot.sheet));
				if (shippable) {
					void this.composePatch(zoneId, mesh, slots, [px, py], token);
					continue;
				}
				const entry = `tex${pad(px)}-${pad(py)}.dds`;
				if (!entries.has(entry)) continue;
				new THREE.TextureLoader()
					.loadAsync(api.worldTextureUrl(zoneId, 'tex', entry))
					.then((texture) => {
						if (token !== this.token) {
							texture.dispose();
							return;
						}
						texture.colorSpace = THREE.SRGBColorSpace;
						texture.wrapS = THREE.ClampToEdgeWrapping;
						texture.wrapT = THREE.ClampToEdgeWrapping;
						texture.anisotropy = this.anisotropy();
						material.map = texture;
						material.color.set(0xffffff);
						material.needsUpdate = true;
						this.texturedPatches += 1;
					})
					.catch((error) => {
						console.warn(`world: ground sheet ${entry} failed`, error);
					});
			}
		}
	}

	/** The renderer's anisotropic filtering ceiling: the ground is the one
	 * surface seen at grazing angles for kilometres — low anisotropy is what
	 * made the far field read as a blur (the layer composition fixes the near
	 * field). */
	private anisotropy(): number {
		return Math.min(16, this.renderer.capabilities.getMaxAnisotropy());
	}

	/** One ground texture, loaded once and shared by every patch that names it. */
	private async groundTexture(
		url: string,
		flipY: boolean,
		wrap: THREE.Wrapping,
		token: number
	): Promise<THREE.Texture | null> {
		const cached = this.groundTextures.get(url);
		if (cached) return cached;
		try {
			const texture = await new THREE.TextureLoader().loadAsync(url);
			if (token !== this.token) {
				texture.dispose();
				return null;
			}
			texture.colorSpace = THREE.SRGBColorSpace;
			texture.wrapS = wrap;
			texture.wrapT = wrap;
			texture.anisotropy = this.anisotropy();
			// The layer sheets are read bottom-up (their row 0 is the tile's
			// south edge — see ground.ts); the masks are north-up images. A mask
			// covers its patch exactly, so it clamps: repeating it would blend a
			// patch's edge weights with the opposite edge, and every patch
			// boundary would show as a seam.
			texture.flipY = flipY;
			texture.needsUpdate = true;
			this.groundTextures.set(url, texture);
			return texture;
		} catch (error) {
			console.warn(`world: ground texture ${url} failed`, error);
			return null;
		}
	}

	/** The patch's material, once its sheets and masks are loaded. */
	private async composePatch(
		zoneId: number,
		mesh: THREE.Mesh,
		slots: GroundSlot[],
		patch: [number, number],
		token: number
	): Promise<void> {
		const wanted = new Map<string, { url: string; flipY: boolean; wrap: THREE.Wrapping }>();
		for (const slot of slots) {
			wanted.set(`sheet:${slot.sheet}`, {
				url: api.worldTerrainTexUrl(`${slot.sheet}.dds`),
				flipY: false,
				wrap: THREE.RepeatWrapping
			});
			wanted.set(`mask:${slot.mask}`, {
				url: api.worldTextureUrl(zoneId, 'ter', slot.mask),
				flipY: true,
				wrap: THREE.ClampToEdgeWrapping
			});
		}
		const loaded = new Map<string, THREE.Texture>();
		for (const [key, source] of wanted) {
			const texture = await this.groundTexture(source.url, source.flipY, source.wrap, token);
			if (!texture) return;
			loaded.set(key, texture);
		}
		if (token !== this.token) return;
		const material = groundMaterial(
			slots,
			{
				sheet: (slot) => loaded.get(`sheet:${slot.sheet}`) ?? null,
				mask: (name) => loaded.get(`mask:${name}`) ?? null
			},
			patch
		);
		if (!material) return;
		const previous = mesh.material as THREE.Material;
		mesh.material = material;
		previous.dispose();
		this.composedPatches += 1;
	}

	private async buildFixtures(
		zoneId: number,
		zone: WorldZone,
		data: TerrainData,
		token: number
	): Promise<void> {
		const byCatalog = new Map<number, WorldFixture[]>();
		for (const fixture of zone.fixtures) {
			if (!fixture.nif_id) continue;
			const list = byCatalog.get(fixture.nif_id);
			if (list) list.push(fixture);
			else byCatalog.set(fixture.nif_id, [fixture]);
		}
		for (const entry of zone.catalog) {
			const fixtures = byCatalog.get(entry.id);
			if (!entry.mesh || !fixtures?.length) continue;
			let loaded: LoadedMesh | undefined;
			try {
				loaded = await this.loadMesh(zoneId, entry.filename);
			} catch (error) {
				console.warn(`world: mesh ${entry.filename} failed`, error);
				continue; // a mesh the client cannot export: skip, the rest stands
			}
			if (token !== this.token) return;
			if (!loaded) continue;
			this.instance(loaded.model, fixtures, data);
		}
	}

	private async loadMesh(zoneId: number, filename: string): Promise<LoadedMesh | undefined> {
		const url = api.worldMeshUrl(zoneId, filename);
		const cached = this.meshCache.get(url);
		if (cached) return cached;
		const response = await fetch(url);
		if (!response.ok) return undefined;
		const model = await loadFromGlb(await response.arrayBuffer(), filename);
		const loaded = { model };
		this.meshCache.set(url, loaded);
		return loaded;
	}

	private instance(model: LoadedMesh['model'], fixtures: WorldFixture[], data: TerrainData): void {
		model.scene.updateMatrixWorld(true);
		const inner: THREE.Mesh[] = [];
		model.scene.traverse((object) => {
			if ((object as THREE.Mesh).isMesh) inner.push(object as THREE.Mesh);
		});
		for (const mesh of inner) {
			const material = this.materialFor(mesh, model);
			const instanced = new THREE.InstancedMesh(mesh.geometry, material, fixtures.length);
			const matrix = new THREE.Matrix4();
			const local = mesh.matrixWorld.clone();
			for (let i = 0; i < fixtures.length; i++) {
				const placement = fixturePlacement(data, fixtures[i]);
				matrix.compose(
					new THREE.Vector3(...placement.position),
					new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
					new THREE.Vector3(placement.scale, placement.scale, placement.scale)
				);
				matrix.multiply(local);
				instanced.setMatrixAt(i, matrix);
			}
			instanced.instanceMatrix.needsUpdate = true;
			instanced.frustumCulled = false; // the instances span the zone
			instanced.name = `fixtures-${mesh.name || 'mesh'}`;
			this.groups.fixtures.add(instanced);
			this.picks.push({ mesh: instanced, fixtures, local });
		}
	}

	private materialFor(mesh: THREE.Mesh, model: LoadedMesh['model']): THREE.Material {
		const found = model.meshTextures.get(mesh);
		const material = (mesh.material as THREE.MeshStandardMaterial).clone();
		material.metalness = 0.05;
		material.roughness = 0.9;
		material.side = THREE.DoubleSide;
		if (found) {
			material.map = textureFromDds(found.bytes);
			material.color = tintColor(found.tint);
			if (found.doubleSided !== undefined) material.side = textureSide(found.doubleSided);
			if (found.alpha) material.alphaTest = 0.5;
		}
		material.needsUpdate = true;
		return material;
	}

	private buildOverlays(zone: WorldZone, data: TerrainData): void {
		for (const bound of zone.bounds) {
			if (bound.points.length < 2) continue;
			const points = bound.points.map(
				([x, y]) => new THREE.Vector3(x, data.heightAt(x, y) + OVERLAY_LIFT, y)
			);
			const geometry = new THREE.BufferGeometry().setFromPoints(points);
			const line = new THREE.LineLoop(geometry, new THREE.LineBasicMaterial({ color: 0xffd166 }));
			this.groups.bounds.add(line);
		}
		for (const river of zone.water) {
			const pairs = Math.min(river.left.length, river.right.length);
			if (pairs < 2) continue;
			const positions: number[] = [];
			for (let i = 0; i < pairs - 1; i++) {
				const [lx0, ly0] = river.left[i];
				const [rx0, ry0] = river.right[i];
				const [lx1, ly1] = river.left[i + 1];
				const [rx1, ry1] = river.right[i + 1];
				const quad = [
					[lx0, ly0],
					[rx0, ry0],
					[rx1, ry1],
					[lx0, ly0],
					[rx1, ry1],
					[lx1, ly1]
				];
				for (const [x, y] of quad) positions.push(x, river.height + OVERLAY_LIFT, y);
			}
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
			geometry.computeVertexNormals();
			const mesh = new THREE.Mesh(
				geometry,
				new THREE.MeshLambertMaterial({
					color: river.kind.toUpperCase() === 'LAVA' ? 0xc1440e : 0x2f6f9f,
					transparent: true,
					opacity: 0.6,
					side: THREE.DoubleSide
				})
			);
			mesh.name = `water-${river.name}`;
			this.groups.water.add(mesh);
		}
		for (const jump of zone.jumps) {
			const geometry = new THREE.ConeGeometry(ZONE_UNIT / 64, ZONE_UNIT / 16, 12);
			const marker = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x67e8f9 }));
			marker.position.set(jump.x, jump.z + ZONE_UNIT / 32, jump.y);
			marker.name = `jump-${jump.name}`;
			this.groups.jumps.add(marker);
		}
	}

	/** Move the camera to a zone-local point (the screenshot harness). */
	focus(x: number, y: number, distance = ZONE_UNIT * 1.5): void {
		const z = this.terrain ? this.terrain.heightAt(x, y) : 0;
		this.controls.target.set(x, z, y);
		this.camera.position.set(x + distance * 0.7, z + distance * 0.55, y + distance);
		this.controls.update();
	}

	/** Stand on a circle around a zone-local point and look at it: azimuth 0 is
	 * the scene's +Z — the client's *south* (its +y is scene +Z) — 90 is east,
	 * 180 north. The screenshot harness uses it to see a structure from a named
	 * side; north stands on a north-up view, framing it as the game does. */
	lookFrom(x: number, y: number, azimuthDeg: number, distance = ZONE_UNIT * 0.45): void {
		if (!this.terrain) return;
		const z = this.terrain.heightAt(x, y);
		const radians = (azimuthDeg * Math.PI) / 180;
		this.controls.target.set(x, z, y);
		this.camera.position.set(
			x + Math.sin(radians) * distance,
			z + distance * 0.4,
			y + Math.cos(radians) * distance
		);
		this.controls.update();
	}

	private frameZone(data: TerrainData): void {
		let minZ = Infinity;
		let maxZ = -Infinity;
		for (let i = 0; i <= 16; i++) {
			for (let j = 0; j <= 16; j++) {
				const z = data.heightAt((i * ZONE_SIZE) / 16, (j * ZONE_SIZE) / 16);
				minZ = Math.min(minZ, z);
				maxZ = Math.max(maxZ, z);
			}
		}
		const mid = (minZ + maxZ) / 2;
		// North up, east right: the camera stands south of the zone centre
		// (scene +Z is the client's south) and looks north.
		const center = new THREE.Vector3(ZONE_SIZE / 2, mid, ZONE_SIZE / 2);
		this.controls.target.copy(center);
		this.camera.position.set(center.x, mid + ZONE_SIZE * 0.55, center.z + ZONE_SIZE * 0.75);
		this.controls.update();
	}
}
