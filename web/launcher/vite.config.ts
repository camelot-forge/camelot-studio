import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

/**
 * The launcher's page is its own build, and that is the point (`C §D16`): the players
 * receive a page that knows four facts and two buttons, never the Studio's front end.
 * The design tokens are the one thing shared, and they are shared by *file* — the same
 * `app.css` the studio uses, so the two surfaces cannot drift apart.
 */
export default defineConfig({
	plugins: [svelte()],
	base: './',
	build: {
		outDir: 'dist',
		emptyOutDir: true
	}
});
