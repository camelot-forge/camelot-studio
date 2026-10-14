<script lang="ts">
	import { Palette } from '@lucide/svelte';
	import type { SkinSlot } from '$lib/inspect';

	interface Props {
		slots: SkinSlot[];
	}
	let { slots }: Props = $props();
</script>

<section class="card">
	<h3>
		<Palette size={14} /> Skins
		<span class="card-meta">mesh slots</span>
	</h3>
	{#if slots.length === 0}
		<p class="hint">
			No sheet travelled with this figure: the exporter writes one per mesh slot when the NIF’s skin
			texture resolves.
		</p>
	{:else}
		<dl class="meta">
			{#each slots as slot (slot.label)}
				<div>
					<dt>{slot.label}</dt>
					<dd class="nums" title={slot.name}>{slot.name}</dd>
				</div>
			{/each}
		</dl>
	{/if}
</section>
