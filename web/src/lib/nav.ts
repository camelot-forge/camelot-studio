/** The app's destinations, in bar order (see notes/ui-modernization.md, Penpot
 * drafts 1–4): Home is the front door first (`home.md` §D6), then the tools —
 * the top bar carries the switch, the pages keep their own modes (View/Edit
 * lives with the world editor's tools). */
export type AppSection = 'home' | 'figures' | 'builder' | 'world';

export interface NavItem {
	id: AppSection;
	label: string;
	href: '/' | '/builder' | '/world' | '/home';
}

export const APP_NAV: readonly NavItem[] = [
	{ id: 'home', label: 'Home', href: '/home' },
	{ id: 'world', label: 'World', href: '/world' },
	{ id: 'figures', label: 'Figures', href: '/' },
	{ id: 'builder', label: 'Builder', href: '/builder' }
];

/** Which destination owns a pathname — the pages stay reachable without
 * passing a prop, and the rule is testable. */
export function activeSection(pathname: string): AppSection {
	if (pathname === '/home' || pathname.startsWith('/home/')) return 'home';
	if (pathname === '/world' || pathname.startsWith('/world/')) return 'world';
	if (pathname === '/builder' || pathname.startsWith('/builder/')) return 'builder';
	return 'figures';
}
