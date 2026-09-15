// Typed client for the `camelot serve` daemon (API v1).
//
// Base resolution: served from the daemon itself (same origin, base = ''),
// or pointed at it from the dev/static build via `?api=http://localhost:8760`
// (persisted in localStorage).

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
	tracks: AnimTrack[];
}

export interface AssetQuery {
	type?: string;
	q?: string;
	dir?: string;
	sort?: string;
	page?: number;
	limit?: number;
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
	characterModelUrl: (raceId: number, female: boolean, appearance: Record<string, number>) => {
		const qs = new URLSearchParams();
		for (const [k, v] of Object.entries(appearance)) qs.set(k, String(v));
		return `${getApiBase()}/api/v1/character/${raceId}/${female ? 'true' : 'false'}/model?${qs}`;
	},
	anim: async (id: string, signal?: AbortSignal): Promise<AnimClip | null> => {
		const res = await fetch(`${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/anim`, {
			signal
		});
		if (res.status === 404) return null;
		if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
		return (await res.json()) as AnimClip;
	},
	events: () => new EventSource(`${getApiBase()}/api/v1/events`)
};
