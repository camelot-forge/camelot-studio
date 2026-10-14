<script lang="ts">
	import { RotateCcw, UserRound } from '@lucide/svelte';
	import { setCharacterVariant, type CharacterRig } from '$lib/gltf/load';

	interface Props {
		rig: CharacterRig;
	}
	let { rig }: Props = $props();

	// User overrides per slot index (mesh visibility is three.js state, not
	// reactive): absent = the rig's default, -1 = slot hidden.
	let chosen = $state<Record<number, number>>({});

	function current(slotIndex: number): number {
		return chosen[slotIndex] ?? rig.slots[slotIndex].defaultIndex;
	}

	function select(slotIndex: number, variantIndex: number) {
		chosen[slotIndex] = variantIndex;
		setCharacterVariant(rig, slotIndex, variantIndex);
	}

	function reset() {
		chosen = {};
		for (let i = 0; i < rig.slots.length; i++) {
			setCharacterVariant(rig, i, rig.slots[i].defaultIndex);
		}
	}
</script>

<section class="card">
	<h3>
		<UserRound size={14} /> Character
		<span class="card-meta">{rig.slots.length} slots</span>
	</h3>
	<p class="hint">
		Player base figure — pick the visible equipment variant per slot. Off hides the slot (naked /
		LOD levels are distance-only).
	</p>
	<div class="slots">
		{#each rig.slots as slot, si (slot.id)}
			<label class="row">
				<span class="slot">{slot.label}</span>
				<select value={current(si)} onchange={(e) => select(si, Number(e.currentTarget.value))}>
					<option value={-1}>Off</option>
					{#each slot.variants as variant, vi (variant.label)}
						<option value={vi}>{variant.label}</option>
					{/each}
				</select>
			</label>
		{/each}
	</div>
	<button class="link" onclick={reset}>
		<RotateCcw size={13} /> Reset
	</button>
</section>

<style>
	.hint {
		margin-bottom: 10px;
	}
	.slots {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}
	.slot {
		font-size: 12.5px;
		color: var(--text-2);
	}
	select {
		flex: 0 1 150px;
		min-width: 0;
		padding: 4px 6px;
		font-size: 12px;
		color: var(--text);
		background: var(--surface-input);
		border: 1px solid var(--border);
		border-radius: var(--r-input);
	}
	.link {
		margin-top: 12px;
	}
</style>
