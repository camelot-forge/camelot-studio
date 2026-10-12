// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	/** When this bundle was built (`vite.config.ts` defines it): the panel shows
	 * it, so a stale page can be told from a stale daemon. */
	const __BUILD_STAMP__: string;

	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
