/** Small pure pieces of Home (`notes/home.md` §D2/§D4): the one line a
 * recents row shows for a project's health, how long ago it was opened, what
 * it still owes a publish, and what a switch leaves behind — all copy the
 * operator reads, so all tested. */
import type { HomeClient, HomeHealth, HomeLeft } from './api/client';

export interface HealthLabel {
	text: string;
	tone: 'ok' | 'warn';
}

/** What a row's health chip says. `undefined` = the row has not been asked
 * yet (the page paints first, the health fills in). */
export function healthLabel(found: HomeHealth | undefined): HealthLabel | null {
	if (!found) return null;
	switch (found.state) {
		case 'ok':
			return { text: 'client ok', tone: 'ok' };
		case 'changed':
			return { text: found.detail || 'client changed', tone: 'warn' };
		case 'missing':
			return { text: 'client missing', tone: 'warn' };
		case 'pinned':
			return { text: found.detail || 'client pinned', tone: 'warn' };
		case 'no-client':
			return { text: 'no client resolved', tone: 'warn' };
		default:
			return { text: found.detail || found.state, tone: 'warn' };
	}
}

/** "2 h ago", for a row's right edge. The exact stamp stays in the title. */
export function since(iso: string, now: number = Date.now()): string {
	const at = Date.parse(iso);
	if (!Number.isFinite(at)) return '';
	const seconds = Math.max(0, Math.round((now - at) / 1000));
	if (seconds < 90) return 'just now';
	const minutes = Math.round(seconds / 60);
	if (minutes < 90) return `${minutes} min ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 36) return `${hours} h ago`;
	const days = Math.round(hours / 24);
	return days < 30 ? `${days} d ago` : iso.slice(0, 10);
}

/** A row's second chip: what a project still owes a publish — the draft's own
 * words (`home.md` §D1). */
export function publishLabel(pending: number): string {
	if (pending <= 0) return 'nothing to publish';
	return pending === 1 ? '1 edit to publish' : `${pending} edits to publish`;
}

/** The line a switch leaves on Home (`home.md` §D3.1): what the project being
 * left still holds. Empty when there is nothing to say. */
export function leftNotice(left: HomeLeft | null | undefined): string {
	if (!left || left.pending <= 0) return '';
	const edits = left.pending === 1 ? '1 edit' : `${left.pending} edits`;
	return `${left.name} keeps ${edits} to publish`;
}

/** The create modal's client line (`home.md` §D4): the daemon's own check,
 * said in the operator's words — the same shape as a row's health chip. */
export function clientLabel(found: HomeClient | undefined): HealthLabel | null {
	if (!found) return null;
	switch (found.state) {
		case 'ok':
			return { text: `client ok \u00b7 ${found.detail}`, tone: 'ok' };
		case 'missing':
			return { text: 'no such folder', tone: 'warn' };
		case 'not-a-client':
			return { text: 'not a DAoC client', tone: 'warn' };
		default:
			return { text: found.detail || found.state, tone: 'warn' };
	}
}

/** The folder a new project would live in, from its name — what the modal's
 * hint shows under NAME ("folder \u00b7 avalon"). */
export function slug(value: string): string {
	const cleaned = value
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
	return cleaned || 'project';
}

/** One line of the chip's menu (`home.md` §D1/§D5): the work waiting, or when
 * the project was last opened — never a `verify`, which is a row's own check. */
export function menuState(pending: number, opened: string, now: number = Date.now()): string {
	if (pending > 0) return pending === 1 ? '1 edit' : `${pending} edits`;
	return since(opened, now);
}
