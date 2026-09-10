import { describe, expect, it } from 'vitest';
import { BONE_INDEX, boneName, MAX_VERIFIED } from './skeleton';

describe('skeleton bone index table', () => {
	it('covers the verified 0..53 range', () => {
		expect(Object.keys(BONE_INDEX)).toHaveLength(54);
		expect(BONE_INDEX[0]).toBe('Bip01');
		expect(BONE_INDEX[53]).toBe('Editable Mesh');
		expect(MAX_VERIFIED).toBe(53);
	});

	it('anchors the verified cross-checked bones', () => {
		expect(boneName(11)).toBe('Bip01 R Forearm');
		expect(boneName(12)).toBe('Bip01 R Hand');
		expect(boneName(23)).toBe('Bip01 R Shield');
		expect(boneName(42)).toBe('Bip01 R Thigh');
	});

	it('returns null outside the verified range', () => {
		expect(boneName(-1)).toBeNull();
		expect(boneName(80)).toBeNull();
	});
});
