// Typed client for the `camelot serve` daemon (API v1).
//
// Base resolution: served from the daemon itself (same origin, base = ''),
// or pointed at it from the dev/static build via `?api=http://localhost:8760`
// (persisted in localStorage).

export interface Health {
	status: string;
	version: string;
	root: string;
}

export interface RootInfo {
	root: string;
	total: number;
	archives: number;
	errors: number;
	scanned_at: number;
	counts: Record<string, number>;
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

export interface AssetPage {
	items: AssetSummary[];
	total: number;
	page: number;
	limit: number;
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
	modelUrl: (id: string) => `${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/model`,
	skinUrl: (id: string) => `${getApiBase()}/api/v1/assets/${encodeURIComponent(id)}/skin`,
	events: () => new EventSource(`${getApiBase()}/api/v1/events`)
};
