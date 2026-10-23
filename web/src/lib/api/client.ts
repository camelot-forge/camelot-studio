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
	/** `null` when the daemon started without a client (the front door). */
	client: string | null;
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
	/** What a publish would still write, read from the project (`home.md` §D1) —
	 * what the chip's menu shows beside the name. */
	pending: number;
}

export interface HomePayload {
	daemon: HomeDaemon;
	project: HomeProject | null;
	recents: HomeRecent[];
	recent_error: string;
}

/** One environment, as the Deploy page reads it (`pipeline.md` §6). */
export interface DeployEnvironment {
	name: string;
	root: string | null;
	exists: boolean;
	live: { release: number; at?: string; from?: string | null } | null;
	client: { path: string; exists: boolean } | null;
	problem: string | null;
}

/** One cut, as the page lists it: what it holds, where it is live, its reload. */
export interface DeployRelease {
	number: number;
	cut_at: string | null;
	families: Record<string, number>;
	order: string;
	needs: { reload?: string[]; restart?: boolean; manual?: string[] };
	server: { sha256: string | null; pack?: string | null };
	client: { version: number | null; patchlist?: string | null };
	live_in: string[];
}

/** What the players' launcher can fetch (C §D4), read from the project. */
export interface DeployFeed {
	path: string;
	present: boolean;
	version: number | string | null;
	files: number;
	bytes: number;
	published: number;
	published_bytes: number;
	release: number | null;
	base_url: string;
}

/** The feed build's progress (`F §D4`): the page polls it after Publish. */
export interface DeployPublish {
	running: boolean;
	stage: string;
	done: number;
	total: number;
	error: string | null;
	version: number | null;
	path: string | null;
}

/** The shard's public addresses, which live in `project.json` (tracked). */
export interface DeployShard {
	feed_url?: string;
	game_server?: string;
}

/** Which key signs this machine's releases; `null` means unsigned. */
export interface DeploySigning {
	key: string | null;
}

/** `project publish` from the page: feed, signature, deploy branch, push. */
export interface DeployShip {
	running: boolean;
	stage: string;
	error: string | null;
	release: number | null;
	commit: string | null;
	pushed: string | null;
	signed: string | null;
}

/** One line of `deploy/log.jsonl` — the record of an attempt. */
export interface DeployLogLine {
	kind: string;
	env?: string;
	release?: number;
	number?: number;
	outcome?: string;
	at?: string;
	from?: string | null;
	wrote?: string[];
	items?: Record<string, number>;
	families?: Record<string, number>;
}

/** One row of the dry run (`pipeline.md` §3): what, ours, theirs, verdict, why. */
export interface DeployItem {
	family: string;
	what: string;
	ours: string | null;
	theirs: string | null;
	size: number | null;
	verdict: string;
	why: string;
	source?: string;
}

/** The dry run itself: the deploy without the writes. */
export interface DeployPlan {
	format: string;
	project: string;
	release: number;
	env: string;
	root: string;
	order: string;
	items: DeployItem[];
	counts: Record<string, number>;
	needs: { reload?: string[]; restart?: boolean; manual?: string[] };
	ids: { checked: number; collisions: string[] };
	evidence: { path: string; present: boolean };
	refusals: string[];
}

/** The plan executed: the outcome, the files written, the log line. */
export interface DeployOutcome {
	plan: DeployPlan;
	outcome: string;
	wrote: string[];
	fact: DeployLogLine;
}

/** One environment read back (`pipeline.md` §D11). */
export interface DeployVerify {
	env: string;
	root: string;
	release: number | null;
	server: { release: number | null; files: { what: string; ok: boolean }[] };
	client: { ok: boolean | null; problems: string[]; installs: string[] };
	problems: string[];
	ok: boolean;
}

/** What the page asks the pipeline: which release, which environment. */
export interface DeployAsk {
	release: number;
	env: string;
	adopt?: boolean;
	source?: string;
}

/** A cut, as the page learns it back (`pipeline.md` §2). */
export interface DeployCut {
	number: number;
	cut_at: string;
	families: Record<string, number>;
	order: string;
	needs: { reload?: string[]; restart?: boolean };
}

export interface DeployPage {
	project: string;
	root: string;
	read_only: boolean;
	pending?: { fixtures: number; rows: number };
	environments: DeployEnvironment[];
	releases: DeployRelease[];
	feed: DeployFeed;
	shard: DeployShard;
	signing: DeploySigning;
	/** `deploy/log.jsonl` has a line HEAD does not carry yet. */
	log_dirty: boolean;
	publish: DeployPublish;
	ship: DeployShip;
	log: DeployLogLine[];
}

export interface HomeHealth {
	path: string;
	name: string;
	state: 'ok' | 'changed' | 'missing' | 'pinned' | 'no-client' | string;
	detail: string;
	findings: Array<Record<string, unknown>>;
	/** How many of the findings the operator chose to keep ours (P8). */
	pinned: number;
	/** `{fixtures, rows}` — the work waiting, read from the project (`home.md` §D1). */
	pending: Record<string, number>;
}

/** One resource the base moved, and the ops that write it (`project.md` §D17). */
export interface HomeRebaseResource {
	resource: string;
	state: 'moved' | 'missing' | 'pinned' | string;
	ours: string;
	theirs: string | null;
	detail: string;
}

export interface HomeRebaseOp {
	key: string;
	where: string;
	row: number | string | null;
	verdict: 'holds' | 'shows' | 'conflict' | string;
	detail: string;
}

export interface HomeRebaseEntry {
	resource: string;
	ops: HomeRebaseOp[];
	conflicts: number;
	unwalked: number;
	note: string;
}

/** The re-base view, and — after a POST — the decision it wrote. */
export interface HomeRebase {
	path: string;
	name: string;
	client: string;
	clean: boolean;
	resources: HomeRebaseResource[];
	journal: HomeRebaseEntry[];
	/** Zones the journal writes whose table `project.json` never fingerprinted. */
	unwatched: Array<{ zone: number; resource: string; ops: number }>;
	moved: number;
	missing: number;
	pinned: number;
	conflicts: number;
	decision: '' | 'keep-ours' | 'adopt' | string;
	written: number;
}

export interface HomeBrowse {
	path: string;
	parent: string | null;
	dirs: Array<{ name: string; path: string; project: boolean }>;
}

/** One folder, checked as a DAoC client before anything is written (`home.md`
 * §D4's modal). */
export interface HomeClient {
	path: string;
	state: 'ok' | 'missing' | 'not-a-client' | string;
	detail: string;
	build: string;
	zones: number;
}

/** What a switch left behind (`home.md` §D3.1): a line Home shows, not a wall. */
export interface HomeLeft {
	path: string;
	name: string;
	pending: number;
}

/** What `POST /home/forget` answers (`home.md` §D1): the entry went (or was
 * not there), and the list as it stands. */
export interface HomeForgotten {
	path: string;
	forgotten: boolean;
	recents: HomeRecent[];
}

/** What one door did (`home.md` §D4) — and, when the daemon switched, the
 * editor state it switched to. */
export interface HomeDoor {
	path: string;
	name: string;
	opened: boolean;
	resources: number;
	zones: number;
	state: EditorState | null;
	left: HomeLeft | null;
	warning: string;
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
	/** The project's own name (`project.json`), else the folder's. */
	name: string;
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
	return readJson<T>(res);
}

/** `res.json()`, with the daemon's own refusal when there is one — and a plain
 * sentence when the answer is not JSON at all. A page pointed at another
 * process (a daemon older than this front, a dev server without its proxy)
 * gets an HTML page where it expected JSON, and the browser's own "JSON.parse:
 * unexpected character" says nothing about why (measured 2026-10-17). The
 * request's own URL is in the sentence: it is the one fact that says which
 * port the page is really talking to. */
export async function readJson<T>(res: Response): Promise<T> {
	if (!res.ok) throw new Error(await detailOf(res));
	const type = res.headers.get('content-type') ?? '';
	if (!type.includes('json')) {
		throw new Error(
			`the answer from ${res.url || 'the daemon'} is not JSON ` +
				`(${type || 'no content type'}) — is this the daemon's own port?`
		);
	}
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
	return readJson<T>(res);
}

/** A JSON request that carries the daemon's own refusal message, not a code. */
async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
	const res = await fetch(`${getApiBase()}/api/v1${path}`, {
		method,
		headers: body === undefined ? undefined : { 'content-type': 'application/json' },
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	return readJson<T>(res);
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
		return readJson<AnimClip>(res);
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

	// --- home (P2) -----------------------------------------------------------
	home: (signal?: AbortSignal) => get<HomePayload>('/home', signal),
	homeHealth: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeHealth>(`/home/health?path=${encodeURIComponent(path)}`, signal),
	/** P8's view: what moved, and the journal against the base as it stands.
	 * Read-only — the decision is `homeRebaseDecide`. */
	homeRebase: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeRebase>(`/home/rebase?path=${encodeURIComponent(path)}`, signal),
	/** The two decisions (`project.md` §D17): `keep-ours` pins the recorded
	 * fingerprints, `adopt` moves them to what the client holds now. */
	homeRebaseDecide: (path: string, decision: 'keep-ours' | 'adopt') =>
		send<HomeRebase>('POST', '/home/rebase', { path, decision }),
	homeBrowse: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeBrowse>(`/home/browse?path=${encodeURIComponent(path)}`, signal),
	/** The create modal's own check, before anything is written. */
	homeClient: (path: string, signal?: AbortSignal) =>
		getDetailed<HomeClient>(`/home/client?path=${encodeURIComponent(path)}`, signal),
	/** D3's switch: another project is served, and the answer says what the one
	 * being left still holds. It warns; only an unreadable project is refused. */
	homeOpen: (path: string) => send<HomeDoor>('POST', '/home/open', { path }),
	homeNew: (body: {
		name: string;
		dir: string;
		client?: string;
		/** The environment's `serverconfig.xml`: the viewer's rows and database. */
		db_config?: string;
		staging?: boolean;
		open?: boolean;
	}) => send<HomeDoor>('POST', '/home/new', body),
	homeImport: (body: {
		dir: string;
		name?: string;
		client?: string;
		git?: boolean;
		open?: boolean;
	}) => send<HomeDoor>('POST', '/home/import', body),
	homeClone: (body: { url: string; dir: string; client?: string; open?: boolean }) =>
		send<HomeDoor>('POST', '/home/clone', body),
	/** Drop one entry from the machine's list. The folder itself is never
	 * touched (`home.md` §D1: a list, not a record). */
	homeForget: (path: string) => send<HomeForgotten>('POST', '/home/forget', { path }),

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
	worldModRestore: () => send<EditorRestore>('POST', '/world/mod/restore'),

	// --- the pipeline (P4) -------------------------------------------------
	deploy: (signal?: AbortSignal) => get<DeployPage>('/deploy', signal),
	deployPlan: (ask: DeployAsk) => send<DeployPlan>('POST', '/deploy/plan', ask),
	deployPush: (ask: DeployAsk) => send<DeployOutcome>('POST', '/deploy/deploy', ask),
	deployCut: () => send<DeployCut>('POST', '/deploy/cut'),
	deployVerify: (env: string) => send<DeployVerify>('POST', '/deploy/verify', { env }),
	deployPublish: () => send<{ progress: DeployPublish }>('POST', '/deploy/publish'),
	deployPublishProgress: () => get<{ progress: DeployPublish }>('/deploy/publish'),
	deployShard: (ask: DeployShard) => send<DeployShard>('POST', '/deploy/shard', ask),
	deployShip: (ask: { release?: number | null; remote?: string }) =>
		send<{ progress: DeployShip }>('POST', '/deploy/ship', ask),
	deployShipProgress: () => get<{ progress: DeployShip }>('/deploy/ship'),
	deployCommitLog: () =>
		send<{ commit: string; dirty: boolean }>('POST', '/deploy/commit-log')
};
