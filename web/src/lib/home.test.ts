import { describe, expect, it } from 'vitest';
import type { HomeClient, HomeHealth } from './api/client';
import { clientLabel, healthLabel, leftNotice, menuState, publishLabel, since, slug } from './home';

const found = (state: string, detail = ''): HomeHealth => ({
	path: '/x',
	name: 'x',
	state,
	detail,
	findings: [],
	pinned: 0,
	pending: {}
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

	it('says a pinned base is a decision, not a problem (P8)', () => {
		const label = healthLabel(found('pinned', '1 resource(s) moved, kept ours'));
		expect(label?.text).toBe('1 resource(s) moved, kept ours');
		expect(label?.tone).toBe('warn');
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

describe('publishLabel', () => {
	it('says what a project still owes, or that it owes nothing', () => {
		expect(publishLabel(0)).toBe('nothing to publish');
		expect(publishLabel(1)).toBe('1 edit to publish');
		expect(publishLabel(3)).toBe('3 edits to publish');
	});
});

describe('leftNotice', () => {
	it('says what the project being left keeps, in one line', () => {
		expect(leftNotice({ path: '/a', name: 'Avalon', pending: 3 })).toBe(
			'Avalon keeps 3 edits to publish'
		);
		expect(leftNotice({ path: '/a', name: 'Avalon', pending: 1 })).toBe(
			'Avalon keeps 1 edit to publish'
		);
	});

	it('says nothing when there is nothing to say', () => {
		expect(leftNotice(null)).toBe('');
		expect(leftNotice({ path: '/a', name: 'Avalon', pending: 0 })).toBe('');
	});
});

describe('clientLabel', () => {
	const check = (state: string, detail = ''): HomeClient => ({
		path: '/x',
		state,
		detail,
		build: '',
		zones: 0
	});

	it('checks before anything is written, and says what it found', () => {
		expect(clientLabel(check('ok', '402 zones'))).toEqual({
			text: 'client ok \u00b7 402 zones',
			tone: 'ok'
		});
		expect(clientLabel(check('missing'))?.text).toBe('no such folder');
		expect(clientLabel(check('not-a-client'))?.tone).toBe('warn');
	});

	it('says nothing before the folder has been checked', () => {
		expect(clientLabel(undefined)).toBeNull();
	});
});

describe('menuState', () => {
	const now = Date.parse('2026-10-17T12:00:00Z');

	it('says the work waiting when there is some', () => {
		expect(menuState(1, '2026-10-17T11:59:00Z', now)).toBe('1 edit');
		expect(menuState(3, '2026-10-17T11:59:00Z', now)).toBe('3 edits');
	});

	it('and when it was last opened, when there is not', () => {
		// the same rendering as a row's own "when" (`since`, validated on Home)
		expect(menuState(0, '2026-10-16T11:00:00Z', now)).toBe('25 h ago');
		expect(menuState(0, '2026-10-12T08:00:00Z', now)).toBe('5 d ago');
	});
});

describe('slug', () => {
	it('turns a name into the folder it would live in', () => {
		expect(slug('Avalon')).toBe('avalon');
		expect(slug('Camelot Hills')).toBe('camelot-hills');
		expect(slug('  Cotswold (test) ')).toBe('cotswold-test');
		expect(slug('')).toBe('project');
	});
});
