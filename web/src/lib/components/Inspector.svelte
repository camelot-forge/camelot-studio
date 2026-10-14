<script lang="ts">
	import { PanelRight } from '@lucide/svelte';
	import type { AnimClip, AnimStateSummary } from '$lib/api/client';
	import type { LoadedModel } from '$lib/gltf/load';
	import { figureJoints, skinSlots } from '$lib/inspect';
	import CharacterPanel from './CharacterPanel.svelte';
	import AnimCard from './inspect/AnimCard.svelte';
	import BonesCard from './inspect/BonesCard.svelte';
	import ModelCard from './inspect/ModelCard.svelte';
	import SkinsCard from './inspect/SkinsCard.svelte';

	interface Props {
		model: LoadedModel;
		/** The daemon's index entry, when the figure came from it. */
		origin?: string | null;
		/** The figure's own anim set (empty when the tables cover it not). */
		states: AnimStateSummary[];
		/** The clip on screen. */
		clip: AnimClip | null;
		/** Slots the daemon refused, with its own words. */
		refused: Record<string, string>;
		/** The slot the daemon is being asked for right now. */
		loadingSlot?: string | null;
		/** The slot the operator last picked (lights its row). */
		activeSlot?: string | null;
		onplay: (slot: string) => void;
		/** The bones overlay, on its way to the viewport. */
		bones?: boolean;
	}
	let {
		model,
		origin = null,
		states,
		clip,
		refused,
		loadingSlot = null,
		activeSlot = null,
		onplay,
		bones = $bindable(false)
	}: Props = $props();

	const joints = $derived(figureJoints(model.skinJson));
	const slots = $derived(skinSlots([...model.meshTextures.values()]));
</script>

<aside class="inspector">
	<header class="dock-head">
		<span class="logo" aria-hidden="true"><PanelRight size={15} /></span>
		<div class="titles">
			<span class="name">Inspect</span>
			<span class="sub">
				{model.name}
				{#if origin}· {origin}{/if}
			</span>
		</div>
	</header>

	<div class="cards">
		<ModelCard {model} {origin} />
		{#if model.character}
			{#key model.character}
				<CharacterPanel rig={model.character} />
			{/key}
		{/if}
		<AnimCard {states} {clip} {refused} loading={loadingSlot} {activeSlot} {onplay} />
		<BonesCard {joints} bind:bones />
		<SkinsCard {slots} />
	</div>
</aside>

<style>
	.inspector {
		width: var(--w-dock);
		min-width: var(--w-dock);
		display: flex;
		flex-direction: column;
		border-left: 1px solid var(--border);
		background: var(--surface-2);
		min-height: 0;
	}
	.cards {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 14px;
		padding: 14px;
		overflow-y: auto;
		min-height: 0;
	}
</style>
