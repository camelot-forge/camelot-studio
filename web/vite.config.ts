/// <reference types="vitest/config" />
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

/** The build's own clock, in the machine's own time zone, so it can be read
 * against the operator's wall clock: "is this page the build I just made?" */
function buildStamp(): string {
	const now = new Date();
	const pad = (value: number) => String(value).padStart(2, '0');
	return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(
		now.getHours()
	)}:${pad(now.getMinutes())}`;
}

export default defineConfig({
	// The viewer stamps its own build into the panel: "is this page the build I
	// think it is?" is a question that costs more than it should when a browser
	// keeps an old bundle (the operator's Firefox did, 2026-10-12).
	define: {
		__BUILD_STAMP__: JSON.stringify(buildStamp())
	},
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}'],
		environment: 'node'
	},
	server: {
		// `pnpm dev` proxies the daemon API (see `camelot serve`), so the browser
		// stays same-origin and no CORS/host exceptions are needed. The default is
		// the daemon `camelot serve` starts by itself, port 8760; a scratch daemon
		// is named explicitly: `CAMELOT_API=http://127.0.0.1:8763 pnpm dev`. Pointing
		// at a daemon older than this front is the one mistake that reads as a JSON
		// error in the console (2026-10-17: a daemon from before the project routes
		// answered every unknown `/api` path with its own index.html).
		proxy: {
			'/api': {
				target: process.env.CAMELOT_API ?? 'http://127.0.0.1:8760',
				changeOrigin: true
			}
		}
	}
});
