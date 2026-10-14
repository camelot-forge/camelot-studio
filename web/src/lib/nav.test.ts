import { describe, expect, it } from 'vitest';
import { APP_NAV, activeSection } from './nav';

describe('activeSection', () => {
	it('maps the three destinations', () => {
		expect(activeSection('/')).toBe('figures');
		expect(activeSection('/builder')).toBe('builder');
		expect(activeSection('/world')).toBe('world');
	});

	it('keeps sub-paths on their section, trailing slash included', () => {
		expect(activeSection('/world/')).toBe('world');
		expect(activeSection('/world/zones/45')).toBe('world');
		expect(activeSection('/builder/')).toBe('builder');
		expect(activeSection('/builder/x')).toBe('builder');
	});

	it('falls back to figures for anything else', () => {
		expect(activeSection('/nope')).toBe('figures');
		expect(activeSection('')).toBe('figures');
	});
});

describe('APP_NAV', () => {
	it('orders the destinations and covers each section once', () => {
		expect(APP_NAV.map((item) => item.id)).toEqual(['figures', 'builder', 'world']);
		expect(new Set(APP_NAV.map((item) => item.id)).size).toBe(APP_NAV.length);
	});

	it('gives every item a label and a rooted href', () => {
		for (const item of APP_NAV) {
			expect(item.label.length).toBeGreaterThan(0);
			expect(item.href.startsWith('/')).toBe(true);
		}
	});
});
