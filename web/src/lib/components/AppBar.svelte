<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { Castle } from '@lucide/svelte';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { APP_NAV, activeSection, type AppSection } from '$lib/nav';

	interface Props {
		/** Which destination to light up; derived from the URL when omitted. */
		section?: AppSection;
		subtitle?: string;
		/** Context chip — the current object (figure, zone, character…). */
		contextIcon?: Component<{ size?: number }> | null;
		contextLabel?: string;
		contextMeta?: string;
		/** Page actions, right-aligned. */
		actions?: Snippet;
	}
	let {
		section,
		subtitle = '',
		contextIcon: ContextIcon = null,
		contextLabel = '',
		contextMeta = '',
		actions
	}: Props = $props();

	const active = $derived(section ?? activeSection(page.url.pathname));
</script>

<header class="appbar">
	<a class="brand" href={resolve('/home')} aria-label="Camelot Studio">
		<span class="logo" aria-hidden="true"><Castle size={20} /></span>
		<span class="titles">
			<span class="name">Camelot Studio</span>
			{#if subtitle}<span class="sub">{subtitle}</span>{/if}
		</span>
	</a>

	{#if contextLabel}
		<div class="context" title={contextMeta ? `${contextLabel} · ${contextMeta}` : contextLabel}>
			{#if ContextIcon}<ContextIcon size={14} />{/if}
			<span class="ctx-label">{contextLabel}</span>
			{#if contextMeta}<span class="ctx-meta">· {contextMeta}</span>{/if}
		</div>
	{/if}

	<nav aria-label="Sections">
		{#each APP_NAV as item (item.id)}
			<a
				class="seg"
				class:on={active === item.id}
				href={resolve(item.href)}
				aria-current={active === item.id ? 'page' : undefined}>{item.label}</a
			>
		{/each}
	</nav>

	<div class="actions">{@render actions?.()}</div>
</header>

<style>
	.appbar {
		display: flex;
		align-items: center;
		gap: 12px;
		height: var(--h-topbar);
		padding: 0 16px;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 12px;
		color: inherit;
		text-decoration: none;
	}
	.logo {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 36px;
		height: 36px;
		border-radius: var(--r-lg);
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	.titles {
		display: flex;
		flex-direction: column;
		line-height: 1.2;
	}
	.name {
		font-weight: 600;
		font-size: var(--fs-title);
		color: var(--text);
	}
	.sub {
		font-size: var(--fs-small);
		color: var(--text-3);
	}
	.context {
		display: flex;
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
	}
	.ctx-label {
		color: var(--text-2);
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.ctx-meta {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	nav {
		display: flex;
		gap: 4px;
		margin: 0 auto;
		padding: 4px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-input);
	}
	nav .seg {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 78px;
		height: 24px;
		padding: 0 10px;
		border-radius: var(--r-sm);
		font-size: var(--fs-dense);
		color: var(--text-3);
	}
	nav .seg:hover {
		color: var(--text);
	}
	nav .seg.on {
		background: var(--accent);
		color: var(--accent-ink);
		font-weight: 600;
	}
	nav .seg.on:hover {
		color: var(--accent-ink);
	}
	.actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}
</style>
