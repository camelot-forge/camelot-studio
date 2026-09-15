// Player base figures carry every equipment variant as separate meshes
// (Body1..Body5 + letter variants, Arms1..6, HeadA1..5 + HeadB1..5, Lod
// levels...). The game shows one variant per slot; rendering them all
// z-fights. This module classifies glTF mesh names into slots/variants so
// the viewer can select what the character wears. Pure names in, slots out —
// no client files needed to test it.

export interface CharacterVariant {
	label: string;
	names: string[]; // glTF mesh names that make up this visible choice
}

export interface CharacterSlot {
	id: string;
	label: string;
	variants: CharacterVariant[]; // engine order (Body1, Body1b, Body2, ...)
	defaultIndex: number; // -1 = hidden by default (LOD levels are distance-only)
	hiddenNames?: string[]; // glTF mesh names that should never be visible
}

interface SlotSpec {
	id: string;
	label: string;
	re: RegExp;
	hiddenByDefault?: boolean;
}

const SLOTS: SlotSpec[] = [
	{ id: 'body', label: 'Body', re: /^body(\d+)([a-z]?)$/i },
	{ id: 'cloak', label: 'Cloak', re: /^cloak(\d+)([a-z]?)$/i },
	{ id: 'arms', label: 'Arms', re: /^arms(\d+)([a-z]?)$/i },
	{ id: 'gloves', label: 'Gloves', re: /^gloves(\d+)([a-z]?)$/i },
	{ id: 'lbody', label: 'Lbody', re: /^lbody(\d+)([a-z]?)$/i },
	{ id: 'legs', label: 'Legs', re: /^legs(\d+)([a-z]?)$/i },
	{ id: 'boots', label: 'Boots', re: /^boots(\d+)([a-z]?)$/i },
	{ id: 'lod', label: 'LOD', re: /^lod(\d+)([a-z]?)$/i, hiddenByDefault: true }
];

// Classic player heads ship a face surface (HeadA<i>) and a hair shell
// (HeadB<i>). The exporter hides both and attaches the Catacombs fig3
// head/hair on player figures (core/camelot/figure.py), so these slots only
// appear on figures the fig3 assembly could not cover.
const HEAD_A = /^heada(\d+)$/i;
const HEAD_B = /^headb(\d+)$/i;

// glTF mesh names keep the NIF shape suffix ("Body1:0", "Cloak1:17").
function baseName(name: string): string {
	return name.replace(/:\d+$/, '').trim();
}

function sortKey(group1: string, group2?: string): string {
	const pad = (n: string) => n.padStart(3, '0');
	return `${pad(group1)}${group2?.toLowerCase() ?? ''}`;
}

// A figure is a player base when it stacks *several* variants per slot: the
// body alone is not enough (class NPCs ship Body1/Body1b + cloaks), so require
// two multi-variant slots as well.
export function parseCharacter(meshNames: string[]): CharacterSlot[] | null {
	const found = new Map<string, Array<{ name: string; key: string }>>();
	const faces = new Map<number, string>();
	const hairs = new Map<number, string>();
	for (const name of meshNames) {
		const base = baseName(name);
		const headA = HEAD_A.exec(base);
		const headB = headA ? null : HEAD_B.exec(base);
		if (headA) {
			faces.set(Number(headA[1]), name);
			continue;
		}
		if (headB) {
			hairs.set(Number(headB[1]), name);
			continue;
		}
		for (const spec of SLOTS) {
			const m = spec.re.exec(base);
			if (!m) continue;
			const list = found.get(spec.id) ?? [];
			list.push({ name, key: sortKey(m[1], m[2]) });
			found.set(spec.id, list);
			break;
		}
	}

	const body = found.get('body')?.length ?? 0;
	const headCount = faces.size;
	const multi =
		SLOTS.filter((s) => s.id !== 'lod' && (found.get(s.id)?.length ?? 0) >= 2).length +
		(headCount >= 2 ? 1 : 0);
	if (body < 2 || multi < 2) return null;

	const slots: CharacterSlot[] = [];
	if (found.has('body')) slots.push(slotOf('body', 'Body', found.get('body')!));
	if (headCount) {
		const headVariants: CharacterVariant[] = [];
		for (const i of [...faces.keys()].sort((a, b) => a - b))
			headVariants.push({ label: `Head${i}`, names: [faces.get(i)!] });
		slots.push({
			id: 'head',
			label: 'Head',
			variants: headVariants,
			defaultIndex: 0
		});
	}
	if (hairs.size) {
		const hairVariants: CharacterVariant[] = [];
		for (const i of [...hairs.keys()].sort((a, b) => a - b))
			hairVariants.push({ label: `Hair${i}`, names: [hairs.get(i)!] });
		slots.push({
			id: 'hair',
			label: 'Hair',
			variants: hairVariants,
			defaultIndex: 0
		});
	}
	for (const spec of SLOTS) {
		if (spec.id === 'body' || !found.has(spec.id)) continue;
		slots.push(slotOf(spec.id, spec.label, found.get(spec.id)!, spec.hiddenByDefault));
	}
	return slots;
}

function slotOf(
	id: string,
	label: string,
	names: Array<{ name: string; key: string }>,
	hidden = false
): CharacterSlot {
	names.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
	return {
		id,
		label,
		variants: names.map((v) => ({ label: baseName(v.name), names: [v.name] })),
		defaultIndex: hidden ? -1 : 0
	};
}
