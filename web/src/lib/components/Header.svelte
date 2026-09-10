<script lang="ts">
	import { Boxes } from '@lucide/svelte';

	interface Props {
		onReset?: () => void;
		hasModel?: boolean;
	}
	let { onReset, hasModel = false }: Props = $props();
</script>

<header class="header">
	<div class="brand">
		<span class="logo" aria-hidden="true"><Boxes size={18} /></span>
		<div class="titles">
			<span class="name">Camelot Studio</span>
			<span class="sub">DAoC figure viewer — glTF</span>
		</div>
	</div>

	{#if hasModel && onReset}
		<button class="btn btn--secondary" onclick={onReset}>Load another</button>
	{/if}
</header>

<style>
	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.logo {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 34px;
		height: 34px;
		border-radius: var(--r-input);
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	.titles {
		display: flex;
		flex-direction: column;
		line-height: 1.2;
	}
	.name {
		font-weight: 650;
		font-size: 15px;
	}
	.sub {
		font-size: 12px;
		color: var(--text-3);
	}
</style>
