// The character builder's own rules: the size of a race's pick space, what
// Randomize may hand back, and how a pick is pulled back into range when the
// race changes. Pure, so all three are testable without a browser.

/** The half of `/character/…/options` the appearance pickers use. */
export interface AppearanceTables {
	faces: number;
	hair_styles: number;
	hair_colors: number;
	eye_colors: number;
	skin_tones: number;
	cloak_variants: number;
}

/** One assembled figure: what the rail's steppers hold. Cloak is 0-based —
 * the engine's own "no cloak" is a real pick, not a missing one. */
export interface Appearance {
	face: number;
	hairStyle: number;
	hairColor: number;
	eyeColor: number;
	skinTone: number;
	cloak: number;
}

export interface AppearanceRow {
	key: keyof Appearance;
	label: string;
	min: number;
	max: number;
}

const stepper = (n: number) => Math.max(1, Math.floor(n));
const cloaks = (n: number) => Math.max(0, Math.floor(n));

/** How many figures these tables describe — the rail's own footer. A table
 * that reports nothing still counts as its one legal pick, so the count never
 * claims a figure the steppers cannot reach. */
export function combinationCount(tables: AppearanceTables): number {
	return (
		stepper(tables.faces) *
		stepper(tables.hair_styles) *
		stepper(tables.hair_colors) *
		stepper(tables.eye_colors) *
		stepper(tables.skin_tones) *
		(cloaks(tables.cloak_variants) + 1)
	);
}

/** The rows the appearance section shows, in the draft's order. The cloak
 * appears only when the race offers one (an elf's sheet has none). */
export function appearanceRows(tables: AppearanceTables): AppearanceRow[] {
	const rows: AppearanceRow[] = [
		{ key: 'face', label: 'Face', min: 1, max: stepper(tables.faces) },
		{ key: 'hairStyle', label: 'Hair style', min: 1, max: stepper(tables.hair_styles) },
		{ key: 'hairColor', label: 'Hair color', min: 1, max: stepper(tables.hair_colors) },
		{ key: 'eyeColor', label: 'Eye color', min: 1, max: stepper(tables.eye_colors) },
		{ key: 'skinTone', label: 'Skin tone', min: 1, max: stepper(tables.skin_tones) }
	];
	if (cloaks(tables.cloak_variants) > 0) {
		rows.push({ key: 'cloak', label: 'Cloak', min: 0, max: cloaks(tables.cloak_variants) });
	}
	return rows;
}

/** The pieces a figure carries, in the document's order, each named once.
 *
 * An assembled character is one piece per slot (`Body`, `Arms`, `Cloak`…); a
 * base figure carries every variant (`Body1`, `Body1b`, `Body2`…). The NIF
 * shape suffix (`Body1:0`) is dropped: it is the exporter's index, not a
 * piece of the name. */
export function pieceSlots(meshNames: string[]): string[] {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const raw of meshNames) {
		const name = raw.replace(/:\d+$/, '').trim();
		if (!name || seen.has(name)) continue;
		seen.add(name);
		out.push(name);
	}
	return out;
}

/** A pick uniform over the tables, inside each row's own range. `rand` is
 * injectable so a test can walk the edges of the space. */
export function randomAppearance(
	tables: AppearanceTables,
	rand: () => number = Math.random
): Appearance {
	const pick = (min: number, max: number) => {
		const span = max - min + 1;
		const offset = Math.min(Math.max(0, Math.floor(rand() * span)), span - 1);
		return min + offset;
	};
	const rows = appearanceRows(tables);
	const at = (key: keyof Appearance, fallback: number) => {
		const row = rows.find((r) => r.key === key);
		return row ? pick(row.min, row.max) : fallback;
	};
	return {
		face: at('face', 1),
		hairStyle: at('hairStyle', 1),
		hairColor: at('hairColor', 1),
		eyeColor: at('eyeColor', 1),
		skinTone: at('skinTone', 1),
		cloak: at('cloak', 0)
	};
}

/** The same pick, brought inside another race's ranges: switching to a race
 * with fewer faces must not leave a face index pointing nowhere. Picks that
 * already fit are left exactly as they are. */
export function clampAppearance(tables: AppearanceTables, pick: Appearance): Appearance {
	const fit = (value: number, min: number, max: number) =>
		Math.min(Math.max(min, value), Math.max(min, max));
	return {
		face: fit(pick.face, 1, stepper(tables.faces)),
		hairStyle: fit(pick.hairStyle, 1, stepper(tables.hair_styles)),
		hairColor: fit(pick.hairColor, 1, stepper(tables.hair_colors)),
		eyeColor: fit(pick.eyeColor, 1, stepper(tables.eye_colors)),
		skinTone: fit(pick.skinTone, 1, stepper(tables.skin_tones)),
		cloak: fit(pick.cloak, 0, cloaks(tables.cloak_variants))
	};
}
