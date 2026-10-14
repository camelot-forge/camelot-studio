import { describe, expect, it } from 'vitest';
import type { AnimClip } from './anim';
import { advance, formatClock, formatTime, keyCount, tickRatios } from './transport';

const clip = (tracks: AnimClip['tracks'], rate = 1, duration = 2.4): AnimClip => ({
	kfa: 'amb_idle.kfa',
	duration,
	rate,
	tracks
});

describe('tickRatios', () => {
	it('merges rotation and position keys, sorted, ends excluded', () => {
		const ratios = tickRatios(
			clip([
				{ node: 'a', times: [0, 1.2, 2.4], quats: [] },
				{ node: 'b', ptimes: [0, 1.2, 2.4], positions: [] }
			])
		);
		expect(ratios).toEqual([0.5]);
	});

	it('reads keys in game seconds through the clip’s own rate', () => {
		// rate 2: a key at KFA second 2.4 lands at game second 1.2 (half of 2.4)
		const ratios = tickRatios(clip([{ node: 'a', times: [2.4], quats: [] }], 2));
		expect(ratios).toHaveLength(1);
		expect(ratios[0]).toBeCloseTo(0.5, 9);
	});

	it('says nothing for a clip with no duration', () => {
		expect(tickRatios(clip([{ node: 'a', times: [1], quats: [] }], 1, 0))).toEqual([]);
	});
});

describe('keyCount', () => {
	it('counts a shared key once', () => {
		expect(
			keyCount(
				clip([
					{ node: 'a', times: [0, 1, 2], quats: [] },
					{ node: 'b', times: [0, 2], quats: [] },
					{ node: 'c', ptimes: [1], positions: [] }
				])
			)
		).toBe(3);
	});
});

describe('advance', () => {
	it('wraps a looping clip', () => {
		expect(advance(2.3, 0.2, 2.4, true)).toEqual({ time: 0.1, ended: false });
	});

	it('holds a one-shot on its last frame and says it ended', () => {
		expect(advance(2.3, 0.2, 2.4, false)).toEqual({ time: 2.4, ended: true });
	});

	it('keeps a paused clock where it is', () => {
		expect(advance(1.2, 0, 2.4, true)).toEqual({ time: 1.2, ended: false });
	});
});

describe('formatTime', () => {
	it('writes two decimals, and a zero for nonsense', () => {
		expect(formatTime(1.236)).toBe('1.24 s');
		expect(formatTime(Number.NaN)).toBe('0.00 s');
	});
});

describe('formatClock', () => {
	it('reads as one span, not two units', () => {
		expect(formatClock(1.24, 2.4)).toBe('1.24 / 2.40 s');
		expect(formatClock(Number.NaN, 0)).toBe('0.00 / 0.00 s');
	});
});
