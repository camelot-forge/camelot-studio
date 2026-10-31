// The `/world` scene: the client's terrain, its fixtures, and the overlays.
//
// Everything here consumes the daemon's artefacts (`/api/v1/world/…`): the
// CAMT terrain planes, glTF meshes with the sheets the NIF itself names, and
// the DDS→PNG ground sheets. Nothing parses a client file in the browser.

import * as THREE from 'three';
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
	parseTerrain,
	patchGeometry,
	zoneNormals,
	type TerrainData
} from './terrain';
import { GIZMO_AXES, axisParam, quantized, type GizmoAxis } from './gizmo';

/** Bounds/water/jumps sit a hair above the ground so they never z-fight it. */
const OVERLAY_LIFT = 8;

/** The client's fixed city and dungeon frames (`FUN_00549436` and
 * `FUN_0054c039`, game1127.dll): one root at `8192 * 4 - 768` (32000) on both
 * plane axes, with a 180-degree yaw about up (`FUN_00675ca0` with the global pi
 * at `0x009675e8`). Only the height differs: 8000 for a city, 16000 for a
 * dungeon. The client stores those as -8000/-16000; the viewer's Y-up frame
 * uses the positive values its server rows do. The server's rows are
 * zone-local, hence the zone-rect subtraction. */
const CLIENT_ORIGIN = 8192 * 4 - 768;
const CITY_HEIGHT = 8000;
const DUNGEON_HEIGHT = 16000;
const CLIENT_YAW = Math.PI;

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
	models: boolean;
	labels: boolean;
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

export type EditMode = 'select' | 'move' | 'rotate' | 'scale' | 'add' | 'measure';
export type EditKind = 'move' | 'rotate' | 'scale' | 'height';

/** A measurement: two points on the ground, and what lies between them. */
export interface Measure {
	a: { x: number; y: number; z: number };
	b: { x: number; y: number; z: number };
	distance: number;
	dx: number;
	dy: number;
	dz: number;
}

/** Snap steps: the zone's own subtile (65536 / 256, measured from the
 * terrain grid), 15 degrees and 5 percent — editor conveniences, named. */
const SNAP_POSITION = 256;
const SNAP_ANGLE = 15;
const SNAP_SCALE = 5;
/** Heights get a finer step than the ground plane: a door sits 30 units above
 * its threshold, and 256 would be a storey. */
const SNAP_HEIGHT = 16;

/** The server's `Heading` is a 12-bit compass (measured): 4096 steps to the
 * turn, clockwise from north — the same way `fixtures.csv`'s `A` runs. */
const HEADING_UNITS = 4096;

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

/** What is selected: the fixtures and the server rows, together — a marquee
 * and a click both speak this language, and a drag moves all of it. */
export interface Selection {
	fixtures: WorldFixture[];
	rows: RowPick[];
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

/** How far the detailed previews reach, and how many stand at once. A zone
 * holds thousands of rows; the nearest ones get their model, the rest keep
 * their point (the same rule the world editor's reference used). */
const PREVIEW_MIN_RADIUS = 3000;
const PREVIEW_MAX_RADIUS = 16000;
const PREVIEW_LIMIT = 80;
const LABEL_LIMIT = 50;

/** One row drawn as a model or a volume, with the label height its own box
 * reports. The group is a child of the layer's group, so the toggle hides it. */
interface PreviewEntry {
	pick: RowPick;
	table: SpawnTable;
	group: THREE.Group;
	hit: THREE.Mesh;
	top: number;
	model?: number;
	size?: number;
	scale?: number;
}

/** One item a gesture holds, with the fields it started from. Every pointer
 * move recomputes the whole transform *from these origins* and applies it to
 * every item: a group never loses its shape, and no delta ever accumulates. */
interface GrabFixture {
	tag: 'fixture';
	fixture: WorldFixture;
	from: { x: number; y: number; z: number; angle: number; scale: number };
	/** the server rows this fixture carries (B2), with their own origins */
	carried: Array<{
		table: SpawnTable;
		row: SpawnRow;
		from: { x: number; y: number; z: number; heading?: number };
	}>;
}

interface GrabRow {
	tag: 'row';
	table: SpawnTable;
	row: SpawnRow;
	from: { x: number; y: number; z: number; heading?: number };
}

type GrabItem = GrabFixture | GrabRow;

/** A gesture's own parameters, in zone-local units: one transform for the whole
 * selection, computed from the origins on every move, quantized once. */
type Transform =
	| { kind: 'move'; dx: number; dy: number }
	| { kind: 'rotate'; degrees: number; pivot: { x: number; y: number } }
	| { kind: 'scale'; percent: number }
	| { kind: 'height'; dz: number };

/** What a finished gesture hands the page: the values the gesture left — not
 * the objects it left them in. A commit that re-reads a payload the daemon has
 * since replaced would journal the values from *before* the gesture (the rows
 * a move is carrying are re-read the moment the first op lands). */
export interface FixtureEdit {
	id: number;
	x: number;
	y: number;
	z: number;
	angle: number;
	scale: number;
	carried: Array<{ table: SpawnTable; key: string }>;
}

/** One server row a gesture moved, with the values it left. */
export interface RowEdit {
	table: SpawnTable;
	key: string;
	x: number;
	y: number;
	z: number;
	heading?: number;
}

/** A point turned about a pivot by `degrees` of the client's own `A` (measured:
 * degrees clockwise on the map, whose `y` grows south). +90° takes a point due
 * east of the pivot to due south, exactly as the fixture frame does. */
export function rotateAbout(
	x: number,
	y: number,
	pivot: { x: number; y: number },
	degrees: number
): { x: number; y: number } {
	const radians = (degrees * Math.PI) / 180;
	const dx = x - pivot.x;
	const dy = y - pivot.y;
	const cos = Math.cos(radians);
	const sin = Math.sin(radians);
	return { x: pivot.x + dx * cos - dy * sin, y: pivot.y + dx * sin + dy * cos };
}

/** One thing the zone's own checks found (C2). The rule is named in `detail`:
 * a linter that hides its threshold is a fortune teller. */
export interface LintFinding {
	kind: 'outside' | 'buried' | 'floating';
	what: string;
	at: [number, number, number];
	detail: string;
	fixture?: WorldFixture;
	row?: RowPick;
}

/** The linter's own thresholds, in the zone's units. */
const LINT_BURIED = 16;
const LINT_FLOATING = 2000;

/** A gesture in flight. `from`/`at` are screen points — what tells a click from
 * a drag and what drives the vertical gestures; the grab's own frame lives
 * below. One gesture is no other gesture's business. */
interface GestureBase {
	pointerId: number;
	button: number;
	from: { x: number; y: number };
	at: { x: number; y: number };
	moved: boolean;
}

interface Grab extends GestureBase {
	kind: EditKind;
	/** the ground point the gesture grabbed (zone-local; its `z` is the height
	 * of the plane the pointer slides on) */
	anchor: { x: number; y: number; z: number };
	/** the items' own middle: what a rotation turns about */
	pivot: { x: number; y: number };
	/** the pointer's angle about the pivot when the grab began, radians */
	startAngle: number;
	items: GrabItem[];
	/** A gizmo drag: which arrow was grabbed, where the axis stood when the drag
	 * began (it must not move with the preview, or the math chases it), the
	 * pointer's own parameter along that axis at pointer down, and whether the
	 * snap is held off for this move (shift — free placement). */
	axis?: GizmoAxis;
	axisOrigin?: [number, number, number];
	t0?: number;
	free?: boolean;
}

type Gesture =
	| (GestureBase & { kind: 'marquee'; add: boolean })
	| (GestureBase & { kind: 'pan' })
	| (GestureBase & { kind: 'orbit' })
	| Grab;

function isGrab(gesture: Gesture | null): gesture is Grab {
	return (
		gesture !== null &&
		(gesture.kind === 'move' ||
			gesture.kind === 'rotate' ||
			gesture.kind === 'scale' ||
			gesture.kind === 'height')
	);
}

export class WorldViewer {
	private canvas: HTMLCanvasElement;
	private renderer: THREE.WebGLRenderer;
	private scene = new THREE.Scene();
	private camera: THREE.PerspectiveCamera;
	private groups = {
		ground: new THREE.Group(),
		fixtures: new THREE.Group(),
		architecture: new THREE.Group(),
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
	private flatGround = false;
	private framedRows = false;
	private architectureAligned = false;
	private architectureBounds: {
		minX: number;
		minY: number;
		minZ: number;
		maxX: number;
		maxY: number;
		maxZ: number;
	} | null = null;
	private texturedPatches = 0;
	private composedPatches = 0;
	private picks: PickGroup[] = [];
	private rowClouds: RowCloud[] = [];
	/** The nearby rows drawn as client figures or volumes, and the labels. */
	private previews = new Map<string, PreviewEntry>();
	private previewLoading = new Set<string>();
	private creatureCache = new Map<number, Promise<LoadedMesh | undefined>>();
	private previewsVisible = true;
	private labelsVisible = true;
	private labelLayer: HTMLDivElement;
	private labelButtons = new Map<string, HTMLButtonElement>();
	/** The row behind each label, so its screen position can follow the camera
	 * on every frame without rebuilding the set. */
	private labelRows = new Map<string, { table: SpawnTable; row: SpawnRow }>();
	private lastPreview = 0;
	private lastLabels = 0;
	private hitMaterial = new THREE.MeshBasicMaterial({
		transparent: true,
		opacity: 0,
		depthWrite: false
	});

	// -- selection and gestures -------------------------------------------------

	/** What is selected — the fixtures and the server rows together. The viewer
	 * owns it: a click, a marquee and the page's lists all settle it here. */
	private selection: Selection = { fixtures: [], rows: [] };
	/** The rows a selected fixture carries, per fixture, proposed against the
	 * payload as it stood when it was selected: the drag that follows carries
	 * the pairs the user was shown, never a re-proposal of a moved structure. */
	private carried = new Map<number, RowProposal[]>();
	/** The one gesture in flight — a box, the camera, or a grab of the selection.
	 * Decided at pointer down, done at pointer up; nothing else writes the items
	 * while it lives, and each move recomputes the transform from the origins. */
	private gesture: Gesture | null = null;
	private marquee: HTMLDivElement;
	private abort = new AbortController();

	// -- the camera (right-drag orbits, shift+right pans, the wheel zooms) -------

	private target = new THREE.Vector3();
	private orbit = { azimuth: 0.6, elevation: 0.45, distance: 60000 };

	private marker = new THREE.Group();
	private rings = new THREE.Group();
	private measureGroup = new THREE.Group();
	/** The drafts' Unity-style arrows, and the fat colliders under them. */
	private gizmo = new THREE.Group();
	private gizmoColliders: THREE.Mesh[] = [];
	private gizmoTarget: [number, number, number] | null = null;
	/** The two points of the measure tool, and the last pointer readout. */
	private measureA: { x: number; y: number; z: number } | null = null;
	private measureB: { x: number; y: number; z: number } | null = null;
	private hoverAt = 0;
	private ringGeometry = new THREE.RingGeometry(150, 210, 40);
	private ringMaterial = new THREE.MeshBasicMaterial({
		color: 0x67e8f9,
		side: THREE.DoubleSide,
		transparent: true,
		opacity: 0.9,
		depthTest: false
	});
	private rowRingMaterial = new THREE.MeshBasicMaterial({
		color: 0xe0b23e,
		side: THREE.DoubleSide,
		transparent: true,
		opacity: 0.95,
		depthTest: false
	});
	private grid: THREE.GridHelper | null = null;
	private editMode: EditMode = 'select';
	private snap = true;

	/** The page's hooks. All of them are *reports*: the viewer owns the gesture,
	 * the page journals the operations it implies (D2). */
	onEdit: ((kind: EditKind, edits: FixtureEdit[]) => void) | null = null;
	onRowEdit: ((kind: EditKind, rows: RowEdit[]) => void) | null = null;
	onSelect: ((selection: Selection) => void) | null = null;
	onPick: ((point: { x: number; y: number; z: number }) => void) | null = null;
	/** The ground under the pointer, as it moves (the panel's own readout). */
	onHover: ((point: { x: number; y: number; z: number } | null) => void) | null = null;
	/** The measure tool's two points, whenever they change. */
	onMeasure: ((measure: Measure | null) => void) | null = null;
	/** A gizmo drag's snap state as it changes: `true` while shift holds the snap
	 * off, null when no gizmo drag is running (the status bar says it back). */
	onGizmoFree: ((free: boolean | null) => void) | null = null;
	private token = 0;
	private frame = 0;

	constructor(canvas: HTMLCanvasElement) {
		this.canvas = canvas;
		this.dot = dotSprite();
		this.scene.background = new THREE.Color(0x0f1418);
		this.scene.fog = new THREE.Fog(0x0f1418, 60000, 160000);
		this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
		this.camera = new THREE.PerspectiveCamera(50, 1, 10, 400000);
		this.scene.add(
			new THREE.HemisphereLight(0xdfeaff, 0x3a3f35, 1.0),
			new THREE.DirectionalLight(0xffffff, 1.6)
		);
		const sun = this.scene.children[this.scene.children.length - 1] as THREE.DirectionalLight;
		sun.position.set(0.55, 1, 0.35).multiplyScalar(50000);
		for (const group of Object.values(this.groups)) this.scene.add(group);
		this.marker.visible = false;
		this.buildMarker();
		this.buildGizmo();
		this.scene.add(this.marker, this.rings, this.measureGroup, this.gizmo);
		this.marquee = document.createElement('div');
		Object.assign(this.marquee.style, {
			position: 'absolute',
			border: '1px solid #67e8f9',
			background: 'rgba(103, 232, 249, 0.12)',
			pointerEvents: 'none',
			display: 'none',
			zIndex: '5'
		} satisfies Partial<CSSStyleDeclaration>);
		this.labelLayer = document.createElement('div');
		Object.assign(this.labelLayer.style, {
			position: 'absolute',
			inset: '0',
			overflow: 'hidden',
			pointerEvents: 'none',
			zIndex: '4'
		} satisfies Partial<CSSStyleDeclaration>);
		canvas.parentElement?.appendChild(this.labelLayer);
		this.attachInput();
		this.resize();
	}

	// -- input: the whole mouse grammar in one place -----------------------------

	private attachInput(): void {
		const signal = this.abort.signal;
		const canvas = this.canvas;
		canvas.style.touchAction = 'none';
		canvas.style.cursor = 'grab';
		// the page's own menu has no business over the scene: right is the camera.
		// Three layers, because a browser that keeps an old page or retargets the
		// event must not win: the canvas' own property handler, a capture-phase
		// listener on the document, and the *position* decides rather than the
		// event's target.
		const shut = (event: MouseEvent) => event.preventDefault();
		canvas.addEventListener('contextmenu', shut, { signal });
		document.addEventListener(
			'contextmenu',
			(event) => {
				const rect = this.canvas.getBoundingClientRect();
				const inside =
					event.clientX >= rect.left &&
					event.clientX <= rect.right &&
					event.clientY >= rect.top &&
					event.clientY <= rect.bottom;
				if (inside) event.preventDefault();
			},
			{ capture: true, signal }
		);
		canvas.addEventListener('wheel', (event) => this.zoomBy(event), {
			signal,
			passive: false
		});
		canvas.addEventListener('pointerdown', (event) => this.onDown(event), { signal });
		canvas.addEventListener('pointermove', (event) => this.onMove(event), { signal });
		canvas.addEventListener('pointerup', (event) => this.onUp(event), { signal });
		canvas.addEventListener('pointercancel', () => this.endPointer(), { signal });
		canvas.addEventListener('pointerleave', () => this.onHover?.(null), { signal });
		canvas.addEventListener('dblclick', (event) => this.onDoubleClick(event), { signal });
	}

	private ndcOf(event: MouseEvent | PointerEvent): { x: number; y: number } {
		const rect = this.canvas.getBoundingClientRect();
		return {
			x: ((event.clientX - rect.left) / rect.width) * 2 - 1,
			y: -(((event.clientY - rect.top) / rect.height) * 2 - 1)
		};
	}

	/** The ground under the pointer, for the panel's readout — at most every
	 * 40 ms (a raycast per pointer event would be wasted work). */
	private reportHover(event: PointerEvent): void {
		if (!this.onHover) return;
		const now = performance.now();
		if (now - this.hoverAt < 40) return;
		this.hoverAt = now;
		const ndc = this.ndcOf(event);
		this.onHover(this.pickGround(ndc.x, ndc.y));
	}

	// -- the measure tool -------------------------------------------------------

	/** One click of the measure tool: the first point, then the second, then a
	 * fresh first — the gesture every map tool has. */
	private measurePoint(point: { x: number; y: number; z: number }): void {
		if (!this.measureA || this.measureB) {
			this.measureA = point;
			this.measureB = null;
		} else {
			this.measureB = point;
		}
		this.drawMeasure();
		this.onMeasure?.(this.measureView);
	}

	get measureView(): Measure | null {
		const a = this.measureA;
		const b = this.measureB;
		if (!a || !b) return null;
		return {
			a,
			b,
			distance: Math.hypot(b.x - a.x, b.y - a.y),
			dx: b.x - a.x,
			dy: b.y - a.y,
			dz: b.z - a.z
		};
	}

	/** Let the measurement go (Escape, or the panel's own clear). */
	clearMeasure(): void {
		this.measureA = null;
		this.measureB = null;
		this.drawMeasure();
		this.onMeasure?.(null);
	}

	private drawMeasure(): void {
		for (const child of [...this.measureGroup.children]) {
			this.measureGroup.remove(child);
			// the rings share the selection's geometry and material: only the
			// segment's own geometry goes with the child
			if ((child as THREE.Line).isLine) (child as THREE.Line).geometry.dispose();
		}
		for (const point of [this.measureA, this.measureB]) {
			if (!point) continue;
			const ring = new THREE.Mesh(this.ringGeometry, this.ringMaterial);
			ring.rotation.x = -Math.PI / 2;
			ring.position.set(point.x, point.z + 12, point.y);
			this.measureGroup.add(ring);
		}
		if (!this.measureA || !this.measureB) return;
		const line = new THREE.Line(
			new THREE.BufferGeometry().setFromPoints([
				new THREE.Vector3(this.measureA.x, this.measureA.z + 12, this.measureA.y),
				new THREE.Vector3(this.measureB.x, this.measureB.z + 12, this.measureB.y)
			]),
			new THREE.LineBasicMaterial({ color: 0x67e8f9, depthTest: false })
		);
		line.renderOrder = 2;
		this.measureGroup.add(line);
	}

	/** The zone's own checks (C2): what stands outside its bounds, what the
	 * terrain buries, what floats far above it. Every finding names its rule. */
	lint(): LintFinding[] {
		if (!this.terrain) return [];
		const terrain = this.terrain;
		const outside = (x: number, y: number) => x < 0 || y < 0 || x > ZONE_SIZE || y > ZONE_SIZE;
		const findings: LintFinding[] = [];
		for (const group of this.picks) {
			for (const fixture of group.fixtures) {
				if (!fixture.nif_id) continue;
				const at = fixturePlacement(terrain, fixture).position;
				const what = `fixture #${fixture.id}${fixture.name ? ` ${fixture.name}` : ''}`;
				if (outside(fixture.x, fixture.y)) {
					findings.push({
						kind: 'outside',
						what,
						at,
						fixture,
						detail: `outside the zone (${Math.round(fixture.x)}, ${Math.round(fixture.y)})`
					});
				}
				if (fixture.ground) continue; // the client drops a ground row on the terrain (W-3)
				const dz = at[1] - terrain.heightAt(fixture.x, fixture.y);
				if (dz < -LINT_BURIED) {
					findings.push({
						kind: 'buried',
						what,
						at,
						fixture,
						detail: `${Math.round(-dz)} units under the terrain`
					});
				} else if (dz > LINT_FLOATING) {
					findings.push({
						kind: 'floating',
						what,
						at,
						fixture,
						detail: `${Math.round(dz)} units above the terrain`
					});
				}
			}
		}
		for (const cloud of this.rowClouds) {
			for (const row of cloud.rows) {
				const at: [number, number, number] = [row.x, row.z, row.y];
				const what = `${cloud.table} ${row.id.slice(0, 8)}${row.name ? ` ${row.name}` : ''}`;
				const pick = { table: cloud.table, id: row.id };
				if (outside(row.x, row.y)) {
					findings.push({
						kind: 'outside',
						what,
						at,
						row: pick,
						detail: `outside the zone (${Math.round(row.x)}, ${Math.round(row.y)})`
					});
				}
				const dz = row.z - terrain.heightAt(row.x, row.y);
				if (dz < -LINT_BURIED) {
					findings.push({
						kind: 'buried',
						what,
						at,
						row: pick,
						detail: `${Math.round(-dz)} units under the terrain`
					});
				} else if (dz > LINT_FLOATING) {
					findings.push({
						kind: 'floating',
						what,
						at,
						row: pick,
						detail: `${Math.round(dz)} units above the terrain`
					});
				}
			}
		}
		return findings;
	}

	private onDown(event: PointerEvent): void {
		const ndc = this.ndcOf(event);
		// the arrows first — even under shift, which is this gizmo's own "free
		// placement" while the ground keeps its shift+left pan
		if (event.button === 0 && this.editMode === 'move') {
			const axis = this.pickGizmo(ndc);
			if (axis && this.beginGizmo(this.pointerBase(event), axis, ndc)) {
				event.preventDefault();
				this.canvas.setPointerCapture(event.pointerId);
				return;
			}
		}
		// shift+left drags the map in every mode: the camera is never a tool's
		// prisoner (a shift+left *click* still adds to the selection, see `onUp`)
		if (event.button === 0 && event.shiftKey) {
			event.preventDefault();
			this.canvas.setPointerCapture(event.pointerId);
			this.gesture = { ...this.pointerBase(event), kind: 'pan' };
			return;
		}
		if (event.button === 0 && this.editMode === 'add') {
			const point = this.pickGround(ndc.x, ndc.y);
			if (point) this.onPick?.(point);
			return;
		}
		if (event.button === 0 && this.editMode === 'measure') {
			const point = this.pickGround(ndc.x, ndc.y);
			if (point) this.measurePoint(point);
			return;
		}
		// the middle button must never start the browser's autoscroll
		event.preventDefault();
		this.canvas.setPointerCapture(event.pointerId);
		const base = this.pointerBase(event);
		if (event.button === 2) {
			this.gesture = { ...base, kind: event.shiftKey ? 'pan' : 'orbit' };
			return;
		}
		if (event.button === 0 || event.button === 1) {
			const kind = event.button === 1 ? this.dragKindFor(event) : this.leftKind(event);
			if (kind) {
				const under = this.underPointer(ndc);
				if (event.button === 1) {
					// the middle button moves the *selection* — what the user chose, not
					// what the pointer happens to stand on; with nothing selected it takes
					// the item under it, so a lone object is still one drag away
					const target =
						this.selection.fixtures.length || this.selection.rows.length
							? this.selectionView
							: under
								? this.targetFor(under)
								: null;
					if (target && this.beginGrab(base, kind, ndc, target)) return;
				} else if (under) {
					// the left button grabs the *selection* and nothing else: a drag that
					// starts anywhere else is the box every editor's left button draws
					const target = this.targetFor(under);
					if (
						this.sameSelection(target, this.selection) &&
						this.beginGrab(base, kind, ndc, target)
					) {
						return;
					}
				}
			}
		}
		if (event.button === 0) {
			this.gesture = { ...base, kind: 'marquee', add: event.ctrlKey || event.metaKey };
		}
	}

	/** What a left drag applies: the mode, with ctrl and alt as the shortcuts they
	 * were (`ctrl` scales whatever the mode says, `alt` lifts). `select` leaves
	 * the left button to the box alone. */
	private leftKind(event: PointerEvent): EditKind | null {
		if (event.ctrlKey || event.metaKey) return 'scale';
		if (event.altKey) return 'height';
		if (this.editMode === 'move' || this.editMode === 'rotate' || this.editMode === 'scale') {
			return this.editMode;
		}
		return null;
	}

	/** What the middle button applies — the mode's own gestures, plus the three
	 * `A`/`Scale`/lift steps shift, ctrl and alt have always meant. */
	private dragKindFor(event: PointerEvent): EditKind {
		if (event.shiftKey) return 'rotate';
		if (event.ctrlKey || event.metaKey) return 'scale';
		if (event.altKey) return 'height';
		if (this.editMode === 'rotate' || this.editMode === 'scale') return this.editMode;
		return 'move';
	}

	/** The one item the pointer stands on (a fixture wins over a row under it). */
	private underPointer(ndc: {
		x: number;
		y: number;
	}): { fixture: WorldFixture } | { row: RowPick } | null {
		const fixture = this.pick(ndc.x, ndc.y)?.fixture ?? null;
		if (fixture) return { fixture };
		const row = this.pickRow(ndc.x, ndc.y);
		return row ? { row } : null;
	}

	/** The selection a gesture grabs: what is already selected stays whole, an
	 * unselected item becomes the selection on its own (what every editor does). */
	private targetFor(under: { fixture: WorldFixture } | { row: RowPick }): Selection {
		if ('fixture' in under) {
			const { fixture } = under;
			const selected = this.selection.fixtures.some((entry) => entry.id === fixture.id);
			return selected ? this.selectionView : { fixtures: [fixture], rows: [] };
		}
		const { row } = under;
		const selected = this.selection.rows.some(
			(entry) => entry.table === row.table && entry.id === row.id
		);
		return selected ? this.selectionView : { fixtures: [], rows: [row] };
	}

	private onMove(event: PointerEvent): void {
		const gesture = this.gesture;
		if (!gesture || gesture.pointerId !== event.pointerId) {
			// the arms say what they are before the click does
			const ndc = this.ndcOf(event);
			this.canvas.style.cursor =
				this.editMode === 'move' && this.pickGizmo(ndc) ? 'pointer' : 'grab';
			this.reportHover(event);
			return;
		}
		const dx = event.clientX - gesture.at.x;
		const dy = event.clientY - gesture.at.y;
		gesture.at = { x: event.clientX, y: event.clientY };
		const total =
			Math.abs(event.clientX - gesture.from.x) + Math.abs(event.clientY - gesture.from.y);
		if (total > 3) gesture.moved = true;
		if (gesture.kind === 'pan') return this.pan(dx, dy);
		if (gesture.kind === 'orbit') return this.orbitBy(dx, dy);
		if (gesture.kind === 'marquee') {
			if (gesture.moved) this.drawMarquee(gesture.from, event);
			return;
		}
		// the gizmo reads the snap's own modifier live (shift holds it off); the
		// ground grammar keeps its meanings (shift turns, ctrl scales, alt lifts)
		if ('items' in gesture) {
			gesture.free = event.shiftKey;
			if (gesture.axis !== undefined) this.onGizmoFree?.(event.shiftKey);
		}
		this.applyGrab(gesture, this.ndcOf(event));
	}

	private onUp(event: PointerEvent): void {
		const gesture = this.gesture;
		if (!gesture || gesture.pointerId !== event.pointerId) {
			this.canvas.releasePointerCapture?.(event.pointerId);
			return;
		}
		this.gesture = null;
		this.canvas.releasePointerCapture?.(event.pointerId);
		this.canvas.style.cursor = 'grab';
		if (isGrab(gesture) && gesture.axis !== undefined) this.onGizmoFree?.(null);
		if (gesture.kind === 'marquee') {
			if (gesture.moved) {
				this.finishMarquee(gesture.from, { x: event.clientX, y: event.clientY }, gesture.add);
			} else {
				// a shift+left *click* adds to the selection rather than replacing it
				this.clickSelect(this.ndcOf(event), event.shiftKey || gesture.add);
			}
			return;
		}
		if (gesture.kind === 'pan' || gesture.kind === 'orbit') return;
		this.finishGrab(gesture);
	}

	/** A cancelled pointer is not a gesture: everything goes back where it was. */
	private endPointer(): void {
		const gesture = this.gesture;
		this.gesture = null;
		this.hideMarquee();
		this.canvas.style.cursor = 'grab';
		if (isGrab(gesture)) {
			if (gesture.axis !== undefined) this.onGizmoFree?.(null);
			for (const item of gesture.items) this.resetItem(item);
		}
		this.refreshSelection();
	}

	private onDoubleClick(event: MouseEvent): void {
		const ndc = this.ndcOf(event);
		const fixture = this.pick(ndc.x, ndc.y)?.fixture ?? null;
		if (fixture) this.focusFixture(fixture);
	}

	// -- the camera -------------------------------------------------------------

	/** The camera's own state as three numbers about its target, so an orbit is
	 * a change of two of them and nothing else has to know. */
	private syncOrbit(): void {
		const offset = this.camera.position.clone().sub(this.target);
		this.orbit.distance = Math.max(offset.length(), 1);
		this.orbit.elevation = Math.asin(THREE.MathUtils.clamp(offset.y / this.orbit.distance, -1, 1));
		this.orbit.azimuth = Math.atan2(offset.x, offset.z);
	}

	private applyOrbit(): void {
		const { azimuth, elevation, distance } = this.orbit;
		this.camera.position.set(
			this.target.x + Math.sin(azimuth) * Math.cos(elevation) * distance,
			this.target.y + Math.sin(elevation) * distance,
			this.target.z + Math.cos(azimuth) * Math.cos(elevation) * distance
		);
		this.camera.lookAt(this.target);
	}

	private orbitBy(dx: number, dy: number): void {
		this.orbit.azimuth -= dx * 0.006;
		// never below the horizon: the ground is single-sided, and from below the
		// zone would read as a hole
		this.orbit.elevation = THREE.MathUtils.clamp(this.orbit.elevation + dy * 0.005, 0.02, 1.5);
		this.applyOrbit();
	}

	private pan(dx: number, dy: number): void {
		const scale = this.orbit.distance * 0.0016;
		const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
		const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
		this.target.addScaledVector(right, -dx * scale).addScaledVector(up, dy * scale);
		this.applyOrbit();
	}

	private zoomBy(event: WheelEvent): void {
		event.preventDefault();
		this.orbit.distance = THREE.MathUtils.clamp(
			this.orbit.distance * Math.exp(event.deltaY * 0.0012),
			200,
			300000
		);
		this.applyOrbit();
	}

	// -- selection --------------------------------------------------------------

	/** Hand the selection over (the page's lists, a script, a click): the single
	 * door every path goes through, so the rings and the page always agree. */
	setSelection(selection: Selection, notify = true): void {
		this.selection = { fixtures: [...selection.fixtures], rows: [...selection.rows] };
		this.carried.clear();
		for (const fixture of this.selection.fixtures) {
			this.carried.set(fixture.id, this.proposalsFor(fixture.id));
		}
		this.refreshSelection();
		if (notify) this.onSelect?.(this.selectionView);
	}

	get selectionView(): Selection {
		return { fixtures: [...this.selection.fixtures], rows: [...this.selection.rows] };
	}

	/** Update the selection's own fields (a drag, the panel's number inputs) and
	 * let the page hear about it: the items are the payload's rows themselves. */
	notifySelection(): void {
		this.refreshSelection();
		this.onSelect?.(this.selectionView);
	}

	/** The rings under the selected items and the pin over the leading one: a
	 * small object has to stay findable from a zone-wide camera. */
	private refreshSelection(): void {
		for (const child of [...this.rings.children]) this.rings.remove(child);
		const fixtures = this.selection.fixtures;
		const rows = this.selection.rows;
		const primaryFixture = fixtures.length ? fixtures[fixtures.length - 1] : null;
		const primaryRow = !primaryFixture && rows.length ? rows[rows.length - 1] : null;
		for (const fixture of fixtures) {
			if (!this.terrain || fixture === primaryFixture) continue;
			this.addRing(fixturePlacement(this.terrain, fixture).position, this.ringMaterial);
		}
		for (const pick of rows) {
			const row = this.rowAt(pick);
			if (!row || (primaryRow && pick.table === primaryRow.table && pick.id === primaryRow.id)) {
				continue;
			}
			this.addRing([row.x, row.z, row.y], this.rowRingMaterial);
		}
		this.pointMarker(primaryFixture ?? primaryRow);
		this.refreshGizmo(primaryFixture ?? primaryRow);
	}

	private addRing(position: [number, number, number], material: THREE.Material): void {
		const ring = new THREE.Mesh(this.ringGeometry, material);
		ring.rotation.x = -Math.PI / 2;
		ring.position.set(position[0], position[1] + 12, position[2]);
		this.rings.add(ring);
	}

	/** The pin and its ring stand over the leading selection (or nowhere). */
	private pointMarker(primary: WorldFixture | RowPick | null): void {
		const position = this.positionOf(primary);
		if (!position) {
			this.marker.visible = false;
			return;
		}
		this.marker.position.set(position[0], position[1], position[2]);
		this.marker.visible = true;
	}

	private positionOf(item: WorldFixture | RowPick | null): [number, number, number] | null {
		if (!item || !this.terrain) return null;
		if ('nif_id' in item) return fixturePlacement(this.terrain, item).position;
		const row = this.rowAt(item);
		return row ? [row.x, row.z, row.y] : null;
	}

	/** A click: the fixture under the pointer, else a server row, else nothing.
	 * Shift keeps what was selected (the marquee's own language). */
	private clickSelect(ndc: { x: number; y: number }, add: boolean): void {
		const fixture = this.pick(ndc.x, ndc.y)?.fixture ?? null;
		const row = fixture ? null : this.pickRow(ndc.x, ndc.y);
		if (!fixture && !row) {
			if (!add) this.setSelection({ fixtures: [], rows: [] });
			return;
		}
		const fixtures = add ? [...this.selection.fixtures] : [];
		const rows = add ? [...this.selection.rows] : [];
		if (fixture) {
			const index = fixtures.findIndex((entry) => entry.id === fixture.id);
			if (index >= 0) fixtures.splice(index, 1);
			else fixtures.push(fixture);
		} else if (row) {
			const index = rows.findIndex((entry) => entry.table === row.table && entry.id === row.id);
			if (index >= 0) rows.splice(index, 1);
			else rows.push(row);
		}
		this.setSelection({ fixtures, rows });
	}

	// -- the marquee ------------------------------------------------------------

	private drawMarquee(from: { x: number; y: number }, event: PointerEvent): void {
		const parent = this.canvas.parentElement;
		if (!parent) return;
		if (!this.marquee.parentElement) parent.appendChild(this.marquee);
		const rect = parent.getBoundingClientRect();
		const left = Math.min(from.x, event.clientX) - rect.left;
		const top = Math.min(from.y, event.clientY) - rect.top;
		this.marquee.style.display = 'block';
		this.marquee.style.left = `${left}px`;
		this.marquee.style.top = `${top}px`;
		this.marquee.style.width = `${Math.abs(event.clientX - from.x)}px`;
		this.marquee.style.height = `${Math.abs(event.clientY - from.y)}px`;
	}

	private hideMarquee(): void {
		this.marquee.style.display = 'none';
	}

	/** Everything the marquee covers: a fixture by its own anchor, a row by its
	 * point (what is drawn is what is caught), hidden layers left out. With `add`
	 * (ctrl) the box adds to the selection instead of replacing it. */
	private finishMarquee(
		from: { x: number; y: number },
		to: { x: number; y: number },
		add: boolean
	): void {
		this.hideMarquee();
		if (!this.terrain) return;
		const box = this.marqueeBox(from, to);
		const fixtures: WorldFixture[] = [];
		const rows: RowPick[] = [];
		if (this.groups.fixtures.visible) {
			for (const group of this.picks) {
				for (const fixture of group.fixtures) {
					if (!fixture.nif_id) continue;
					const position = fixturePlacement(this.terrain, fixture).position;
					if (this.inBox(position, box)) fixtures.push(fixture);
				}
			}
		}
		for (const cloud of this.rowClouds) {
			if (!cloud.cloud.visible || !cloud.group.visible) continue;
			for (const row of cloud.rows) {
				if (this.inBox([row.x, row.z, row.y], box)) {
					rows.push({ table: cloud.table, id: row.id });
				}
			}
		}
		const kept = add ? this.selection : { fixtures: [], rows: [] };
		const keptFixtures = new Set(kept.fixtures.map((fixture) => fixture.id));
		const keptRows = new Set(kept.rows.map((row) => `${row.table}:${row.id}`));
		this.setSelection({
			fixtures: [
				...kept.fixtures,
				...this.uniqueFixtures(fixtures).filter((fixture) => !keptFixtures.has(fixture.id))
			],
			rows: [
				...kept.rows,
				...rows.filter((row) => !keptRows.has(`${row.table}:${row.id}`)).slice(0, 600)
			]
		});
	}

	private uniqueFixtures(fixtures: WorldFixture[]): WorldFixture[] {
		const seen = new Map<number, WorldFixture>();
		for (const fixture of fixtures) seen.set(fixture.id, fixture);
		return [...seen.values()];
	}

	private marqueeBox(
		from: { x: number; y: number },
		to: { x: number; y: number }
	): { x0: number; y0: number; x1: number; y1: number } {
		return {
			x0: Math.min(from.x, to.x),
			y0: Math.min(from.y, to.y),
			x1: Math.max(from.x, to.x),
			y1: Math.max(from.y, to.y)
		};
	}

	/** A zone-local point, in or out of the drawn rectangle. Points behind the
	 * camera are out (their projection mirrors), which is what the row check
	 * below is for. */
	private inBox(
		position: [number, number, number],
		box: { x0: number; y0: number; x1: number; y1: number }
	): boolean {
		const point = new THREE.Vector3(...position);
		const view = point.clone().applyMatrix4(this.camera.matrixWorldInverse);
		if (view.z > -1) return false;
		const rect = this.canvas.getBoundingClientRect();
		const projected = point.clone().project(this.camera);
		const x = rect.left + ((projected.x + 1) / 2) * rect.width;
		const y = rect.top + ((1 - projected.y) / 2) * rect.height;
		return x >= box.x0 && x <= box.x1 && y >= box.y0 && y <= box.y1;
	}

	// -- the gestures -----------------------------------------------------------

	/** The pointer-down bookkeeping every gesture starts from. */
	private pointerBase(event: PointerEvent): {
		pointerId: number;
		button: number;
		from: { x: number; y: number };
		at: { x: number; y: number };
		moved: boolean;
	} {
		return {
			pointerId: event.pointerId,
			button: event.button,
			from: { x: event.clientX, y: event.clientY },
			at: { x: event.clientX, y: event.clientY },
			moved: false
		};
	}

	/** Arm the ground click (`add`): every other gesture is a mouse button. */
	setEditMode(mode: EditMode, snap = true): void {
		this.editMode = mode;
		this.snap = snap;
		this.showGrid(mode === 'add');
		this.refreshGizmo();
	}

	/** The gizmo (the drafts' Unity-style arrows): one arm per axis, built along
	 * +Y then turned into place; the fat, invisible collider under each arm is
	 * what the pointer actually hits. The group is scaled by the camera's own
	 * distance every frame, so it keeps its size on screen. */
	private buildGizmo(): void {
		const hidden = new THREE.MeshBasicMaterial({
			transparent: true,
			opacity: 0,
			depthWrite: false
		});
		for (const spec of GIZMO_AXES) {
			const arm = new THREE.Group();
			const material = new THREE.MeshBasicMaterial({ color: spec.color, depthTest: false });
			const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.7, 10), material);
			shaft.position.y = 0.45;
			const head = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.22, 14), material);
			head.position.y = 0.91;
			shaft.renderOrder = 10;
			head.renderOrder = 10;
			arm.add(shaft, head);
			if (spec.axis === 'x') arm.rotation.z = -Math.PI / 2;
			if (spec.axis === 'y') arm.rotation.x = Math.PI / 2;
			const collider = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 1, 8), hidden);
			collider.position.y = 0.5;
			collider.userData.axis = spec.axis;
			arm.add(collider);
			this.gizmoColliders.push(collider);
			this.gizmo.add(arm);
		}
		this.gizmo.visible = false;
	}

	/** The gizmo stands over the leading selection while a move is armed: the
	 * arrows are the visible half of the strip's own mode. */
	private refreshGizmo(primary?: WorldFixture | RowPick | null): void {
		const leading =
			primary !== undefined
				? primary
				: this.selection.fixtures.length
					? this.selection.fixtures[this.selection.fixtures.length - 1]
					: this.selection.rows.length
						? this.selection.rows[this.selection.rows.length - 1]
						: null;
		const position = this.editMode === 'move' ? this.positionOf(leading) : null;
		this.gizmoTarget = position;
		this.gizmo.visible = position !== null;
		if (!position) return;
		this.gizmo.position.set(position[0], position[1], position[2]);
		this.gizmo.updateMatrixWorld(true);
	}

	private pickGizmo(ndc: { x: number; y: number }): GizmoAxis | null {
		if (!this.gizmo.visible) return null;
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), this.camera);
		const hit = ray.intersectObjects(this.gizmoColliders, false)[0];
		const axis = hit ? (hit.object.userData.axis as GizmoAxis | undefined) : undefined;
		return axis ?? null;
	}

	/** Where the pointer ray meets one axis of the gizmo: the travel along that
	 * axis, in zone-local units (null when the two are parallel). */
	private axisT(
		axis: GizmoAxis,
		ndc: { x: number; y: number },
		origin?: [number, number, number]
	): number | null {
		const spec = GIZMO_AXES.find((entry) => entry.axis === axis);
		const at = origin ?? this.gizmoTarget;
		if (!spec || !at) return null;
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), this.camera);
		return axisParam(
			{ x: ray.ray.origin.x, y: ray.ray.origin.y, z: ray.ray.origin.z },
			{ x: ray.ray.direction.x, y: ray.ray.direction.y, z: ray.ray.direction.z },
			{ x: at[0], y: at[1], z: at[2] },
			{ x: spec.dir[0], y: spec.dir[1], z: spec.dir[2] }
		);
	}

	/** A gizmo drag: the grab machinery, with the transform read off one axis
	 * (see `transformOf`) — the arrows do exactly what they show, and the ground
	 * grammar (drag the selection, the middle button, the wheel) is untouched. */
	private beginGizmo(base: GestureBase, axis: GizmoAxis, ndc: { x: number; y: number }): boolean {
		const at = this.gizmoTarget;
		if (!at) return false;
		const t = this.axisT(axis, ndc, at);
		if (t === null) return false;
		if (!this.beginGrab(base, 'move', ndc, this.selectionView)) return false;
		const grab = this.gesture;
		if (!grab || !('items' in grab)) return false;
		grab.axis = axis;
		grab.axisOrigin = [...at];
		grab.t0 = t;
		return true;
	}

	/** Quantize a transform's own parameter — once, for the whole selection.
	 * Snapping each item's *position* would silently deform the group: two
	 * objects 40 units apart would leave the gesture 300 apart. */
	private quantize(value: number, step: number): number {
		return quantized(value, step, this.snap);
	}

	/** A grab begins: the selection is snapshotted — every item's origins and the
	 * rows it carries — and the ground point under the pointer becomes the anchor
	 * every move is measured from. One state, for the whole gesture. */
	private beginGrab(
		base: Omit<Grab, 'kind' | 'anchor' | 'pivot' | 'startAngle' | 'items'>,
		kind: EditKind,
		ndc: { x: number; y: number },
		target: Selection
	): boolean {
		if (!this.terrain) return false;
		if (!this.sameSelection(target, this.selection)) this.setSelection(target);
		const items = this.grabItems(target);
		if (!items.length) return false;
		const pivot = this.pivotOf(items);
		// the anchor: the terrain under the pointer, or — over the sky — the
		// horizontal plane through the items themselves
		const hit = this.pickGround(ndc.x, ndc.y);
		const flat = hit ? null : this.planePoint(ndc, pivot.z);
		if (!hit && !flat) return false;
		const anchor = hit ?? { x: flat!.x, y: flat!.y, z: pivot.z };
		this.gesture = {
			...base,
			kind,
			anchor,
			pivot: { x: pivot.x, y: pivot.y },
			startAngle: Math.atan2(anchor.y - pivot.y, anchor.x - pivot.x),
			items
		};
		this.canvas.style.cursor = 'grabbing';
		return true;
	}

	/** The same selection, by id — never by object identity (the page's `$state`
	 * proxies and the payload's plain rows are two views of the same row). */
	private sameSelection(a: Selection, b: Selection): boolean {
		const fixtures = new Set(b.fixtures.map((fixture) => fixture.id));
		const rows = new Set(b.rows.map((row) => `${row.table}:${row.id}`));
		return (
			a.fixtures.length === b.fixtures.length &&
			a.rows.length === b.rows.length &&
			a.fixtures.every((fixture) => fixtures.has(fixture.id)) &&
			a.rows.every((row) => rows.has(`${row.table}:${row.id}`))
		);
	}

	private pivotOf(items: GrabItem[]): { x: number; y: number; z: number } {
		let x = 0;
		let y = 0;
		let z = 0;
		for (const item of items) {
			x += item.from.x;
			y += item.from.y;
			z += item.from.z;
		}
		const count = items.length || 1;
		return { x: x / count, y: y / count, z: z / count };
	}

	/** The items a gesture holds, with their origins: the selection's fixtures and
	 * rows, plus the rows the fixtures carry (B2) — each carried by its *nearest*
	 * fixture, and never one the user grabbed in its own right: one gesture writes
	 * a row once. */
	private grabItems(target: Selection): GrabItem[] {
		const rows: GrabRow[] = [];
		const taken = new Set<string>();
		for (const pick of target.rows) {
			const row = this.rowAt(pick);
			if (!row) continue;
			taken.add(`${pick.table}:${pick.id}`);
			rows.push({
				tag: 'row',
				table: pick.table,
				row,
				from: { x: row.x, y: row.y, z: row.z, heading: row.heading }
			});
		}
		const carriers = new Map<string, { proposal: RowProposal; fixture: WorldFixture }>();
		for (const fixture of target.fixtures) {
			for (const proposal of this.carriedRows(fixture.id)) {
				const key = `${proposal.table}:${proposal.key}`;
				if (taken.has(key)) continue;
				const seen = carriers.get(key);
				if (!seen || proposal.distance < seen.proposal.distance) {
					carriers.set(key, { proposal, fixture });
				}
			}
		}
		const fixtures: GrabFixture[] = target.fixtures.map((fixture) => ({
			tag: 'fixture',
			fixture,
			from: {
				x: fixture.x,
				y: fixture.y,
				z: this.drawnZ(fixture),
				angle: fixture.angle,
				scale: fixture.scale
			},
			carried: []
		}));
		const byId = new Map(fixtures.map((item) => [item.fixture.id, item]));
		for (const { proposal, fixture } of carriers.values()) {
			const item = byId.get(fixture.id);
			const row = this.rowAt({ table: proposal.table, id: proposal.key });
			if (!item || !row) continue;
			item.carried.push({
				table: proposal.table,
				row,
				from: { x: row.x, y: row.y, z: row.z, heading: row.heading }
			});
		}
		return [...fixtures, ...rows];
	}

	/** The `z` a fixture stands at as the client and the daemon draw it: the
	 * terrain for a ground-placed row (W-3), the stored `Z` otherwise. */
	private drawnZ(fixture: WorldFixture): number {
		return this.terrain ? fixturePlacement(this.terrain, fixture).position[1] : fixture.z;
	}

	/** The gesture's transform, recomputed from the origins on every move: the
	 * pointer's own ground point (through the anchor's plane), never a chain of
	 * small deltas — the two disagree the moment the terrain, or the zoom, does. */
	private transformOf(grab: Grab, ndc: { x: number; y: number }): Transform | null {
		// a gizmo drag: the pointer's own travel along one axis, measured from where
		// the axis stood when the drag began (the preview may move the items; the
		// ruler must not move with them)
		if (grab.axis !== undefined && grab.t0 !== undefined && grab.axisOrigin) {
			const t = this.axisT(grab.axis, ndc, grab.axisOrigin);
			if (t === null) return null;
			// shift, held during the drag, buys whole units instead of the grid
			const snap = this.snap && grab.free !== true;
			const travel = t - grab.t0;
			if (grab.axis === 'z') return { kind: 'height', dz: quantized(travel, SNAP_HEIGHT, snap) };
			const step = quantized(travel, SNAP_POSITION, snap);
			return grab.axis === 'x'
				? { kind: 'move', dx: step, dy: 0 }
				: { kind: 'move', dx: 0, dy: step };
		}
		if (grab.kind === 'move' || grab.kind === 'rotate') {
			const point = this.planePoint(ndc, grab.anchor.z);
			if (!point) return null;
			if (grab.kind === 'move') {
				return {
					kind: 'move',
					dx: this.quantize(point.x - grab.anchor.x, SNAP_POSITION),
					dy: this.quantize(point.y - grab.anchor.y, SNAP_POSITION)
				};
			}
			const angle = Math.atan2(point.y - grab.pivot.y, point.x - grab.pivot.x);
			let degrees = ((angle - grab.startAngle) * 180) / Math.PI;
			// the shortest way round, so a turn past ±180° never flips the group
			degrees = ((((degrees + 180) % 360) + 360) % 360) - 180;
			return { kind: 'rotate', degrees: this.quantize(degrees, SNAP_ANGLE), pivot: grab.pivot };
		}
		// up is up: dragging the pointer up raises, and the step follows the
		// camera's distance so it feels the same at any zoom
		const units = (this.orbit.distance * 0.0016 + 1) * -(grab.at.y - grab.from.y);
		if (grab.kind === 'height') {
			return { kind: 'height', dz: this.quantize(units, SNAP_HEIGHT) };
		}
		return { kind: 'scale', percent: this.quantize(units / 40, SNAP_SCALE) };
	}

	/** Where a screen point meets the horizontal plane at `height`, in zone-local
	 * map coordinates: the gesture never re-reads the terrain *under* the pointer,
	 * so a ridge cannot make the selection jump. */
	private planePoint(
		ndc: { x: number; y: number },
		height: number
	): { x: number; y: number } | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), this.camera);
		const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -height);
		const point = new THREE.Vector3();
		return ray.ray.intersectPlane(plane, point) ? { x: point.x, y: point.z } : null;
	}

	/** One pointer move: one transform, computed from the origins and applied to
	 * every item by the same numbers — the shape of a selection never changes. */
	private applyGrab(grab: Grab, ndc: { x: number; y: number }): void {
		const transform = this.transformOf(grab, ndc);
		if (!transform) return;
		this.applyTransform(grab.items, transform);
	}

	private applyTransform(items: GrabItem[], transform: Transform): void {
		for (const item of items) {
			if (item.tag === 'fixture') {
				this.transformFixture(item, transform);
				this.updateFixture(item.fixture, false);
				for (const carried of item.carried) {
					this.updateRow({ table: carried.table, id: carried.row.id }, false);
				}
			} else {
				this.transformRow(item, transform);
				this.updateRow({ table: item.table, id: item.row.id }, false);
			}
		}
		this.refreshSelection();
	}

	private transformFixture(item: GrabFixture, transform: Transform): void {
		const fixture = item.fixture;
		const from = item.from;
		if (transform.kind === 'move') {
			fixture.x = from.x + transform.dx;
			fixture.y = from.y + transform.dy;
		} else if (transform.kind === 'rotate') {
			fixture.angle = (((from.angle + transform.degrees) % 360) + 360) % 360;
			const turned = rotateAbout(from.x, from.y, transform.pivot, transform.degrees);
			fixture.x = Math.round(turned.x);
			fixture.y = Math.round(turned.y);
		} else if (transform.kind === 'scale') {
			fixture.scale = THREE.MathUtils.clamp(
				Math.round(from.scale * (1 + transform.percent / 100)),
				5,
				400
			);
		} else if (!fixture.ground) {
			// W-3: a ground row has no z of its own — the client drops it on the terrain
			fixture.z = from.z + transform.dz;
		}
		if (!item.carried.length) return;
		// The rows this fixture carries follow its frame — the daemon's own
		// arithmetic (B2), which `carryRow` matches and its test holds to it.
		const before: CarryFrame = { x: from.x, y: from.y, z: from.z, angle: from.angle };
		const after: CarryFrame = {
			x: fixture.x,
			y: fixture.y,
			z: this.drawnZ(fixture),
			angle: fixture.angle
		};
		for (const carried of item.carried) {
			const moved = carryRow(before, after, carried.from);
			carried.row.x = Math.round(moved.x);
			carried.row.y = Math.round(moved.y);
			carried.row.z = moved.z;
			if (moved.heading !== undefined) carried.row.heading = moved.heading;
		}
	}

	private transformRow(item: GrabRow, transform: Transform): void {
		const row = item.row;
		const from = item.from;
		if (transform.kind === 'scale') return; // a server row has no scale to turn
		if (transform.kind === 'height') {
			row.z = from.z + transform.dz;
			return;
		}
		if (transform.kind === 'move') {
			row.x = from.x + transform.dx;
			row.y = from.y + transform.dy;
		} else {
			const turned = rotateAbout(from.x, from.y, transform.pivot, transform.degrees);
			row.x = Math.round(turned.x);
			row.y = Math.round(turned.y);
			if (from.heading !== undefined) {
				const turn = Math.round((transform.degrees * HEADING_UNITS) / 360);
				row.heading = (((from.heading + turn) % HEADING_UNITS) + HEADING_UNITS) % HEADING_UNITS;
			}
		}
		// The daemon keeps a moved row's height *above the terrain* when the op
		// names no `z` — the preview keeps the same rule, or the row jumps on the
		// next reload (the two are the same arithmetic, by construction).
		if (this.terrain) {
			const offset = from.z - this.terrain.heightAt(from.x, from.y);
			row.z = Math.round(this.terrain.heightAt(row.x, row.y) + offset);
		}
	}

	/** The gesture ended: the page journals what it changed — and only that. An
	 * item the gesture did not change goes back to its origin, so what stands on
	 * the screen is exactly what the journal holds. The rows go first: their ops
	 * reload the row clouds, and a fixture op that landed before them would have
	 * the daemon re-read rows the gesture is about to move. */
	private finishGrab(grab: Grab): void {
		const edits: FixtureEdit[] = [];
		const rows: RowEdit[] = [];
		for (const item of grab.items) {
			if (item.tag === 'fixture') {
				if (this.fixtureChanged(item)) {
					edits.push({
						id: item.fixture.id,
						x: item.fixture.x,
						y: item.fixture.y,
						z: item.fixture.z,
						angle: item.fixture.angle,
						scale: item.fixture.scale,
						carried: item.carried.map((carried) => ({
							table: carried.table,
							key: carried.row.id
						}))
					});
				} else {
					this.resetItem(item);
				}
				continue;
			}
			if (this.rowChanged(item)) {
				rows.push({
					table: item.table,
					key: item.row.id,
					x: item.row.x,
					y: item.row.y,
					z: item.row.z,
					heading: item.from.heading !== undefined ? item.row.heading : undefined
				});
			} else {
				this.resetItem(item);
			}
		}
		this.refreshSelection();
		if (rows.length) this.onRowEdit?.(grab.kind, rows);
		if (edits.length) this.onEdit?.(grab.kind, edits);
	}

	/** Did the gesture change what the editor can *write*? A `Ground = 1` row has
	 * no `z` of its own (W-3), so a phantom height difference must not journal. */
	private fixtureChanged(item: GrabFixture): boolean {
		const fixture = item.fixture;
		const from = item.from;
		return (
			fixture.x !== from.x ||
			fixture.y !== from.y ||
			fixture.angle !== from.angle ||
			fixture.scale !== from.scale ||
			(!fixture.ground && fixture.z !== from.z)
		);
	}

	private rowChanged(item: GrabRow): boolean {
		const row = item.row;
		const from = item.from;
		return (
			row.x !== from.x ||
			row.y !== from.y ||
			row.z !== from.z ||
			(row.heading ?? 0) !== (from.heading ?? 0)
		);
	}

	/** Put one item back exactly where the gesture found it (its carried rows
	 * with it): a cancelled pointer, or a change the journal will not hear about. */
	private resetItem(item: GrabItem): void {
		if (item.tag === 'fixture') {
			const fixture = item.fixture;
			fixture.x = item.from.x;
			fixture.y = item.from.y;
			fixture.angle = item.from.angle;
			fixture.scale = item.from.scale;
			if (!fixture.ground) fixture.z = item.from.z;
			this.updateFixture(fixture, false);
			for (const carried of item.carried) {
				carried.row.x = carried.from.x;
				carried.row.y = carried.from.y;
				carried.row.z = carried.from.z;
				if (carried.from.heading !== undefined) carried.row.heading = carried.from.heading;
				this.updateRow({ table: carried.table, id: carried.row.id }, false);
			}
			return;
		}
		item.row.x = item.from.x;
		item.row.y = item.from.y;
		item.row.z = item.from.z;
		if (item.from.heading !== undefined) item.row.heading = item.from.heading;
		this.updateRow({ table: item.table, id: item.row.id }, false);
	}

	/** Drop the selection at a zone-local point (the panel's "move here"): an
	 * explicit destination, so the *leading* item lands on the point exactly and
	 * every other item takes the same vector — no snapping in between. */
	moveSelectionTo(point: { x: number; y: number }): void {
		if (!this.terrain) return;
		const items = this.grabItems(this.selection);
		if (!items.length) return;
		const pivot = this.pivotOf(items);
		const grab: Grab = {
			kind: 'move',
			pointerId: -1,
			button: 0,
			from: { x: 0, y: 0 },
			at: { x: 0, y: 0 },
			moved: true,
			anchor: { x: items[0].from.x, y: items[0].from.y, z: pivot.z },
			pivot: { x: pivot.x, y: pivot.y },
			startAngle: 0,
			items
		};
		this.applyTransform(items, {
			kind: 'move',
			dx: Math.round(point.x - grab.anchor.x),
			dy: Math.round(point.y - grab.anchor.y)
		});
		this.finishGrab(grab);
	}

	/** Every item the scene holds, with the fields a measured test reads */
	items(): {
		fixtures: Array<{
			id: number;
			x: number;
			y: number;
			z: number;
			angle: number;
			scale: number;
			ground: boolean;
		}>;
		rows: Array<{
			table: SpawnTable;
			id: string;
			x: number;
			y: number;
			z: number;
			heading?: number;
		}>;
	} {
		return {
			fixtures: this.picks.flatMap((group) =>
				group.fixtures
					.filter((fixture) => fixture.nif_id)
					.map((fixture) => ({
						id: fixture.id,
						x: fixture.x,
						y: fixture.y,
						z: fixture.z,
						angle: fixture.angle,
						scale: fixture.scale,
						ground: fixture.ground
					}))
			),
			rows: this.rowClouds.flatMap((cloud) =>
				cloud.rows.map((row) => ({
					table: cloud.table,
					id: row.id,
					x: row.x,
					y: row.y,
					z: row.z,
					heading: row.heading
				}))
			)
		};
	}

	/** The ground under a screen point, zone-local (a test's own probe: the
	 * gestures measure the same surface the `add` click does). */
	groundAt(clientX: number, clientY: number): { x: number; y: number; z: number } | null {
		const ndc = this.ndcOf({ clientX, clientY } as MouseEvent);
		return this.pickGround(ndc.x, ndc.y);
	}

	/** The screen point a zone-local map point projects to (a test's own probe, for
	 * gestures that need a pointer exactly there); `height` defaults to the
	 * terrain under the point. */
	screenAt(x: number, y: number, height?: number): { x: number; y: number } {
		const at = height ?? (this.terrain ? this.terrain.heightAt(x, y) : 0);
		const point = new THREE.Vector3(x, at, y).project(this.camera);
		const rect = this.canvas.getBoundingClientRect();
		return {
			x: rect.left + ((point.x + 1) / 2) * rect.width,
			y: rect.top + ((1 - point.y) / 2) * rect.height
		};
	}

	/** Where a screen point meets the plane at `height` (a test's own probe: the
	 * same plane a move measures its vector on). */
	planeAt(clientX: number, clientY: number, height: number): { x: number; y: number } | null {
		return this.planePoint(this.ndcOf({ clientX, clientY } as MouseEvent), height);
	}

	/** Write one row back into its cloud (the drag's live preview). Found by
	 * id, never by object identity: the page's `$state` proxies and the
	 * payload's plain rows are two views of the same row. */
	updateRow(pick: RowPick, moveMarker = true): void {
		const entry = this.rowClouds.find((cloud) => cloud.table === pick.table);
		const index = entry ? entry.rows.findIndex((row) => row.id === pick.id) : -1;
		if (!entry || index < 0) return;
		const row = entry.rows[index];
		const attribute = entry.cloud.geometry.getAttribute('position') as THREE.BufferAttribute;
		attribute.setXYZ(index, row.x, row.z, row.y);
		attribute.needsUpdate = true;
		const preview = this.previews.get(`${pick.table}:${pick.id}`);
		if (preview) this.updatePreviewTransform(preview, row);
		const selected = this.selection.rows.some(
			(entry) => entry.table === pick.table && entry.id === pick.id
		);
		if (moveMarker && selected && !this.selection.fixtures.length) {
			this.pointMarker(pick);
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
	updateFixture(fixture: WorldFixture, notify = true): void {
		if (!this.terrain) return;
		const placement = fixturePlacement(this.terrain, fixture);
		const matrix = new THREE.Matrix4();
		for (const group of this.picks) {
			const index = group.fixtures.findIndex((row) => row.id === fixture.id);
			if (index < 0) continue;
			// The caller may hold another view of the row (the page's `$state`
			// proxy, a payload that was replaced): the pick's own row takes the
			// fields, so the scene's data and its instances never disagree — a
			// gesture after a typed edit must start from what the user sees.
			if (group.fixtures[index] !== fixture) Object.assign(group.fixtures[index], fixture);
			matrix.compose(
				new THREE.Vector3(...placement.position),
				new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
				new THREE.Vector3(placement.scale, placement.scale, placement.scale)
			);
			matrix.multiply(group.local);
			group.mesh.setMatrixAt(index, matrix);
			group.mesh.instanceMatrix.needsUpdate = true;
		}
		if (notify) {
			this.refreshSelection();
			this.onSelect?.(this.selectionView);
		}
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
	 * Meshes stay cached; only the instancing is redone — and the selection
	 * re-resolves by id, because the payload's rows are new objects: a fixture
	 * edited after a reload would otherwise write into a ghost. */
	async rebuildFixtures(zoneId: number, zone: WorldZone): Promise<void> {
		if (!this.terrain) return;
		this.clearFixtures();
		await this.buildFixtures(zoneId, zone, this.terrain, this.token);
		this.setSelection(this.resolveSelection());
	}

	/** The selection's fixtures, re-found by id among the picks as they stand. */
	private resolveSelection(): Selection {
		const fixtures = this.picks.flatMap((group) => group.fixtures);
		return {
			fixtures: this.selection.fixtures
				.map((fixture) => fixtures.find((row) => row.id === fixture.id))
				.filter((row): row is WorldFixture => Boolean(row)),
			rows: [...this.selection.rows]
		};
	}

	private clearFixtures(): void {
		// the instances are about to be rebuilt: a gesture holding them would move
		// rows nothing draws any more
		this.gesture = null;
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
		this.previewsVisible = toggles.models;
		this.labelsVisible = toggles.labels;
		for (const entry of this.previews.values()) entry.group.visible = toggles.models;
		if (!toggles.labels) this.hideLabels();
	}

	/** What the scene currently holds — the diagnostic hook the screenshots read. */
	stats(): Record<string, number | string> {
		const instances = this.groups.fixtures.children.reduce(
			(sum, child) => sum + ((child as THREE.InstancedMesh).count ?? 0),
			0
		);
		const architecture = this.groups.architecture.children.reduce(
			(sum, child) => sum + ((child as THREE.InstancedMesh).count ?? 0),
			0
		);
		return {
			ground: this.groups.ground.children.length,
			fixtureGroups: this.groups.fixtures.children.length,
			architectureGroups: this.groups.architecture.children.length,
			architectureInstances: architecture,
			previews: this.previews.size,
			labels: this.labelButtons.size,
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

	/** The server row under a click. A nearby preview's own volume is the
	 * first target; a point cloud has no surface, so the raycaster matches
	 * within a radius — each cloud is probed with its own dot size, a door
	 * being easier to hit than a mob exactly as it is drawn. */
	pickRow(ndcX: number, ndcY: number): RowPick | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
		const previews = [...this.previews.values()]
			.filter((entry) => entry.group.visible && entry.group.parent?.visible !== false)
			.map((entry) => entry.hit);
		const previewHit = ray.intersectObjects(previews, false)[0];
		if (previewHit) return previewHit.object.userData.pick as RowPick;
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

	/** Mark one fixture (the page's lists, a script): the single-item shorthand
	 * for `setSelection`. */
	select(fixture: WorldFixture | null): void {
		this.setSelection({ fixtures: fixture ? [fixture] : [], rows: [] });
	}

	/** Mark one server row, the way a fixture is marked (same ring and pin). */
	selectRow(pick: RowPick | null): void {
		if (pick && !this.rowAt(pick)) return;
		this.setSelection({ fixtures: [], rows: pick ? [pick] : [] });
	}

	/** The server rows a fixture would carry if it moved now (B2): the pairs
	 * the page sends with the op, and the ones the daemon validates live. */
	carriedRows(fixtureId: number): RowProposal[] {
		return this.carried.get(fixtureId) ?? this.proposalsFor(fixtureId);
	}

	/** Drop the selection at a zone-local point: what the page's "move here"
	 * does, and what a click on the ground with something selected means. */
	moveSelection(point: { x: number; y: number }): void {
		this.moveSelectionTo(point);
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

	/** Recentre the camera on a fixture (F, or a double click): close, above. */
	focusFixture(fixture: WorldFixture, distance = 1400): void {
		if (!this.terrain) return;
		this.lookAtPoint(fixturePlacement(this.terrain, fixture).position, distance);
	}

	/** Recentre on the selection's own middle (the panel's Focus button). */
	focusSelection(distance = 2400): void {
		if (!this.terrain) return;
		const points = this.selection.fixtures.map(
			(fixture) => fixturePlacement(this.terrain!, fixture).position
		);
		for (const pick of this.selection.rows) {
			const row = this.rowAt(pick);
			if (row) points.push([row.x, row.z, row.y]);
		}
		if (!points.length) return;
		const middle = points
			.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1], sum[2] + point[2]], [0, 0, 0])
			.map((value) => value / points.length) as [number, number, number];
		this.lookAtPoint(middle, Math.max(distance, points.length * 40));
	}

	private lookAtPoint(position: [number, number, number], distance: number): void {
		this.target.set(position[0], position[1], position[2]);
		this.orbit.azimuth = 0.6;
		this.orbit.elevation = 0.55;
		this.orbit.distance = distance;
		this.applyOrbit();
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
			// the gizmo keeps its size on screen, whatever the zoom (Unity's trick)
			if (this.gizmo.visible) {
				const distance = this.camera.position.distanceTo(this.gizmo.position);
				this.gizmo.scale.setScalar(Math.max(distance * 0.2, 1));
			}
			const now = performance.now();
			if (now - this.lastPreview > 250) {
				this.lastPreview = now;
				this.refreshPreviews();
			}
			if (now - this.lastLabels > 100) {
				this.lastLabels = now;
				this.updateLabels();
			}
			// The label *set* changes at 10 Hz; their positions follow the camera
			// every frame, or they visibly lag the scene while it moves.
			this.positionLabels();
			this.renderer.render(this.scene, this.camera);
		};
		loop();
	}

	dispose(): void {
		cancelAnimationFrame(this.frame);
		this.abort.abort();
		this.marquee.remove();
		this.labelLayer.remove();
		this.clear();
		this.disposeCreatureCache();
		this.hitMaterial.dispose();
		this.ringGeometry.dispose();
		this.ringMaterial.dispose();
		this.rowRingMaterial.dispose();
		this.grid?.geometry.dispose();
		this.renderer.dispose();
	}

	/** The GLB scenes are shared by every clone of a model; dispose them once,
	 * after the clones are gone. */
	private disposeCreatureCache(): void {
		for (const promise of this.creatureCache.values()) {
			void promise.then((loaded) => {
				loaded?.model.scene.traverse((object) => {
					const mesh = object as THREE.Mesh;
					if (!mesh.isMesh) return;
					mesh.geometry?.dispose();
					const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
					if (Array.isArray(material)) material.forEach((item) => item.dispose());
					else material?.dispose();
				});
			});
		}
		this.creatureCache.clear();
	}

	private clear(): void {
		this.clearPreviews();
		this.picks = [];
		this.rowClouds = [];
		this.selection = { fixtures: [], rows: [] };
		this.carried.clear();
		this.gesture = null;
		this.marker.visible = false;
		for (const child of [...this.rings.children]) this.rings.remove(child);
		this.measureA = null;
		this.measureB = null;
		this.drawMeasure();
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
		this.flatGround = Boolean(zone.terrain?.flat);
		this.framedRows = false;
		this.architectureAligned = false;
		const city = zone.architecture_anchor === 'city';
		const dungeon = zone.architecture_anchor === 'dungeon';
		const fixed = city || dungeon;
		// A city and a dungeon both hang their layout under the client's fixed
		// root, whose 180-degree yaw flips both plane axes. The group carries
		// that frame once. The fallback (no client frame) keeps the x mirror and
		// row fitting for an architecture set without one.
		this.groups.architecture.scale.set(fixed ? 1 : -1, 1, 1);
		this.groups.architecture.rotation.set(0, fixed ? CLIENT_YAW : 0, 0);
		if (fixed) {
			const [offsetX, offsetY] = zone.info.world_rect;
			this.groups.architecture.position.set(
				CLIENT_ORIGIN - offsetX,
				city ? CITY_HEIGHT : DUNGEON_HEIGHT,
				CLIENT_ORIGIN - offsetY
			);
			this.architectureAligned = true;
			this.architectureBounds = null;
		} else {
			this.groups.architecture.position.set(0, 0, 0);
			this.architectureBounds = this.fixtureBounds(zone.architecture ?? []);
		}
		// A city or a dungeon ships its own floors; the synthetic flat ground
		// would cut through them (Camelot's streets sat under it by 86 units).
		// The ground stays the fallback for zones whose client layout is absent.
		if (!zone.architecture?.length) this.buildGround(zoneId, zone, data, token);
		await this.buildFixtures(zoneId, zone, data, token);
		if (token !== this.token) return;
		await this.buildArchitecture(zoneId, zone, data, token);
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
		this.addRows('mob', 'spawns', spawns.mobs, 0xe0b23e, 45);
		this.addRows('worldobject', 'statics', spawns.objects, 0x64b5f6, 70);
		this.addRows('door', 'doors', spawns.doors, 0xef5350, 80);
		this.refreshPreviews();
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
		// A city or dungeon has no client ground to frame: its rows are the
		// content, so put the camera on them once (not after every edit).
		if (this.flatGround && !this.framedRows) {
			this.frameRows(spawns);
			this.framedRows = true;
			this.refreshPreviews();
		}
		if (this.architectureBounds && !this.architectureAligned) {
			this.architectureAligned = this.alignArchitecture(spawns);
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

	// -- nearby entities: client figures, volumes and labels --------------------

	private keyOf(pick: RowPick): string {
		return `${pick.table}:${pick.id}`;
	}

	private groupFor(table: SpawnTable): THREE.Group {
		return table === 'mob'
			? this.groups.spawns
			: table === 'worldobject'
				? this.groups.statics
				: this.groups.doors;
	}

	/** Yaw of a server heading (0..4095, clockwise from north), the same
	 * conversion the fixtures' `A` uses. */
	private rowYaw(heading?: number): number {
		return -((((heading ?? 0) % HEADING_UNITS) / HEADING_UNITS) * Math.PI * 2);
	}

	/** Keep the nearest rows drawn as their own model or a volume. Runs on a
	 * timer: a camera move is cheap, and loading is what must stay lazy. */
	private refreshPreviews(): void {
		if (!this.terrain) return;
		const radius = THREE.MathUtils.clamp(
			this.orbit.distance * 0.22,
			PREVIEW_MIN_RADIUS,
			PREVIEW_MAX_RADIUS
		);
		const origin = this.camera.position;
		const near: Array<{ cloud: RowCloud; row: SpawnRow; distance: number }> = [];
		for (const cloud of this.rowClouds) {
			if (!cloud.group.visible) continue;
			for (const row of cloud.rows) {
				if (cloud.table === 'mob' && (!row.model || row.model <= 0)) continue;
				const distance = Math.hypot(row.x - origin.x, row.y - origin.z);
				if (distance <= radius) near.push({ cloud, row, distance });
			}
		}
		near.sort((a, b) => a.distance - b.distance);
		const keep = new Set<string>();
		let shown = 0;
		for (const { cloud, row } of near) {
			if (shown >= PREVIEW_LIMIT) break;
			const pick: RowPick = { table: cloud.table, id: row.id };
			const key = this.keyOf(pick);
			keep.add(key);
			const existing = this.previews.get(key);
			if (existing) {
				if (existing.table === 'mob' && existing.model !== row.model) {
					this.removePreview(key);
					continue;
				}
				this.updatePreviewTransform(existing, row);
				existing.group.visible = this.previewsVisible;
				shown++;
				continue;
			}
			if (this.previewLoading.has(key)) continue;
			if (cloud.table === 'mob') this.loadCreaturePreview(pick, row, this.token);
			else this.addVolumePreview(pick, row);
			shown++;
		}
		for (const key of [...this.previews.keys()]) {
			if (!keep.has(key)) this.removePreview(key);
		}
	}

	private loadCreaturePreview(pick: RowPick, row: SpawnRow, token: number): void {
		const key = this.keyOf(pick);
		const model = row.model;
		if (!model) return;
		this.previewLoading.add(key);
		void this.creatureModel(model).then((loaded) => {
			this.previewLoading.delete(key);
			if (!loaded || token !== this.token || !this.rowStillThere(pick)) return;
			const group = new THREE.Group();
			group.userData.preview = true;
			group.userData.shared = true;
			group.position.set(row.x, row.z, row.y);
			group.rotation.y = this.rowYaw(row.heading);
			group.scale.setScalar(Math.max(row.size ?? 50, 1) / 50);
			const scene = loaded.model.scene.clone(true);
			group.add(scene);
			const box = new THREE.Box3().setFromObject(scene);
			const height = Number.isFinite(box.max.y) ? Math.max(box.max.y, 80) : 160;
			const hit = new THREE.Mesh(new THREE.BoxGeometry(70, height, 70), this.hitMaterial);
			hit.position.y = height / 2;
			hit.userData.pick = pick;
			hit.name = `preview-${key}`;
			group.add(hit);
			this.groupFor(pick.table).add(group);
			this.previews.set(key, {
				pick,
				table: pick.table,
				group,
				hit,
				top: height,
				model,
				size: row.size,
				scale: Math.max(row.size ?? 50, 1) / 50
			});
			group.visible = this.previewsVisible;
		});
	}

	private addVolumePreview(pick: RowPick, row: SpawnRow): void {
		const door = pick.table === 'door';
		const width = door ? 110 : 100;
		const height = door ? 160 : 120;
		const depth = door ? 25 : 100;
		const color = door ? 0xef5350 : 0x64b5f6;
		const key = this.keyOf(pick);
		const group = new THREE.Group();
		group.userData.preview = true;
		group.position.set(row.x, row.z, row.y);
		group.rotation.y = this.rowYaw(row.heading);
		const geometry = new THREE.BoxGeometry(width, height, depth);
		const mesh = new THREE.Mesh(
			geometry,
			new THREE.MeshBasicMaterial({
				color,
				transparent: true,
				opacity: 0.12,
				depthWrite: false
			})
		);
		mesh.position.y = height / 2;
		mesh.userData.pick = pick;
		mesh.name = `preview-${key}`;
		group.add(mesh);
		const edges = new THREE.LineSegments(
			new THREE.EdgesGeometry(geometry),
			new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 })
		);
		edges.position.copy(mesh.position);
		group.add(edges);
		this.groupFor(pick.table).add(group);
		this.previews.set(key, {
			pick,
			table: pick.table,
			group,
			hit: mesh,
			top: height
		});
		group.visible = this.previewsVisible;
	}

	private updatePreviewTransform(entry: PreviewEntry, row: SpawnRow): void {
		entry.group.position.set(row.x, row.z, row.y);
		entry.group.rotation.y = this.rowYaw(row.heading);
		if (entry.table === 'mob') {
			entry.scale = Math.max(row.size ?? 50, 1) / 50;
			entry.size = row.size;
			entry.group.scale.setScalar(entry.scale);
		}
	}

	private removePreview(key: string): void {
		const entry = this.previews.get(key);
		if (!entry) return;
		entry.group.parent?.remove(entry.group);
		if (!entry.group.userData.shared) {
			entry.group.traverse((object) => {
				const drawable = object as THREE.Mesh;
				if (!drawable.geometry && !drawable.material) return;
				drawable.geometry?.dispose();
				const material = drawable.material as THREE.Material | THREE.Material[] | undefined;
				if (Array.isArray(material)) material.forEach((item) => item.dispose());
				else material?.dispose();
			});
		}
		this.previews.delete(key);
		const button = this.labelButtons.get(key);
		if (button) {
			button.remove();
			this.labelButtons.delete(key);
			this.labelRows.delete(key);
		}
	}

	private clearPreviews(): void {
		for (const key of [...this.previews.keys()]) this.removePreview(key);
		this.previewLoading.clear();
		this.hideLabels();
	}

	private rowStillThere(pick: RowPick): boolean {
		const cloud = this.rowClouds.find((entry) => entry.table === pick.table);
		return cloud?.rows.some((row) => row.id === pick.id) ?? false;
	}

	private labelHeight(row: SpawnRow, table: SpawnTable): number {
		const entry = this.previews.get(`${table}:${row.id}`);
		return (entry ? entry.top * (entry.scale ?? 1) : table === 'door' ? 230 : 160) + 30;
	}

	/** Nearby names, projected on every tick and capped: labels are a reading
	 * aid, not a second scene graph. */
	private updateLabels(): void {
		if (!this.labelsVisible) {
			this.hideLabels();
			return;
		}
		this.camera.updateMatrixWorld();
		const rect = this.canvas.getBoundingClientRect();
		const layerRect = this.labelLayer.getBoundingClientRect();
		const radius = THREE.MathUtils.clamp(this.orbit.distance * 0.5, 3500, 22000);
		const candidates: Array<{
			table: SpawnTable;
			row: SpawnRow;
			selected: boolean;
			distance: number;
		}> = [];
		for (const cloud of this.rowClouds) {
			if (!cloud.group.visible) continue;
			for (const row of cloud.rows) {
				const selected = this.selection.rows.some(
					(pick) => pick.table === cloud.table && pick.id === row.id
				);
				const distance = Math.hypot(row.x - this.camera.position.x, row.y - this.camera.position.z);
				if (!selected && distance > radius) continue;
				candidates.push({ table: cloud.table, row, selected, distance });
			}
		}
		candidates.sort((a, b) => Number(b.selected) - Number(a.selected) || a.distance - b.distance);
		const shown = new Set<string>();
		const placed: Array<{ x: number; y: number }> = [];
		for (const { table, row, selected } of candidates.slice(0, LABEL_LIMIT)) {
			const key = `${table}:${row.id}`;
			const at = this.screenOf(rect, [row.x, row.z + this.labelHeight(row, table), row.y]);
			if (!at) continue;
			let button = this.labelButtons.get(key);
			if (
				!button &&
				!selected &&
				placed.some((point) => Math.abs(point.x - at.x) < 60 && Math.abs(point.y - at.y) < 14)
			) {
				continue;
			}
			placed.push(at);
			if (!button) {
				button = document.createElement('button');
				button.type = 'button';
				button.className = 'world-label';
				Object.assign(button.style, {
					position: 'absolute',
					left: '0',
					top: '0',
					willChange: 'transform',
					transform: 'translate(-50%, -100%)',
					pointerEvents: 'auto',
					padding: '1px 5px',
					borderRadius: '4px',
					border: '1px solid rgba(232, 238, 242, 0.22)',
					background: 'rgba(15, 20, 24, 0.82)',
					color: '#e8eef2',
					font: '11px/1.5 system-ui, sans-serif',
					whiteSpace: 'nowrap',
					cursor: 'pointer'
				} satisfies Partial<CSSStyleDeclaration>);
				button.addEventListener('click', (event) => {
					event.stopPropagation();
					this.selectRow({ table, id: row.id });
				});
				button.addEventListener('dblclick', (event) => {
					event.stopPropagation();
					this.selectRow({ table, id: row.id });
					this.focusRow({ table, id: row.id });
				});
				this.labelLayer.appendChild(button);
				this.labelButtons.set(key, button);
				this.labelRows.set(key, { table, row });
			}
			button.textContent = row.name || `${table} ${row.id.slice(0, 8)}`;
			this.placeLabel(button, at, layerRect);
			button.style.borderColor = selected ? '#67e8f9' : 'rgba(232, 238, 242, 0.22)';
			button.style.color = selected ? '#67e8f9' : '#e8eef2';
			shown.add(key);
		}
		for (const [key, button] of this.labelButtons) {
			if (shown.has(key)) continue;
			button.remove();
			this.labelButtons.delete(key);
			this.labelRows.delete(key);
		}
	}

	/** Move the existing labels on every frame. `translate3d` keeps the work on
	 * the compositor; `left`/`top` would relayout the page at 60 Hz. */
	private positionLabels(): void {
		if (!this.labelsVisible || !this.labelButtons.size) return;
		this.camera.updateMatrixWorld();
		const rect = this.canvas.getBoundingClientRect();
		const layerRect = this.labelLayer.getBoundingClientRect();
		for (const [key, button] of this.labelButtons) {
			const entry = this.labelRows.get(key);
			if (!entry) continue;
			const at = this.screenOf(rect, [
				entry.row.x,
				entry.row.z + this.labelHeight(entry.row, entry.table),
				entry.row.y
			]);
			if (!at) {
				button.style.display = 'none';
				continue;
			}
			this.placeLabel(button, at, layerRect);
		}
	}

	private placeLabel(
		button: HTMLButtonElement,
		at: { x: number; y: number },
		layerRect: DOMRect
	): void {
		button.style.display = 'block';
		button.style.transform = `translate3d(${at.x - layerRect.left}px, ${at.y - layerRect.top}px, 0) translate(-50%, -100%)`;
	}

	private hideLabels(): void {
		for (const button of this.labelButtons.values()) button.remove();
		this.labelButtons.clear();
		this.labelRows.clear();
	}

	/** A zone point projected for the label layer, or null when it is behind
	 * the camera or outside the viewport. */
	private screenOf(
		rect: DOMRect,
		position: [number, number, number]
	): { x: number; y: number } | null {
		const point = new THREE.Vector3(...position);
		const view = point.clone().applyMatrix4(this.camera.matrixWorldInverse);
		if (view.z > -1) return null;
		const projected = point.project(this.camera);
		if (projected.x < -1.2 || projected.x > 1.2 || projected.y < -1.2 || projected.y > 1.2) {
			return null;
		}
		return {
			x: rect.left + ((projected.x + 1) / 2) * rect.width,
			y: rect.top + ((1 - projected.y) / 2) * rect.height
		};
	}

	/** The creature GLBs carry their sheets in the exporter's own extension,
	 * not in `gltf.materials`; bind them once, before any clone is made. */
	private textureCreature(model: LoadedMesh['model']): void {
		model.scene.traverse((object) => {
			const mesh = object as THREE.Mesh;
			if (mesh.isMesh) mesh.material = this.materialFor(mesh, model);
		});
	}

	private creatureModel(model: number): Promise<LoadedMesh | undefined> {
		const cached = this.creatureCache.get(model);
		if (cached) return cached;
		const promise = fetch(api.worldCreatureUrl(model))
			.then((response) => (response.ok ? response.arrayBuffer() : undefined))
			.then((buffer) => (buffer ? loadFromGlb(buffer, `creature-${model}`) : undefined))
			.then((loaded) => {
				if (!loaded) return undefined;
				this.textureCreature(loaded);
				return { model: loaded };
			})
			.catch((error) => {
				console.warn(`world: creature ${model} preview failed`, error);
				return undefined;
			});
		this.creatureCache.set(model, promise);
		return promise;
	}

	/** Drop the loaded figures and read them again: a client import or a
	 * changed `Model` can leave the browser holding the old bytes. */
	reloadPreviews(): void {
		this.clearPreviews();
		this.creatureCache.clear();
		this.refreshPreviews();
	}

	/** Frame one server row (the entity list's double click). */
	focusRow(pick: RowPick, distance = 1600): void {
		const row = this.rowAt(pick);
		if (!row || !this.terrain) return;
		this.lookAtPoint([row.x, row.z, row.y], distance);
	}

	private clearSpawns(): void {
		// the rows are about to be replaced: a gesture holding them would move
		// objects nothing shows any more (a commit landing mid-drag is enough)
		this.clearPreviews();
		this.gesture = null;
		this.rowClouds = [];
		this.carried.clear();
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

	/** A dungeon's chunks or a city's blocks, from the client's own layout.
	 * They are the zone's architecture: drawn, but never picked or edited,
	 * since the editable list is `fixtures.csv` and neither zone ships one. */
	private async buildArchitecture(
		zoneId: number,
		zone: WorldZone,
		data: TerrainData,
		token: number
	): Promise<void> {
		const blocks = zone.architecture ?? [];
		if (!blocks.length) return;
		const dungeon = zone.architecture_anchor === 'dungeon';
		const fixed = zone.architecture_anchor === 'city' || dungeon;
		const byCatalog = new Map<number, WorldFixture[]>();
		for (const block of blocks) {
			const list = byCatalog.get(block.nif_id);
			if (list) list.push(block);
			else byCatalog.set(block.nif_id, [block]);
		}
		// One dungeon ships a few dozen chunks and the daemon exports them on
		// demand; a small pool keeps the first load from serialising every
		// NIF read while still leaving the daemon room to breathe.
		const queue = (zone.architecture_catalog ?? []).filter(
			(entry) => entry.mesh && byCatalog.get(entry.id)?.length
		);
		const worker = async () => {
			for (;;) {
				const entry = queue.shift();
				if (!entry || token !== this.token) return;
				const fixtures = byCatalog.get(entry.id);
				if (!fixtures?.length) continue;
				let loaded: LoadedMesh | undefined;
				try {
					loaded = await this.loadMesh(zoneId, entry.filename);
				} catch (error) {
					console.warn(`world: architecture mesh ${entry.filename} failed`, error);
					continue;
				}
				if (token !== this.token) return;
				if (!loaded) continue;
				this.instance(
					loaded.model,
					fixtures,
					data,
					this.groups.architecture,
					false,
					dungeon ? 'dungeon' : 'zone'
				);
				if (!fixed) {
					loaded.model.scene.updateMatrixWorld(true);
					this.expandArchitectureBounds(
						new THREE.Box3().setFromObject(loaded.model.scene),
						fixtures
					);
				}
			}
		};
		await Promise.all(Array.from({ length: Math.min(4, queue.length) }, () => worker()));
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

	private instance(
		model: LoadedMesh['model'],
		fixtures: WorldFixture[],
		data: TerrainData,
		group: THREE.Group = this.groups.fixtures,
		pickable = true,
		layout: 'zone' | 'dungeon' = 'zone'
	): void {
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
				// `dungeon.place` stores the NIF depth axis in Y; the viewer's
				// scene (and the zone rows) use -Y there. The client root's 180-
				// degree yaw then puts the child back on the server's plane.
				const position: [number, number, number] =
					layout === 'dungeon'
						? [placement.position[0], placement.position[1], -placement.position[2]]
						: placement.position;
				matrix.compose(
					new THREE.Vector3(...position),
					new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
					new THREE.Vector3(placement.scale, placement.scale, placement.scale)
				);
				matrix.multiply(local);
				instanced.setMatrixAt(i, matrix);
			}
			instanced.instanceMatrix.needsUpdate = true;
			instanced.frustumCulled = false; // the instances span the zone
			instanced.name = `fixtures-${mesh.name || 'mesh'}`;
			group.add(instanced);
			if (pickable) this.picks.push({ mesh: instanced, fixtures, local });
		}
	}

	/** The client-frame bounds of a set of placements (x/y on the plane, z up). */
	private fixtureBounds(fixtures: WorldFixture[]): {
		minX: number;
		minY: number;
		minZ: number;
		maxX: number;
		maxY: number;
		maxZ: number;
	} | null {
		if (!fixtures.length) return null;
		let minX = Infinity;
		let minY = Infinity;
		let minZ = Infinity;
		let maxX = -Infinity;
		let maxY = -Infinity;
		let maxZ = -Infinity;
		for (const fixture of fixtures) {
			minX = Math.min(minX, fixture.x);
			minY = Math.min(minY, fixture.y);
			minZ = Math.min(minZ, fixture.z);
			maxX = Math.max(maxX, fixture.x);
			maxY = Math.max(maxY, fixture.y);
			maxZ = Math.max(maxZ, fixture.z);
		}
		return { minX, minY, minZ, maxX, maxY, maxZ };
	}

	/** Grow the architecture's plane bounds with one mesh's placed extent.
	 *
	 * The box is the loaded model's own scene box; a fixture's yaw and scale
	 * turn and scale it, then the placement moves it. Only x/y are grown: the
	 * z anchor stays the placement's own z, which is the client's authored
	 * floor height (`dungeon.place`), not a roof or a pit. */
	private expandArchitectureBounds(box: THREE.Box3, fixtures: WorldFixture[]): void {
		if (box.isEmpty() || !this.terrain || !fixtures.length) return;
		const bounds = this.architectureBounds ?? {
			minX: Infinity,
			minY: Infinity,
			minZ: Infinity,
			maxX: -Infinity,
			maxY: -Infinity,
			maxZ: -Infinity
		};
		const corners: [number, number][] = [];
		for (const x of [box.min.x, box.max.x]) {
			for (const z of [box.min.z, box.max.z]) {
				corners.push([x, z]);
			}
		}
		for (const fixture of fixtures) {
			const placement = fixturePlacement(this.terrain, fixture);
			const cos = Math.cos(placement.yaw);
			const sin = Math.sin(placement.yaw);
			for (const [x, z] of corners) {
				const px = x * placement.scale;
				const pz = z * placement.scale;
				const sceneX = placement.position[0] + px * cos + pz * sin;
				const sceneZ = placement.position[2] - px * sin + pz * cos;
				bounds.minX = Math.min(bounds.minX, sceneX);
				bounds.maxX = Math.max(bounds.maxX, sceneX);
				bounds.minY = Math.min(bounds.minY, sceneZ);
				bounds.maxY = Math.max(bounds.maxY, sceneZ);
			}
		}
		this.architectureBounds = bounds;
	}

	/** Put a dungeon's local layout on the server rows.
	 *
	 * `dungeon.place` is the client's own arrangement around its origin; the
	 * rows are the server's positions in zone space. The layout is centred on
	 * the rows rather than corner-aligned, so a room or a row at the edge does
	 * not decide where the whole dungeon stands. The group's x mirror (set in
	 * `show`) means the local minimum becomes the mirrored maximum, so the
	 * centres coincide when the offset is `row centre + local centre`. A city
	 * never comes here: its frame is the client's fixed one.
	 */
	private alignArchitecture(spawns: WorldSpawns): boolean {
		const bounds = this.architectureBounds;
		if (!bounds) return false;
		const points = [...spawns.mobs, ...spawns.objects, ...spawns.doors];
		if (!points.length) return false;
		let minX = Infinity;
		let minY = Infinity;
		let minZ = Infinity;
		let maxX = -Infinity;
		let maxY = -Infinity;
		let maxZ = -Infinity;
		for (const point of points) {
			minX = Math.min(minX, point.x);
			minY = Math.min(minY, point.y);
			minZ = Math.min(minZ, point.z);
			maxX = Math.max(maxX, point.x);
			maxY = Math.max(maxY, point.y);
			maxZ = Math.max(maxZ, point.z);
		}
		// A dungeon's placement z is the authored floor, so its centre over the
		// rows is the datum. Cities never take this path (fixed client origin).
		const height = (minZ + maxZ - bounds.minZ - bounds.maxZ) / 2;
		this.groups.architecture.position.set(
			(minX + maxX + bounds.minX + bounds.maxX) / 2,
			height,
			(minY + maxY - bounds.minY - bounds.maxY) / 2
		);
		return true;
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
		this.lookAtPoint([x, z, y], distance);
	}

	/** Move the camera to a point and keep the zoom (the panel's "go to"). */
	focusPoint(x: number, y: number): void {
		this.focus(x, y, this.orbit.distance);
	}

	/** Stand on a circle around a zone-local point and look at it: azimuth 0 is
	 * the scene's +Z — the client's *south* (its +y is scene +Z) — 90 is east,
	 * 180 north. The screenshot harness uses it to see a structure from a named
	 * side; north stands on a north-up view, framing it as the game does. */
	lookFrom(x: number, y: number, azimuthDeg: number, distance = ZONE_UNIT * 0.45): void {
		if (!this.terrain) return;
		const z = this.terrain.heightAt(x, y);
		this.target.set(x, z, y);
		this.orbit.distance = distance;
		this.orbit.azimuth = (azimuthDeg * Math.PI) / 180;
		this.orbit.elevation = 0.38;
		this.applyOrbit();
	}

	private frameRows(spawns: WorldSpawns): void {
		const points = [...spawns.mobs, ...spawns.objects, ...spawns.doors];
		if (!points.length) return;
		let minX = Infinity;
		let minY = Infinity;
		let minZ = Infinity;
		let maxX = -Infinity;
		let maxY = -Infinity;
		let maxZ = -Infinity;
		for (const point of points) {
			minX = Math.min(minX, point.x);
			minY = Math.min(minY, point.y);
			minZ = Math.min(minZ, point.z);
			maxX = Math.max(maxX, point.x);
			maxY = Math.max(maxY, point.y);
			maxZ = Math.max(maxZ, point.z);
		}
		const span = Math.max(maxX - minX, maxY - minY, 2000);
		this.target.set((minX + maxX) / 2, (minZ + maxZ) / 2, (minY + maxY) / 2);
		this.orbit.azimuth = 0.6;
		this.orbit.elevation = 0.45;
		this.orbit.distance = Math.max(span * 1.1, 3000);
		this.applyOrbit();
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
		this.target.copy(center);
		this.orbit.distance = ZONE_SIZE * 0.95;
		this.orbit.azimuth = 0;
		this.orbit.elevation = 0.62;
		this.applyOrbit();
	}
}
