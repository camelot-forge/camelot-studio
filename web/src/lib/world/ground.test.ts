// The ground composition, pinned to the model measured against the client's
// own bake (`notes/layer-compose.py`): layer 0 first, later layers
// lerped in ascending order, the mask channel each *file index* owns, tiled
// sheets repeated `tiles` times, an untiled row stamped from the zone-wide
// sheet.

import { describe, expect, it } from 'vitest';
import type { WorldTextureLayer } from '$lib/api/client';
import { MAX_LAYERS, PATCH_GRID, groundShader, groundSlots } from './ground';

function layer(overrides: Partial<WorldTextureLayer>): WorldTextureLayer {
	return {
		patch_x: 3,
		patch_y: 5,
		texture: 'LightGrass2',
		rotate: 0,
		u: 0,
		v: 0,
		u_scale: 1,
		v_scale: 1,
		visible: true,
		tiles: 256,
		tileable: true,
		...overrides
	} as WorldTextureLayer;
}

describe('groundSlots', () => {
	it('keeps the mask channel each file index owns', () => {
		const slots = groundSlots(
			[
				layer({ texture: 'LightGrass2' }),
				layer({ texture: 'shoresand', tiles: 256 }),
				layer({ texture: 'bouldersnrocks', tiles: 64 }),
				layer({ texture: 'cliff02' }),
				layer({ texture: 'CamHills-Small', tiles: 1, tileable: false })
			],
			['patch0305-00.dds', 'patch0305-01.dds']
		);
		expect(slots).not.toBeNull();
		expect(slots!.map((slot) => [slot.maskIndex, slot.channel])).toEqual([
			[0, 0],
			[0, 1],
			[0, 2],
			[1, 0],
			[1, 1]
		]);
		expect(slots!.map((slot) => slot.mask)).toEqual([
			'patch0305-00.dds',
			'patch0305-00.dds',
			'patch0305-00.dds',
			'patch0305-01.dds',
			'patch0305-01.dds'
		]);
	});

	it('drops invisible rows without moving the others off their channel', () => {
		const slots = groundSlots(
			[
				layer({ texture: 'LightGrass2' }),
				layer({ texture: 'shoresand', visible: false }),
				layer({ texture: 'bouldersnrocks' })
			],
			['patch0305-00.dds']
		);
		expect(slots!.map((slot) => [slot.sheet, slot.channel])).toEqual([
			['LightGrass2', 0],
			['bouldersnrocks', 2]
		]);
	});

	it('reads an untiled row as one window of the zone-wide sheet', () => {
		const slots = groundSlots(
			[layer({}), layer({ texture: 'CamHills-Small', tiles: 1, tileable: false })],
			['patch0305-00.dds']
		);
		expect(slots![0].tiles).toBe(256);
		// tileable = 0 needs no case of its own: tiles = 1 is one zone window
		expect(slots![1].tiles).toBe(1);
		expect(slots![1].sheet).toBe('CamHills-Small');
	});

	it('refuses a patch whose mask the client does not ship', () => {
		expect(
			groundSlots([layer({}), layer({}), layer({}), layer({})], ['patch0305-00.dds'])
		).toBeNull();
		expect(groundSlots([], [])).toBeNull();
	});
});

describe('groundShader', () => {
	it('stacks the layers in ascending order over layer 0', () => {
		const slots = groundSlots(
			[
				layer({ texture: 'LightGrass2' }),
				layer({ texture: 'shoresand' }),
				layer({ texture: 'bouldersnrocks' })
			],
			['patch0305-00.dds']
		)!;
		const { declarations, body } = groundShader(
			slots.length,
			slots.map((slot) => slot.maskIndex)
		);
		expect(declarations).toContain('uniform sampler2D uGroundSheet0;');
		expect(declarations).toContain('uniform sampler2D uGroundMask0;');
		// the declarations serve the deepest stack the corpus ships
		expect(declarations).toContain(`uniform sampler2D uGroundSheet${MAX_LAYERS - 1};`);
		// layer 0 is the base: sampled once, never mixed
		expect(body.startsWith('vec3 groundColor = texture2D(uGroundSheet0,')).toBe(true);
		// the others lerp over it by their own mask channel, in order
		expect(body).toContain('uGroundChannel1');
		expect(body).toContain('uGroundChannel2');
		expect(body.indexOf('uGroundSheet1')).toBeLessThan(body.indexOf('uGroundSheet2'));
		expect(body.endsWith('diffuseColor.rgb *= groundColor;')).toBe(true);
	});

	it('reads each layer through its own mask file', () => {
		// layers 0-2 read mask 0, layers 3-4 read mask 1
		const { body } = groundShader(5, [0, 0, 0, 1, 1]);
		expect(body).toContain('texture2D(uGroundMask0, vGroundUv)');
		expect(body).toContain('texture2D(uGroundMask1, vGroundUv)');
	});

	it('repeats a sheet tiles times across the zone', () => {
		const slots = groundSlots(
			[
				layer({ texture: 'LightGrass2', tiles: 256 }),
				layer({ texture: 'CamHills-Small', tiles: 1, tileable: false })
			],
			['patch0305-00.dds']
		)!;
		const { declarations, body } = groundShader(
			slots.length,
			slots.map((slot) => slot.maskIndex)
		);
		// one zone uv for every layer: ((patch + uv) / 8) * tiles
		expect(declarations).toContain(
			`(vec2(vGroundUv.x, 1.0 - vGroundUv.y) + uGroundPatch) / ${PATCH_GRID}.0;`
		);
		expect(declarations).toContain('return t * shape.y;');
		// the layer sheets read (u, 1 - v) — row 0 north, flipY off
		expect(declarations).toContain('1.0 - vGroundUv.y');
		// every layer goes through the same helper; the tiles ride in the shape
		expect(body).toContain('groundLayerUv(uGroundTransform0, uGroundShape0)');
		expect(body).toContain('groundLayerUv(uGroundTransform1, uGroundShape1)');
	});
});
