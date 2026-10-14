/** The app's three destinations, in bar order (see notes/ui-modernization.md,
 * Penpot drafts 1–3): the top bar carries the app switch, the pages keep
 * their own modes (View/Edit lives with the world editor's tools). */
export type AppSection = 'figures' | 'builder' | 'world';

export interface NavItem {
	id: AppSection;
	label: string;
	href: '/' | '/builder' | '/world';
}

export const APP_NAV: readonly NavItem[] = [
	{ id: 'figures', label: 'Figures', href: '/' },
	{ id: 'builder', label: 'Builder', href: '/builder' },
	{ id: 'world', label: 'World', href: '/world' }
];

/** Which destination owns a pathname — the pages stay reachable without
 * passing a prop, and the rule is testable. */
export function activeSection(pathname: string): AppSection {
	if (pathname === '/world' || pathname.startsWith('/world/')) return 'world';
	if (pathname === '/builder' || pathname.startsWith('/builder/')) return 'builder';
	return 'figures';
}
