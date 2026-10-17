/** The app's destinations, in bar order (see notes/ui-modernization.md, Penpot
 * drafts 1–5): Home is the front door first (`home.md` §D6), then the tools —
 * the top bar carries the switch, the pages keep their own modes (View/Edit
 * lives with the world editor's tools) — and Deploy closes the row: it is where
 * a release meets an environment (`pipeline.md` §6). */
export type AppSection = 'home' | 'figures' | 'builder' | 'world' | 'deploy';

export interface NavItem {
	id: AppSection;
	label: string;
	href: '/' | '/builder' | '/world' | '/home' | '/deploy';
}

export const APP_NAV: readonly NavItem[] = [
	{ id: 'home', label: 'Home', href: '/home' },
	{ id: 'world', label: 'World', href: '/world' },
	{ id: 'figures', label: 'Figures', href: '/' },
	{ id: 'builder', label: 'Builder', href: '/builder' },
	{ id: 'deploy', label: 'Deploy', href: '/deploy' }
];

/** Which destination owns a pathname — the pages stay reachable without
 * passing a prop, and the rule is testable. */
export function activeSection(pathname: string): AppSection {
	if (pathname === '/home' || pathname.startsWith('/home/')) return 'home';
	if (pathname === '/world' || pathname.startsWith('/world/')) return 'world';
	if (pathname === '/builder' || pathname.startsWith('/builder/')) return 'builder';
	if (pathname === '/deploy' || pathname.startsWith('/deploy/')) return 'deploy';
	return 'figures';
}
