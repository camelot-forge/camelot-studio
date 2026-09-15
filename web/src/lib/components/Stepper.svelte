<script lang="ts">
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';

	interface Props {
		value: number;
		min?: number;
		max: number;
		label?: (value: number) => string;
		onchange?: (value: number) => void;
	}
	let { value, min = 1, max = 1, label, onchange }: Props = $props();

	const change = (v: number) => {
		if (v < min || v > max) return;
		onchange?.(v);
	};
</script>

<div class="stepper">
	<button aria-label="previous" disabled={value <= min} onclick={() => change(value - 1)}>
		<ChevronLeft size={14} />
	</button>
	<span class="value">{label ? label(value) : value}</span>
	<button aria-label="next" disabled={value >= max} onclick={() => change(value + 1)}>
		<ChevronRight size={14} />
	</button>
</div>

<style>
	.stepper {
		display: flex;
		align-items: center;
		gap: 6px;
		flex: 1;
		justify-content: space-between;
	}
	button {
		width: 22px;
		height: 22px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface-2);
		color: var(--text);
		display: grid;
		place-items: center;
		cursor: pointer;
	}
	button:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.value {
		font-size: 12px;
		color: var(--text);
		text-align: center;
		min-width: 42px;
	}
</style>
