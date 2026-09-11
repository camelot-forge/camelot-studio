<script lang="ts">
	import Header from '$lib/components/Header.svelte';
	import DropZone from '$lib/components/DropZone.svelte';
	import Library from '$lib/components/Library.svelte';
	import Viewer from '$lib/components/Viewer.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { loadFromFiles, loadFromGlb, type LoadedModel } from '$lib/gltf/load';
	import { api, type AssetSummary } from '$lib/api/client';
	import { LoaderCircle } from '@lucide/svelte';

	let model = $state<LoadedModel | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let selectedId = $state<string | null>(null);

	async function onFiles(files: File[]) {
		loading = true;
		error = null;
		selectedId = null;
		try {
			model = await loadFromFiles(files);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function onSelect(asset: AssetSummary) {
		loading = true;
		error = null;
		selectedId = asset.id;
		try {
			const res = await fetch(api.modelUrl(asset.id));
			if (!res.ok) throw new Error(`model export failed (${res.status})`);
			const buf = await res.arrayBuffer();
			const loaded = await loadFromGlb(buf, asset.name.replace(/\.nif$/i, '.glb'));
			try {
				const skin = await fetch(api.skinUrl(asset.id));
				if (skin.ok) loaded.skinJson = await skin.json();
			} catch {
				/* sidecar is optional */
			}
			model = loaded;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function reset() {
		model = null;
		error = null;
		selectedId = null;
	}
</script>

<div class="app">
	<Header onReset={reset} hasModel={model !== null} />

	<main class="stage">
		<Library onselect={onSelect} {selectedId} />

		<div class="content">
			{#if loading}
				<div class="center"><LoaderCircle class="spin" size={22} /> Loading…</div>
			{:else if model}
				<Viewer {model} />
				<Sidebar {model} />
			{:else}
				<div class="center">
					{#if error}<p class="err">{error}</p>{/if}
					<DropZone onfiles={onFiles} />
				</div>
			{/if}
		</div>
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
	.content {
		flex: 1;
		display: flex;
		min-width: 0;
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
		color: var(--text-3);
	}
	.err {
		color: var(--danger-text);
		background: var(--danger-tint);
		border: 1px solid var(--danger-border);
		border-radius: var(--r-card);
		padding: 10px 14px;
	}
	:global(.spin) {
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
