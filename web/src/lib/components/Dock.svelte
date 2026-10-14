<script lang="ts">
	import type { Component, Snippet } from 'svelte';

	interface Props {
		title: string;
		/** The line under the title: what this dock is showing right now. */
		sub?: string;
		icon: Component<{ size?: number }>;
		/** Which edge the dock is attached to (the side its border is on). */
		side?: 'left' | 'right';
		/** Extra controls for the head, right-aligned. */
		head?: Snippet;
		/** A dock's own foot: actions that belong to the whole dock, not to its
		 * scrolled body (the builder's Randomize / Reset). */
		foot?: Snippet;
		children: Snippet;
	}
	let { title, sub = '', icon: Icon, side = 'left', head, foot, children }: Props = $props();
</script>

<aside class="dock {side}">
	<header class="dock-head">
		<span class="logo" aria-hidden="true"><Icon size={15} /></span>
		<div class="titles">
			<span class="name">{title}</span>
			{#if sub}<span class="sub">{sub}</span>{/if}
		</div>
		{@render head?.()}
	</header>
	<div class="dock-body">{@render children()}</div>
	{#if foot}<footer class="dock-foot">{@render foot()}</footer>{/if}
</aside>

<style>
	.dock-foot {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 14px;
		border-top: 1px solid var(--border);
	}
</style>
