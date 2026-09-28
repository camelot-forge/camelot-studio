import { describe, expect, it } from 'vitest';
import { armourMaterialParam, parseCharacter } from './character';

// Real mesh list of figures/BSaracenF.NIF (49 shapes as exported).
const SARACEN_F = [
	'Cloak3:29',
	'Arms2:10',
	'Arms4:12',
	'Legs1:14',
	'Cloak2:18',
	'HeadB5:28',
	'HeadA3:21',
	'HeadB2:25',
	'HeadB1:24',
	'Boots1:15',
	'Gloves1:13',
	'Body1:0',
	'Cloak1:17',
	'HeadA2:20',
	'HeadB3:26',
	'HeadA4:22',
	'Arms3:11',
	'HeadA1:19',
	'HeadB4:27',
	'HeadA5:23',
	'Arms1:9',
	'Lbody1:6',
	'Lbody2:7',
	'Boots2:16',
	'Legs2:36',
	'LBody4:39',
	'Boots4:47',
	'Legs3:38',
	'Legs4:46',
	'Boots5:48',
	'Boots3:37',
	'Lbody3:8',
	'Body2:2',
	'Body3:4',
	'Body4:30',
	'Body5:44',
	'Body1b:1',
	'Body2b:3',
	'Body3b:5',
	'Body4b:31',
	'Body5b:45',
	'Arms6:33',
	'Arms5:32',
	'Gloves2:34',
	'Gloves3:35',
	'Lod1a:40',
	'Lod1b:41',
	'Lod1c:42',
	'Lod1d:43'
];

describe('parseCharacter', () => {
	it('parses a player base figure into ordered slots', () => {
		const slots = parseCharacter(SARACEN_F)!;
		expect(slots).not.toBeNull();
		expect(slots.map((s) => s.id)).toEqual([
			'body',
			'head',
			'hair',
			'cloak',
			'arms',
			'gloves',
			'lbody',
			'legs',
			'boots',
			'lod'
		]);
		const byId = Object.fromEntries(slots.map((s) => [s.id, s]));
		expect(byId.body.variants.map((v) => v.label)).toEqual([
			'Body1',
			'Body1b',
			'Body2',
			'Body2b',
			'Body3',
			'Body3b',
			'Body4',
			'Body4b',
			'Body5',
			'Body5b'
		]);
		expect(byId.head.variants).toEqual([
			{ label: 'Head1', names: ['HeadA1:19'] },
			{ label: 'Head2', names: ['HeadA2:20'] },
			{ label: 'Head3', names: ['HeadA3:21'] },
			{ label: 'Head4', names: ['HeadA4:22'] },
			{ label: 'Head5', names: ['HeadA5:23'] }
		]);
		expect(byId.hair.variants).toEqual([
			{ label: 'Hair1', names: ['HeadB1:24'] },
			{ label: 'Hair2', names: ['HeadB2:25'] },
			{ label: 'Hair3', names: ['HeadB3:26'] },
			{ label: 'Hair4', names: ['HeadB4:27'] },
			{ label: 'Hair5', names: ['HeadB5:28'] }
		]);
		expect(byId.lbody.variants.map((v) => v.label)).toEqual([
			'Lbody1',
			'Lbody2',
			'Lbody3',
			'LBody4'
		]);
	});

	it('defaults every slot to its first variant, LOD to hidden', () => {
		const slots = parseCharacter(SARACEN_F)!;
		for (const slot of slots) {
			expect(slot.defaultIndex).toBe(slot.id === 'lod' ? -1 : 0);
		}
	});

	it('accepts class NPCs with body + cloak variants', () => {
		const slots = parseCharacter([
			'Body1:0',
			'Body1b:1',
			'Cloak1:2',
			'Cloak2:3',
			'Lod1a:4',
			'Lod1b:5',
			'Bip01 Pelvis'
		]);
		expect(slots?.map((s) => s.id)).toEqual(['body', 'cloak', 'lod']);
	});

	it('keeps extra letter variants ordered after their number', () => {
		const slots = parseCharacter([
			'Body1',
			'Body1b',
			'Body1c',
			'Body2',
			'Body2b',
			'Arms1',
			'Arms2'
		]);
		expect(slots?.find((s) => s.id === 'body')?.variants.map((v) => v.label)).toEqual([
			'Body1',
			'Body1b',
			'Body1c',
			'Body2',
			'Body2b'
		]);
	});

	it('rejects monsters and single-variant figures', () => {
		expect(parseCharacter(['Body', 'Head', 'Legs', 'Tail'])).toBeNull();
		expect(parseCharacter(['Body1', 'Body2', 'Head', 'Tail', 'Wing1', 'Wing2'])).toBeNull();
		expect(parseCharacter([])).toBeNull();
	});
});

describe('armourMaterialParam', () => {
	it('sends the picked material as a query value', () => {
		expect(armourMaterialParam('Plate')).toBe('Plate');
	});

	it('omits the parameter when nothing is picked, so the server default applies', () => {
		expect(armourMaterialParam('')).toBeUndefined();
	});
});
