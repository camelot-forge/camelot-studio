<script lang="ts">
	import { FileBox } from '@lucide/svelte';
	import { figureJoints, rigBones } from '$lib/inspect';
	import type { LoadedModel } from '$lib/gltf/load';

	interface Props {
		model: LoadedModel;
		/** How the daemon's index reached the file ("loose" | "mpak"), when it did. */
		origin?: string | null;
	}
	let { model, origin = null }: Props = $props();

	const bones = $derived(rigBones(model.scene, model.skinJson));
	const skins = $derived(model.meshTextures.size);
	const bonesTitle = $derived(
		figureJoints(model.skinJson).length > 0
			? 'the joints named by the .skin.json file (the NIF’s own rig)'
			: 'the bones the exported glTF carries (no .skin.json file)'
	);
</script>

<section class="card">
	<h3>
		<FileBox size={14} /> Model
		{#if origin}<span class="card-meta">{origin}</span>{/if}
	</h3>
	<dl class="meta">
		<div>
			<dt>File</dt>
			<dd class="nums" title={model.name}>{model.name}</dd>
		</div>
		<div>
			<dt>Meshes</dt>
			<dd class="nums">{model.meshCount}</dd>
		</div>
		<div>
			<dt>Triangles</dt>
			<dd class="nums">{model.triangleCount.toLocaleString()}</dd>
		</div>
		<div>
			<dt>Bones</dt>
			<dd class="nums" title={bonesTitle}>{bones}</dd>
		</div>
		<div>
			<dt>Skins</dt>
			<dd class="nums" title="the meshes that use a client texture">{skins}</dd>
		</div>
	</dl>
</section>
