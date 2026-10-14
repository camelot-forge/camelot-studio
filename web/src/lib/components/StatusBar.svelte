<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		left?: Snippet;
		center?: Snippet;
		right?: Snippet;
	}
	let { left, center, right }: Props = $props();
</script>

<footer class="statusbar">
	<div class="side">{@render left?.()}</div>
	<div class="mid">{@render center?.()}</div>
	<div class="side end">{@render right?.()}</div>
</footer>

<style>
	.statusbar {
		display: flex;
		align-items: center;
		gap: 16px;
		height: var(--h-statusbar);
		padding: 0 12px;
		background: var(--surface);
		border-top: 1px solid var(--border);
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.side {
		display: flex;
		align-items: center;
		gap: 16px;
		flex: 0 0 auto;
	}
	.mid {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 16px;
		flex: 1;
		min-width: 0;
	}
	.end {
		justify-content: flex-end;
		gap: 12px;
	}
	.statusbar :global(.sb-strong) {
		color: var(--text-3);
	}
	.statusbar :global(.sb-accent) {
		color: var(--accent);
	}
	.statusbar :global(.sb-nums) {
		font-variant-numeric: tabular-nums;
	}
	.statusbar :global(.sb-link) {
		color: var(--accent);
		font: inherit;
		text-decoration: underline;
	}
</style>
