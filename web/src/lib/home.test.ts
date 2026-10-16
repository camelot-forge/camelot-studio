import { describe, expect, it } from 'vitest';
import type { HomeHealth } from './api/client';
import { healthLabel, since } from './home';

const found = (state: string, detail = ''): HomeHealth => ({
	path: '/x',
	name: 'x',
	state,
	detail,
	findings: []
});

describe('healthLabel', () => {
	it('says nothing before the row has been asked', () => {
		expect(healthLabel(undefined)).toBeNull();
	});

	it('says the one thing the operator needs, in his words', () => {
		expect(healthLabel(found('ok'))).toEqual({ text: 'client ok', tone: 'ok' });
		expect(healthLabel(found('missing'))?.text).toBe('client missing');
		expect(healthLabel(found('no-client'))?.text).toBe('no client resolved');
	});

	it('carries the daemon detail for a changed base', () => {
		const label = healthLabel(found('changed', '2 of 565 resources no longer match'));
		expect(label?.tone).toBe('warn');
		expect(label?.text).toBe('2 of 565 resources no longer match');
	});
});

describe('since', () => {
	const now = Date.parse('2026-10-16T22:00:00Z');
	const ago = (iso: string) => since(iso, now);

	it('reads like a person wrote it', () => {
		expect(ago('2026-10-16T21:59:30Z')).toBe('just now');
		expect(ago('2026-10-16T21:40:00Z')).toBe('20 min ago');
		expect(ago('2026-10-16T20:00:00Z')).toBe('2 h ago');
		expect(ago('2026-10-14T12:00:00Z')).toBe('2 d ago');
	});

	it('falls back to the date, then to nothing', () => {
		expect(ago('2026-08-01T12:00:00Z')).toBe('2026-08-01');
		expect(ago('')).toBe('');
		expect(since('not a date', now)).toBe('');
	});
});
