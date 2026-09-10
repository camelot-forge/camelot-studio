<script lang="ts">
	import Header from '$lib/components/Header.svelte';
	import DropZone from '$lib/components/DropZone.svelte';
	import Viewer from '$lib/components/Viewer.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { loadFromFiles, type LoadedModel } from '$lib/gltf/load';

	let model = $state<LoadedModel | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);

	async function onFiles(files: File[]) {
		loading = true;
		error = null;
		try {
			model = await loadFromFiles(files);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function reset() {
		model = null;
		error = null;
	}
</script>

<div class="app">
	<Header onReset={reset} hasModel={model !== null} />

	<main class="stage">
		{#if loading}
			<div class="center">Loading…</div>
		{:else if model}
			<Viewer {model} />
			<Sidebar {model} />
		{:else}
			<div class="center">
				{#if error}<p class="err">{error}</p>{/if}
				<DropZone onfiles={onFiles} />
			</div>
		{/if}
	</main>
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		height: 100dvh;
	}
	.stage {
		flex: 1;
		display: flex;
		min-height: 0;
	}
	.center {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 16px;
		padding: 24px;
	}
	.err {
		color: var(--danger-text);
		background: var(--danger-tint);
		border: 1px solid var(--danger-border);
		border-radius: var(--r-card);
		padding: 10px 14px;
	}
</style>
