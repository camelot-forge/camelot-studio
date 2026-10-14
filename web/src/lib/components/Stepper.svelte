<script lang="ts">
	import { Minus, Plus } from '@lucide/svelte';

	interface Props {
		/** What the row is called: the two buttons' own labels. */
		name: string;
		value: number;
		min?: number;
		max: number;
		/** The read-out; the drafts' "3 / 7" by default. */
		label?: (value: number) => string;
		onchange?: (value: number) => void;
	}
	let { name, value, min = 1, max = 1, label, onchange }: Props = $props();

	function change(next: number) {
		if (next < min || next > max) return;
		onchange?.(next);
	}
</script>

<div class="stepper">
	<button
		type="button"
		class="step"
		aria-label={`${name}: previous`}
		disabled={value <= min}
		onclick={() => change(value - 1)}
	>
		<Minus size={14} />
	</button>
	<span class="value nums">{label ? label(value) : `${value} / ${max}`}</span>
	<button
		type="button"
		class="step"
		aria-label={`${name}: next`}
		disabled={value >= max}
		onclick={() => change(value + 1)}
	>
		<Plus size={14} />
	</button>
</div>

<style>
	.stepper {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-left: auto;
	}
	.step {
		display: grid;
		place-items: center;
		width: 26px;
		height: 26px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-sm);
		background: var(--surface-input);
		color: var(--text-2);
		transition:
			border-color var(--dur) var(--ease),
			color var(--dur) var(--ease);
	}
	.step:hover:not(:disabled) {
		border-color: var(--border-hover);
		color: var(--text);
	}
	.step:disabled {
		color: var(--text-4);
		cursor: not-allowed;
	}
	.value {
		display: grid;
		place-items: center;
		min-width: 64px;
		height: 36px;
		padding: 0 8px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface);
		font-size: var(--fs-dense);
		color: var(--text-2);
	}
</style>
