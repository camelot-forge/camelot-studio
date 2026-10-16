/** Small pure pieces of Home (`notes/home.md` §D2): the one line a
 * recents row shows for a project's health, and how long ago it was opened —
 * both are copy the operator reads, so both are tested. */
import type { HomeHealth } from './api/client';

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
