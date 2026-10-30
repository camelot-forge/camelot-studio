/** The zone catalog, searched the way the Add panel asks: by name, file or
 * id, with the two filters the daemon's `nifs.csv` supports. */
import type { WorldCatalogEntry } from '$lib/api/client';

export type CatalogFilter = 'all' | 'ground' | 'mesh';

export function filterCatalog(
	entries: WorldCatalogEntry[],
	query: string,
	filter: CatalogFilter = 'all',
	limit = 200
): WorldCatalogEntry[] {
	const needle = query.trim().toLowerCase();
	return entries
		.filter((entry) => {
			if (filter === 'ground' && !entry.ground) return false;
			if (filter === 'mesh' && !entry.mesh) return false;
			return (
				!needle ||
				entry.filename.toLowerCase().includes(needle) ||
				entry.name.toLowerCase().includes(needle) ||
				String(entry.id) === needle
			);
		})
		.sort((a, b) => (a.name || a.filename).localeCompare(b.name || b.filename))
		.slice(0, limit);
}
