<script lang="ts">
	import { Shirt } from '@lucide/svelte';
	import { pieceSlots } from '$lib/appearance';

	interface Props {
		/** The glTF document's own mesh names: what this figure carries. */
		meshNames: string[];
		/** The material the assembly was asked for (one for the whole figure:
		 * DAoC picks a slot's piece *by* material name, not per slot). */
		material: string;
	}
	let { meshNames, material }: Props = $props();

	const pieces = $derived(pieceSlots(meshNames));
</script>

<section class="card">
	<h3>
		<Shirt size={14} /> Equipment
		<span class="card-meta">per slot · {material || 'default'}</span>
	</h3>
	{#if pieces.length === 0}
		<p class="hint">No named equipment on this figure.</p>
	{:else}
		<dl class="meta">
			{#each pieces as piece (piece)}
				<div class="piece">
					<dt>{piece}</dt>
				</div>
			{/each}
		</dl>
	{/if}
	<p class="hint">
		Read-only: the assembly picks one piece per slot; the rail's material changes it. Picking a
		variant needs a base figure (load one from <strong>Figures</strong>).
	</p>
</section>

<style>
	.piece {
		justify-content: flex-start;
	}
	.hint {
		margin-top: 10px;
	}
</style>
