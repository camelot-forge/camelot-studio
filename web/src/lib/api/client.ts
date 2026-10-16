// Typed client for the `camelot serve` daemon (API v1).
//
// Base resolution: served from the daemon itself (same origin, base = ''),
// or pointed at it from the dev/static build via `?api=http://localhost:8760`
// (persisted in localStorage).

import type { AnimStateSlot } from '$lib/character';

export interface Health {
	status: string;
	version: string;
	root: string;
	build: string;
}

export interface RootInfo {
	root: string;
	total: number;
	archives: number;
	errors: number;
	scanned_at: number;
	counts: Record<string, number>;
	dirs: Record<string, number>;
}

export interface AssetSummary {
	id: string;
	type: string;
	name: string;
	path: string;
	origin: string;
	size: number;
	mtime: number;
	archive: string | null;
	entry: string | null;
}

export interface AssetDetail extends AssetSummary {
	metadata: Record<string, unknown>;
}

export interface CharacterOptions {
	race: string;
	race_id: number;
	female: boolean;
	faces: number;
	hair_styles: number;
	hair_colors: number;
	eye_colors: number;
	skin_tones: number;
	has_decal: boolean;
	cloak_variants: number;
	materials: string[];
	material: string;
}

export interface AssetPage {
	items: AssetSummary[];
	total: number;
	page: number;
	limit: number;
}

export interface AnimTrack {
	node: string;
	times: number[];
	quats: Array<[number, number, number, number]>;
	ptimes?: number[];
	positions?: Array<[number, number, number]>;
}

export interface AnimClip {
	kfa: string;
	duration: number; // loop length, game seconds (animnifs frames/fps)
	rate: number; // game seconds -> KFA key seconds multiplier
	// The client's own `animnifs` type: `loop` clips wrap, `clamp` ones are
	// one-shots. Absent when the clip came from the idle resolver, which does
	// not consult a slot row.
	loop?: boolean;
	tracks: AnimTrack[];
}

/** One playable state of a figure, as the client's `anims.csv` declares it:
 * the slot name the game uses (`Idle`, `Walk`, `Fly`…), the clip it points at,
 * and the `loop`/`clamp` flag the viewer must play it with. */
export interface AnimStateSummary {
	slot: string;
	kfa: string;
	animnifs_id: string;
	loop: boolean;
	duration: number; // game seconds (frames / fps), not the file's key span
}

export interface AssetQuery {
	type?: string;
	q?: string;
	dir?: string;
	sort?: string;
	page?: number;
	limit?: number;
}

// --- Home, the front door (P2 slice 2a, `notes/home.md`) --------------

export interface HomeDaemon {
	version: string;
	build: string;
	host: string;
	port: number;
	client: string;
	zones: number;
}

/** The project this daemon serves — `pending` counts what a save would still
 * write, the way the panel counts it. */
export interface HomeProject {
	path: string;
	name: string;
	pending: Record<string, number>;
	read_only: boolean;
	holder: string;
}

/** One row of the machine's list: the entry, plus what the disk says *now*. */
export interface HomeRecent {
	path: string;
	name: string;
	opened: string;
	exists: boolean;
	project: boolean;
}

export interface HomePayload {
	daemon: HomeDaemon;
	project: HomeProject | null;
	recents: HomeRecent[];
	recent_error: string;
}

export interface HomeHealth {
	path: string;
	name: string;
	state: 'ok' | 'changed' | 'missing' | 'no-client' | string;
	detail: string;
	findings: Array<Record<string, unknown>>;
}

export interface HomeBrowse {
	path: string;
	parent: string | null;
	dirs: Array<{ name: string; path: string; project: boolean }>;
}

// --- the world (Phase 6.2) ----------------------------------------------------

export interface WorldSearchHit {
	zone: number;
	zone_name: string;
	id: number;
	name: string;
	nif_id: number;
	filename: string;
	x: number;
	y: number;
	z: number;
	ground: boolean;
}

/** A region-wide fixture search (C1): the matches, and how many zones the
 * daemon actually read to answer. */
export interface WorldSearch {
	query: string;
	region: number | null;
	scanned: number;
	limit: number;
	matches: WorldSearchHit[];
}

export interface WorldZoneSummary {
	id: number;
	name: string;
	region: number;
	kind: string;
	enabled: boolean;
	music: number | null;
	world_rect: [number, number, number, number];
	cells: [number, number, number, number];
	available: boolean;
}

export interface WorldRegionSummary {
	id: number;
	zones: number[];
	available: number;
	settings: Record<string, string>;
}

export interface WorldIndex {
	zones: WorldZoneSummary[];
	regions: WorldRegionSummary[];
}

export interface WorldTerrain {
	width: number;
	height: number;
	terrain_factor: number;
	offset_factor: number;
	url: string;
}

export interface WorldFixture {
	id: number;
	nif_id: number;
	name: string;
	x: number;
	y: number;
	z: number;
	angle: number;
	scale: number;
	radius: number;
	collide: number;
	animate: boolean;
	ground: boolean;
	flip: boolean;
	cave: boolean;
	unique_id: number;
}

export interface WorldCatalogEntry {
	id: number;
	name: string;
	filename: string;
	ground: boolean;
	radius: number;
	min_scale: number;
	max_scale: number;
	min_angle: number;
	max_angle: number;
	mesh: boolean;
}

export interface WorldBound {
	id: number;
	points: Array<[number, number]>;
}

export interface WorldWater {
	name: string;
	kind: string;
	texture: string;
	height: number;
	flow: number;
	left: Array<[number, number]>;
	right: Array<[number, number]>;
}

export interface WorldJump {
	name: string;
	x: number;
	y: number;
	z: number;
	angle: number;
}

export interface WorldTextureLayer {
	patch_x: number;
	patch_y: number;
	texture: string;
	rotate: number;
	u: number;
	v: number;
	u_scale: number;
	v_scale: number;
	visible: boolean;
	tiles: number;
	tileable: boolean;
}

export interface WorldPatches {
	layers: WorldTextureLayer[];
	masks: Record<string, string[]>;
	textures: string[];
	available: string[];
}

export interface WorldArchive {
	path: string;
	entries: string[];
}

export interface WorldZone {
	info: WorldZoneSummary;
	urls: Record<string, string>;
	terrain: WorldTerrain | null;
	fixtures: WorldFixture[];
	catalog: WorldCatalogEntry[];
	bounds: WorldBound[];
	water: WorldWater[];
	jumps: WorldJump[];
	zonejumps: string[][];
	patches: WorldPatches | null;
	archives: Record<string, WorldArchive>;
	warnings: string[];
}

// --- the server's rows (Phase 6.4's read half) --------------------------------
// Field names are the server's own columns, lowercased (see the daemon's
// `serverrows.py`): no client-side vocabulary is invented for them.

export interface WorldSpawnMob {
	id: string;
	class_type: string;
	name: string;
	suffix: string;
	guild: string;
	x: number;
	y: number;
	z: number;
	heading: number;
	level: number;
	realm: number;
	model: number;
	size: number;
	gender: number;
	flags: number;
	aggro_level: number;
	aggro_range: number;
	path: string | null;
	npc_template: number;
}

export interface WorldSpawnObject {
	id: string;
	class_type: string;
	name: string;
	x: number;
	y: number;
	z: number;
	heading: number;
	model: number;
	emblem: number;
	realm: number;
}

export interface WorldSpawnDoor {
	id: string;
	name: string;
	type: number;
	x: number;
	y: number;
	z: number;
	heading: number;
	level: number;
	realm: number;
	is_postern: boolean;
	locked: number;
}

export interface WorldSpawnPoint {
	step: number;
	x: number;
	y: number;
	z: number;
	wait: number;
	speed: number;
}

export interface WorldSpawnPath {
	name: string;
	path_type: number;
	points: WorldSpawnPoint[];
}

export interface WorldSpawns {
	source: string;
	counts: Record<string, number>;
	mobs: WorldSpawnMob[];
	objects: WorldSpawnObject[];
	doors: WorldSpawnDoor[];
	paths: WorldSpawnPath[];
	warnings: string[];
}

/** One server row as the viewer tracks it: a position, an id, and whatever
 * the daemon sent (the full row lives in the page's payload). */
export interface SpawnRow {
	id: string;
	x: number;
	y: number;
	z: number;
	name?: string;
	heading?: number;
}

export type SpawnTable = 'mob' | 'worldobject' | 'door';

// --- the editor (Phase 6.3) ---------------------------------------------------
// D2: the daemon writes, the browser consumes. `op` is the request op; the
// daemon resolves it into a `gamedata` row op and journals it.

export interface EditorZone {
	id: number;
	ops: Record<string, unknown>[];
}

export interface EditorPack {
	pack: string;
	modified: number;
	size: number;
}

export interface EditorRowOp {
	zone: number;
	patches: Record<string, unknown>[];
}

/** What the *client* says about the session's own half: the client records
 * what it carries, so `revert` cannot touch it — `restore` does. */
export interface EditorClient {
	mod: string;
	installed: boolean;
	version: string | null;
	claims: string[];
}

export interface EditorState {
	available: boolean;
	dir: string | null;
	mod: Record<string, unknown>;
	zones: EditorZone[];
	rows: EditorRowOp[];
	/** What a save would still write, counted by the daemon (`pending_counts`):
	 * `fixtures` = distinct fixture ids, `rows` = distinct `table:key` the
	 * database has not been given. One definition, one number. */
	pending: Record<string, number>;
	/** Set when another process holds the checkout: every write refuses, and
	 * `holder` names who to close or stop (`project.md` §D13). */
	read_only: boolean;
	holder: string;
	pack: EditorPack | null;
	client: EditorClient | null;
	/** What was last published: the op count at that moment (the panel's own
	 * "is the session ahead of its client half?" test). */
	published: Record<string, unknown>;
}

export interface EditorEdit {
	op: Record<string, unknown>;
	fixture: WorldFixture | null;
	patches: Record<string, unknown>[];
}

export interface EditorBuild {
	pack: string | null;
	entries: Record<string, unknown>[];
	files: string[];
	report: Record<string, unknown>;
	install: Record<string, unknown> | null;
	patches: number;
}

/** What a full restore took back: the client half that *was* installed, the
 * server rows put back, and the session as it stands after. */
export interface EditorRestore {
	client: EditorClient;
	rows: number;
	state: EditorState;
}

const BASE_KEY = 'camelot.apiBase';

function normalize(base: string): string {
	return base.replace(/\/+$/, '');
}

export function getApiBase(): string {
	if (typeof window === 'undefined') return '';
	const fromQuery = new URLSearchParams(window.location.search).get('api');
	if (fromQuery !== null) {
		const base = normalize(fromQuery);
		localStorage.setItem(BASE_KEY, base);
		return base;
	}
	return normalize(localStorage.getItem(BASE_KEY) ?? '');
}

export function setApiBase(base: string): void {
	localStorage.setItem(BASE_KEY, normalize(base));
}

export function clearApiBase(): void {
	localStorage.removeItem(BASE_KEY);
}

function queryString(params: Record<string, string | number | undefined>): string {
	const qs = new URLSearchParams();
	for (const [key, value] of Object.entries(params)) {
		if (value !== undefined && value !== '') qs.set(key, String(value));
	}
	const s = qs.toString();
	return s ? `?${s}` : '';
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
	const res = await fetch(`${getApiBase()}/api/v1${path}`, { signal });
	if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
	return (await res.json()) as T;
}

/** The daemon's own words for a failure, falling back to the status line. */
async function detailOf(res: Response): Promise<string> {
	let detail = `${res.status} ${res.statusText}`;
	try {
		const payload = (await res.json()) as { detail?: unknown };
		if (typeof payload.detail === 'string') detail = payload.detail;
	} catch {
		// not JSON: the status line is all there is
	}
	return detail;
}

/** A GET for the routes that *refuse with a reason* (Home's health and browse:
 * "not a project: …", "no such directory"): the operator reads the daemon's
 * own sentence, not a status code. */
async function getDetailed<T>(path: string, signal?: AbortSignal): Promise<T> {
	const res = await fetch(`${getApiBase()}/api/v1${path}`, { signal });
	if (!res.ok) throw new Error(await detailOf(res));
	return (await res.json()) as T;
}

/** A JSON request that carries the daemon's own refusal message, not a code. */
async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
	const res = await fetch(`${getApiBase()}/api/v1${path}`, {
		method,
		headers: body === undefined ? undefined : { 'content-type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (!res.ok) throw new Error(await detailOf(res));
	return (await res.json()) as T;
}

export const api = {
	base: getApiBase,
	health: (signal?: AbortSignal) => get<Health>('/health', signal),
	root: (signal?: AbortSignal) => get<RootInfo>('/root', signal),
	assets: (params: AssetQuery = {}, signal?: AbortSignal) =>
		get<AssetPage>(`/assets${queryString({ ...params })}`, signal),
	detail: (id: string, signal?: AbortSignal) =>
		get<AssetDetail>(`/assets/${encodeURIComponent(id)}`, signal),
	modelUrl: (id: string, skinned = false) =>
		`${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/model${skinned ? '?skinned=true' : ''}`,
	skinUrl: (id: string) => `${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/skin`,
	animUrl: (id: string) => `${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/anim`,
	characterOptions: (raceId: number, female: boolean, signal?: AbortSignal) =>
		get<CharacterOptions>(`/character/${raceId}/${female ? 'true' : 'false'}/options`, signal),
	characterTextureUrl: (
		raceId: number,
		female: boolean,
		appearance: Record<string, string | number>
	) => {
		const qs = new URLSearchParams();
		for (const [k, v] of Object.entries(appearance)) qs.set(k, String(v));
		return `${getApiBase()}/api/v1/character/${raceId}/${female ? 'true' : 'false'}/texture?${qs}`;
	},
	characterModelUrl: (
		raceId: number,
		female: boolean,
		appearance: Record<string, string | number | undefined>,
		skinned = true
	) => {
		const qs = new URLSearchParams();
		for (const [k, v] of Object.entries(appearance)) {
			if (v !== undefined) qs.set(k, String(v));
		}
		if (skinned) qs.set('skinned', 'true');
		return `${getApiBase()}/api/v1/character/${raceId}/${female ? 'true' : 'false'}/model?${qs}`;
	},
	characterAnim: async (
		raceId: number,
		female: boolean,
		state?: string,
		signal?: AbortSignal
	): Promise<AnimClip | null> => {
		const qs = state ? `?state=${encodeURIComponent(state)}` : '';
		const res = await fetch(
			`${getApiBase()}/api/v1/character/${raceId}/${female ? 'true' : 'false'}/anim${qs}`,
			{ signal }
		);
		if (res.status === 404) return null;
		if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
		return (await res.json()) as AnimClip;
	},
	characterStates: async (
		raceId: number,
		female: boolean,
		signal?: AbortSignal
	): Promise<AnimStateSlot[]> => {
		const res = await fetch(
			`${getApiBase()}/api/v1/character/${raceId}/${female ? 'true' : 'false'}/states`,
			{ signal }
		);
		if (res.status === 404) return [];
		if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
		return (await res.json()) as AnimStateSlot[];
	},
	anim: async (id: string, state?: string, signal?: AbortSignal): Promise<AnimClip | null> => {
		const qs = state ? `?state=${encodeURIComponent(state)}` : '';
		const res = await fetch(`${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/anim${qs}`, {
			signal
		});
		if (res.status === 404) return null;
		if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
		return (await res.json()) as AnimClip;
	},
	/** One named state of a figure (`/assets/{id}/states`). A refusal — the
	 * daemon answering 404 with its reason, such as a clip whose channels do not
	 * bind to this rig — throws, so the row can say why instead of going quiet. */
	animState: async (id: string, slot: string, signal?: AbortSignal): Promise<AnimClip> => {
		const res = await fetch(
			`${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/anim?state=${encodeURIComponent(slot)}`,
			{ signal }
		);
		if (!res.ok) throw new Error(await detailOf(res));
		return (await res.json()) as AnimClip;
	},
	/** The figure's own anim set, read from the client's tables. An empty list
	 * means the tables do not cover this figure: the viewer keeps the idle its
	 * resolver picks. */
	states: async (id: string, signal?: AbortSignal): Promise<AnimStateSummary[]> => {
		const res = await fetch(`${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/states`, {
			signal
		});
		if (res.status === 404) return [];
		if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
		return (await res.json()) as AnimStateSummary[];
	},
	events: () => new EventSource(`${getApiBase()}/api/v1/events`),

	// --- home (P2 slice 2a) ------------------------------------------------
	home: (signal?: AbortSignal) => get<HomePayload>('/home', signal),
	homeHealth: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeHealth>(`/home/health?path=${encodeURIComponent(path)}`, signal),
	homeBrowse: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeBrowse>(`/home/browse?path=${encodeURIComponent(path)}`, signal),

	// --- the world ---------------------------------------------------------
	worldIndex: (signal?: AbortSignal) => get<WorldIndex>('/world/index', signal),
	worldZone: (zoneId: number, signal?: AbortSignal) =>
		get<WorldZone>(`/world/zone/${zoneId}`, signal),
	worldSpawns: (zoneId: number, signal?: AbortSignal) =>
		get<WorldSpawns>(`/world/zone/${zoneId}/spawns`, signal),
	worldSearch: (q: string, region?: number | null, limit?: number) =>
		get<WorldSearch>(
			`/world/search?q=${encodeURIComponent(q)}${region != null ? `&region=${region}` : ''}${limit ? `&limit=${limit}` : ''}`
		),
	worldTerrainUrl: (zoneId: number) => `${getApiBase()}/api/v1/world/zone/${zoneId}/terrain`,
	worldMeshUrl: (zoneId: number, name: string) =>
		`${getApiBase()}/api/v1/world/zone/${zoneId}/mesh/${encodeURIComponent(name)}`,
	worldTextureUrl: (zoneId: number, logical: string, entry: string) =>
		`${getApiBase()}/api/v1/world/zone/${zoneId}/texture/${encodeURIComponent(logical)}/${encodeURIComponent(entry)}`,
	worldTerrainTexUrl: (name: string) =>
		`${getApiBase()}/api/v1/world/terraintex/${encodeURIComponent(name)}`,

	// --- the editor (6.3) --------------------------------------------------
	worldEdit: (signal?: AbortSignal) => get<EditorState>('/world/edit', signal),
	worldEditApply: (zoneId: number, op: Record<string, unknown>) =>
		send<EditorEdit>('POST', `/world/zone/${zoneId}/edits`, op),
	worldEditUndo: (zoneId: number) => send<EditorEdit>('DELETE', `/world/zone/${zoneId}/edits`),
	worldModBuild: () => send<EditorBuild>('POST', '/world/mod/build'),
	worldModApply: () => send<EditorBuild>('POST', '/world/mod/apply'),
	worldModRevert: () => send<EditorState>('POST', '/world/mod/revert'),
	worldModRestore: () => send<EditorRestore>('POST', '/world/mod/restore')
};
