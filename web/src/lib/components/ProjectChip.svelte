<script lang="ts">
	import { Lock } from '@lucide/svelte';
	import { resolve } from '$app/paths';

	interface Props {
		/** The project this daemon serves — empty when there is none. */
		name?: string;
		/** What a save would still write (`pending.fixtures + pending.rows`). */
		pending?: number;
		readOnly?: boolean;
		/** Who holds the checkout, when another process does. */
		holder?: string;
	}
	let { name = '', pending = 0, readOnly = false, holder = '' }: Props = $props();

	const state = $derived(
		readOnly
			? holder
				? `held by ${holder}`
				: 'read-only'
			: pending > 0
				? `${pending} ${pending === 1 ? 'edit' : 'edits'} to publish`
				: 'all published'
	);
	const warn = $derived(readOnly || pending > 0);
	const title = $derived(
		readOnly
			? `this project is held by ${holder || 'another process'}: writes are refused`
			: name
				? 'the edits this project has not published yet'
				: 'no project open — go to Home'
	);
</script>

<a class="chip" class:warn href={resolve('/home')} {title}>
	{#if readOnly}
		<Lock size={13} />
	{:else}
		<span class="dot" aria-hidden="true"></span>
	{/if}
	<span class="name">{name || 'No project'}</span>
	<span class="state">· {name ? state : 'Open…'}</span>
</a>

<style>
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 12px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-input);
		font-size: var(--fs-dense);
		color: var(--text-4);
		min-width: 0;
		text-decoration: none;
	}
	.chip:hover {
		border-color: var(--border-hover, var(--border-input));
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
	}
	.warn .dot {
		background: var(--warn);
	}
	.warn {
		color: var(--warn-text);
	}
	.name {
		color: var(--text-2);
		font-weight: 500;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.warn .name {
		color: var(--text);
	}
	.state {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
