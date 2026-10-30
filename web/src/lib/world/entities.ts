/** The zone's server rows as one searchable list: NPCs, statics and doors.
 *
 * The viewer draws them; the Navigator needs them by name, guild, id and
 * model so an operator can find one without hunting on screen. Pure here so
 * the filters and the sort are tested without a WebGL context. */
import type { WorldSpawns } from '$lib/api/client';

export type EntityTable = 'mob' | 'worldobject' | 'door';

export interface WorldEntity {
	table: EntityTable;
	id: string;
	name: string;
	x: number;
	y: number;
	z: number;
	level?: number;
	guild?: string;
	model?: number;
	classType?: string;
	/** Set by `filterEntities` when an origin is given. */
	distance?: number;
}

export type EntitySort = 'name' | 'level' | 'distance';

export interface EntityFilter {
	query?: string;
	table?: 'all' | EntityTable;
	/** NPCs with a guild: the town guards, trainers and merchants. */
	townOnly?: boolean;
	sort?: EntitySort;
	origin?: { x: number; y: number } | null;
	limit?: number;
}

export function entitiesFromSpawns(spawns: WorldSpawns | null): WorldEntity[] {
	if (!spawns) return [];
	const rows: WorldEntity[] = [];
	for (const mob of spawns.mobs) {
		rows.push({
			table: 'mob',
			id: mob.id,
			name: mob.name,
			x: mob.x,
			y: mob.y,
			z: mob.z,
			level: mob.level,
			guild: mob.guild,
			model: mob.model,
			classType: mob.class_type
		});
	}
	for (const object of spawns.objects) {
		rows.push({
			table: 'worldobject',
			id: object.id,
			name: object.name,
			x: object.x,
			y: object.y,
			z: object.z,
			model: object.model,
			classType: object.class_type
		});
	}
	for (const door of spawns.doors) {
		rows.push({
			table: 'door',
			id: door.id,
			name: door.name,
			x: door.x,
			y: door.y,
			z: door.z
		});
	}
	return rows;
}

export function filterEntities(entities: WorldEntity[], options: EntityFilter = {}): WorldEntity[] {
	const query = (options.query ?? '').trim().toLowerCase();
	const tokens = query ? query.split(/\s+/) : [];
	const origin = options.origin ?? null;
	const table = options.table ?? 'all';
	const filtered = entities
		.filter((entity) => table === 'all' || entity.table === table)
		.filter(
			(entity) => !options.townOnly || (entity.table === 'mob' && Boolean(entity.guild?.trim()))
		)
		.map((entity) => ({
			...entity,
			distance: origin ? Math.hypot(entity.x - origin.x, entity.y - origin.y) : undefined
		}))
		.filter((entity) => {
			if (!tokens.length) return true;
			const haystack = [
				entity.name,
				entity.guild ?? '',
				entity.id,
				entity.model ?? '',
				entity.classType ?? ''
			]
				.join(' ')
				.toLowerCase();
			return tokens.every((token) => haystack.includes(token));
		});
	const sort = options.sort ?? 'name';
	filtered.sort((a, b) => {
		if (sort === 'level') {
			return (a.level ?? 0) - (b.level ?? 0) || a.name.localeCompare(b.name);
		}
		if (sort === 'distance' && origin) {
			return (a.distance ?? Infinity) - (b.distance ?? Infinity) || a.name.localeCompare(b.name);
		}
		return a.name.localeCompare(b.name);
	});
	return filtered.slice(0, options.limit ?? 250);
}
