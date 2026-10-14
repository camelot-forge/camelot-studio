<script lang="ts">
	import { PanelRight } from '@lucide/svelte';
	import type { AnimClip, AnimStateSummary } from '$lib/api/client';
	import type { LoadedModel } from '$lib/gltf/load';
	import { figureJoints, skinSlots } from '$lib/inspect';
	import CharacterPanel from './CharacterPanel.svelte';
	import Dock from './Dock.svelte';
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
	const sub = $derived(origin ? `${model.name} · ${origin}` : model.name);
</script>

<Dock title="Inspect" {sub} icon={PanelRight} side="right">
	<ModelCard {model} {origin} />
	{#if model.character}
		{#key model.character}
			<CharacterPanel rig={model.character} />
		{/key}
	{/if}
	<AnimCard {states} {clip} {refused} loading={loadingSlot} {activeSlot} {onplay} />
	<BonesCard {joints} bind:bones />
	<SkinsCard {slots} />
</Dock>
