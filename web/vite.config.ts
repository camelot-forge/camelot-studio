/// <reference types="vitest/config" />
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
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
		// `pnpm dev` proxies the daemon API (see `camelot serve`), so the
		// browser stays same-origin and no CORS/host exceptions are needed.
		proxy: {
			'/api': {
				target: process.env.CAMELOT_API ?? 'http://127.0.0.1:8760',
				changeOrigin: true
			}
		}
	}
});
