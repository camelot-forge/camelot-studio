// The journal as the operator reads it.
//
// The daemon journals the ops it *resolved* — `action` (`set`, `add`,
// `remove`), the `values` it wrote and the `expect` it required, which is what
// the user saw (the other side of the diff) — and it keeps the server rows as
// patches with their `originals`. That is enough for the tab the drafts ask
// for, so the view is built here out of pure data: no second round trip, and
// the whole thing is testable without a zone.
import type { EditorRowOp, EditorZone } from '$lib/api/client';

export interface JournalEntry {
	/** The gesture's own word: move, rotate, scale, add, remove, row. */
	kind: string;
	/** What it acted on: "#1234", "mob 8acf21ea". */
	target: string;
	/** Where it stood, or null when there was nothing to compare. */
	before: string | null;
	/** Where it stands now. */
	after: string;
	/** The columns behind `before`/`after`, in the daemon's own names. */
	columns: string[];
	/** A note worth reading (a ground row's z rule, the catalog NIF). */
	note?: string;
}

export interface JournalZone {
	id: number;
	entries: JournalEntry[];
}

export interface JournalView {
	zones: JournalZone[];
	/** The zone's server rows, one entry per patched row. */
	rows: JournalEntry[];
	/** Everything the journal holds, fixtures and rows together. */
	count: number;
}

const num = (value: unknown): number | null =>
	typeof value === 'number' && Number.isFinite(value)
		? value
		: typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))
			? Number(value)
			: null;

const text = (value: unknown): string =>
	value === null || value === undefined ? '' : String(value);

/** Whole units where the editor works in them (it snaps to whole units), one
 * decimal otherwise — never a float's noise in a history the eye reads. */
const round = (value: number): string =>
	Math.abs(value - Math.round(value)) < 0.05 ? String(Math.round(value)) : value.toFixed(1);

/** A column of an op's `values`, in the CSV's own order when the op says so
 * (`columns` is name → file column). */
function columnsOf(values: Record<string, unknown>, order: unknown): string[] {
	const names = Object.keys(values);
	const index = (order ?? {}) as Record<string, number>;
	return names.sort((a, b) => (index[a] ?? 0) - (index[b] ?? 0));
}

function valuesOf(source: Record<string, unknown>, columns: string[]): string | null {
	if (!columns.length) return null;
	return columns.map((column) => `${column} ${text(source[column])}`).join(' · ');
}

/**
 * One entry of the journal, as the operator reads it.
 *
 * The journal holds the ops the daemon *resolved* — `action` (`set`, `add`,
 * `remove`), the `values` it wrote, and the `expect` it required (the values
 * the user saw: the other side of the diff). Which gesture a `set` was is in
 * the columns it names: a scale writes `Scale`, a turn writes `A` (and the
 * place it landed when the pivot moved), anything else moves. A ground row
 * never carries a `Z` — the client drops it on the terrain (W-3) — and the
 * entry says so rather than showing a zero nobody wrote.
 */
function fixtureEntry(op: Record<string, unknown>): JournalEntry {
	const id = num(op.id);
	const target = id !== null ? `#${id}` : '(the journal)';
	const action = text(op.action);
	const values = (op.values ?? {}) as Record<string, unknown>;
	const expect = (op.expect ?? {}) as Record<string, unknown>;
	const written = columnsOf(values, op.columns);
	// A removal writes nothing: what it *held* is the only side there is.
	const held = columnsOf(expect, op.columns);
	const columns = written.length ? written : held;
	const before = valuesOf(expect, held);

	switch (action) {
		case 'set': {
			const kind = 'Scale' in values ? 'scale' : 'A' in values ? 'rotate' : 'move';
			return {
				kind,
				target,
				before,
				after: valuesOf(values, written) ?? '—',
				columns,
				note:
					kind === 'move' && !('Z' in values)
						? 'ground row — its z belongs to the terrain'
						: undefined
			};
		}
		case 'add': {
			const name = text(values['Textual Name']);
			const x = num(values.X);
			const y = num(values.Y);
			return {
				kind: 'add',
				target: name || target,
				before: null,
				after: x !== null && y !== null ? `at ${round(x)}, ${round(y)}` : 'added',
				columns,
				note: `catalog nif ${text(values['NIF #'])}`
			};
		}
		case 'remove':
			return { kind: 'remove', target, before, after: 'removed', columns };
		default:
			// An action a newer daemon knows and this page does not: say so rather
			// than dropping the entry (the count must stay honest).
			return {
				kind: action || 'op',
				target,
				before,
				after: columns.join(', ') || text(op.action),
				columns
			};
	}
}

function patchEntry(patch: Record<string, unknown>): JournalEntry {
	const target = `${text(patch.table)} ${text(patch.key).slice(0, 8)}`;
	if (patch.deleted) return { kind: 'row', target, before: null, after: 'removed', columns: [] };
	const values = (patch.values ?? {}) as Record<string, unknown>;
	const originals = (patch.originals ?? {}) as Record<string, unknown>;
	const columns = Object.keys(values);
	const side = (source: Record<string, unknown>) =>
		columns.map((column) => `${column} ${text(source[column])}`).join(' · ');
	return { kind: 'row', target, before: side(originals), after: side(values), columns };
}

/** The journal, grouped the way the daemon holds it: per zone, then its rows. */
export function journalView(state: { zones: EditorZone[]; rows: EditorRowOp[] }): JournalView {
	const zones = (state.zones ?? []).map((zone) => ({
		id: zone.id,
		entries: (zone.ops ?? []).map(fixtureEntry)
	}));
	const rows = (state.rows ?? []).flatMap((entry) => (entry.patches ?? []).map(patchEntry));
	return {
		zones,
		rows,
		count: zones.reduce((total, zone) => total + zone.entries.length, 0) + rows.length
	};
}
