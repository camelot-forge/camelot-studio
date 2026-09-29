import { describe, expect, it } from 'vitest';
import { gameToKeyTime, sampleClip, sampleTrack, slerp, type AnimClip } from './anim';

describe('slerp', () => {
	it('returns endpoints at u=0/1', () => {
		const a: [number, number, number, number] = [1, 0, 0, 0];
		const b: [number, number, number, number] = [0, 1, 0, 0];
		expect(slerp(a, b, 0)).toEqual([1, 0, 0, 0]);
		const end = slerp(a, b, 1);
		expect(end[0]).toBeCloseTo(0, 5);
		expect(end[1]).toBeCloseTo(1, 5);
	});

	it('stays normalized (wxyz order)', () => {
		const q = slerp([0.7071, 0, 0, -0.7071], [0.7071, 0, 0, 0.7071], 0.5);
		expect(Math.hypot(...q)).toBeCloseTo(1, 5);
		// halfway between -90° and +90° about Z is the identity
		expect(q[0]).toBeCloseTo(1, 2);
	});
});

describe('sampleTrack', () => {
	const track = {
		node: 'Bip01 Spine',
		times: [0, 1],
		quats: [
			[1, 0, 0, 0],
			[0, 1, 0, 0]
		] as Array<[number, number, number, number]>
	};
	it('clamps outside the range', () => {
		expect(sampleTrack(track, -1)).toEqual([1, 0, 0, 0]);
		expect(sampleTrack(track, 5)).toEqual([0, 1, 0, 0]);
	});
	it('interpolates inside', () => {
		const q = sampleTrack(track, 0.5)!;
		expect(Math.hypot(...q)).toBeCloseTo(1, 5);
	});
});

describe('sampleClip', () => {
	it('maps node names to quats', () => {
		const clip: AnimClip = {
			kfa: 'BOAR_idle.kfa',
			duration: 5.333,
			rate: 0.2,
			tracks: [
				{ node: 'Bip01', times: [0], quats: [[0.7071, 0, 0, -0.7071]] },
				{
					node: 'Bip01 Tail',
					times: [0, 1],
					quats: [
						[1, 0, 0, 0],
						[1, 0, 0, 0]
					]
				}
			]
		};
		const m = sampleClip(clip, 0.5);
		expect(m.get('Bip01')).toEqual([0.7071, 0, 0, -0.7071]);
		expect(m.has('Bip01 Tail')).toBe(true);
	});
});

describe('gameToKeyTime', () => {
	const clip: AnimClip = { kfa: 'BOAR_idle.kfa', duration: 16 / 3, rate: 0.2, tracks: [] };
	it('scales game seconds to key seconds and loops', () => {
		expect(gameToKeyTime(clip, 0)).toBe(0);
		expect(gameToKeyTime(clip, 2.5)).toBeCloseTo(0.5, 9);
		expect(gameToKeyTime(clip, 16 / 3)).toBeCloseTo(0, 9);
		expect(gameToKeyTime(clip, 16 / 3 + 2.5)).toBeCloseTo(0.5, 9);
	});
	it('falls back to raw time without a duration', () => {
		expect(gameToKeyTime({ ...clip, duration: 0, rate: 0 }, 1.25)).toBe(1.25);
	});
});

describe('gameToKeyTime on a one-shot clip', () => {
	const clamp: AnimClip = { kfa: 'd.kfa', duration: 2, rate: 0.5, loop: false, tracks: [] };

	it('holds the last frame instead of wrapping back to the start', () => {
		expect(gameToKeyTime(clamp, 0)).toBeCloseTo(0);
		expect(gameToKeyTime(clamp, 1)).toBeCloseTo(0.5);
		// past the end: the client plays a `clamp` clip once and stops
		expect(gameToKeyTime(clamp, 5)).toBeCloseTo(2 * 0.5);
	});

	it('still wraps when the clip loops', () => {
		const loop: AnimClip = { ...clamp, loop: true };
		expect(gameToKeyTime(loop, 5)).toBeCloseTo(1 * 0.5);
	});

	it('treats a missing loop flag as looping, so old clips keep playing', () => {
		const legacy = { kfa: 'x.kfa', duration: 2, rate: 1, tracks: [] } as AnimClip;
		expect(gameToKeyTime(legacy, 5)).toBeCloseTo(1);
	});
});
