<script lang="ts">
	import type { Mesh, MeshStandardMaterial } from 'three';
	import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
	import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
	import { Camera, Download, FileBox, FolderOpen, LoaderCircle } from '@lucide/svelte';
	import AppBar from '$lib/components/AppBar.svelte';
	import DropZone from '$lib/components/DropZone.svelte';
	import Inspector from '$lib/components/Inspector.svelte';
	import Library from '$lib/components/Library.svelte';
	import ProjectChip from '$lib/components/ProjectChip.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import Viewer, { type ViewerHandles } from '$lib/components/Viewer.svelte';
	import { loadFromFiles, loadFromGlb, type LoadedModel } from '$lib/gltf/load';
	import { api, type AnimClip, type AnimStateSummary, type AssetSummary } from '$lib/api/client';
	import { rigBones } from '$lib/inspect';

	let model = $state<LoadedModel | null>(null);
	let anim = $state<AnimClip | null>(null);
	let states = $state<AnimStateSummary[]>([]);
	/** Slots the daemon refused, in its own words. */
	let refused = $state<Record<string, string>>({});
	let loadingSlot = $state<string | null>(null);
	/** The slot on screen, when the operator picked one (the idle's resolver
	 * choice has no slot name of its own). */
	let activeSlot = $state<string | null>(null);
	let asset = $state<AssetSummary | null>(null);
	let bones = $state(false);
	let fps = $state(0);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let exporting = $state(false);
	let selectedId = $state<string | null>(null);
	let viewer = $state<ViewerHandles | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);

	const meshLabel = $derived(
		model ? `${model.meshCount} ${model.meshCount === 1 ? 'mesh' : 'meshes'}` : ''
	);
	const boneCount = $derived(model ? rigBones(model.scene, model.skinJson) : 0);

	function reset() {
		model = null;
		anim = null;
		states = [];
		refused = {};
		activeSlot = null;
		asset = null;
		selectedId = null;
		error = null;
		bones = false;
	}

	function open() {
		fileInput?.click();
	}

	// The viewer's two outputs are named functions, not inline closures: a fresh
	// closure each render would re-fire the viewer's effects (state written back
	// into the page is a render of the page).
	function onViewerFps(value: number) {
		fps = value;
	}

	function onViewerReady(handles: ViewerHandles) {
		viewer = handles;
	}

	async function onPick(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const files = Array.from(input.files ?? []);
		input.value = ''; // the same file twice in a row is not a change
		if (files.length) await onFiles(files);
	}

	async function onFiles(files: File[]) {
		loading = true;
		error = null;
		reset();
		try {
			model = await loadFromFiles(files);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function onSelect(picked: AssetSummary) {
		loading = true;
		error = null;
		reset();
		selectedId = picked.id;
		asset = picked;
		try {
			const res = await fetch(api.modelUrl(picked.id, true));
			if (!res.ok) throw new Error(`model export failed (${res.status})`);
			// Named after the file the operator picked, not the .glb the daemon
			// handed over: the chip and the Model card are about *their* figure.
			const loaded = await loadFromGlb(await res.arrayBuffer(), picked.name);
			try {
				const skin = await fetch(api.skinUrl(picked.id));
				if (skin.ok) loaded.skinJson = await skin.json();
			} catch {
				/* sidecar is optional */
			}
			model = loaded;
			// The idle and the figure's own set: the tables may name neither.
			const [clip, list] = await Promise.all([
				api.anim(picked.id).catch(() => null),
				api.states(picked.id).catch(() => [] as AnimStateSummary[])
			]);
			anim = clip;
			states = list;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	/** Play one of the figure's own states. A refusal is the daemon's answer —
	 * a pair whose channels do not bind comes back as a 404 with its reason,
	 * and the card says so instead of leaving the figure in its bind pose. */
	async function playState(slot: string) {
		if (!asset) return;
		loadingSlot = slot;
		try {
			anim = await api.animState(asset.id, slot);
			activeSlot = slot;
			delete refused[slot];
		} catch (e) {
			refused[slot] = e instanceof Error ? e.message : String(e);
		} finally {
			loadingSlot = null;
		}
	}

	/** The figure as it stands: geometry, skinning and the node tree. The
	 * client's sheets are BC-compressed DDS, which a glTF writer cannot
	 * re-encode — the clone goes out without them, the viewport keeps them. */
	async function exportGltf() {
		if (!model) return;
		exporting = true;
		error = null;
		try {
			const copy = cloneSkinned(model.scene);
			copy.position.set(0, 0, 0);
			copy.rotation.set(0, 0, 0);
			copy.scale.setScalar(1);
			copy.traverse((obj) => {
				const mesh = obj as Mesh;
				if (!mesh.isMesh) return;
				const source = mesh.material as MeshStandardMaterial;
				const own = source.clone();
				own.map = null;
				mesh.material = own;
			});
			const out = (await new GLTFExporter().parseAsync(copy, {
				binary: true,
				onlyVisible: false
			})) as ArrayBuffer;
			const stem = model.name.replace(/\.\w+$/, '');
			const url = URL.createObjectURL(new Blob([out], { type: 'model/gltf-binary' }));
			const link = document.createElement('a');
			link.href = url;
			link.download = `${stem}.glb`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			error = `export failed: ${e instanceof Error ? e.message : String(e)}`;
		} finally {
			exporting = false;
		}
	}
</script>

<div class="app">
	<AppBar
		section="figures"
		subtitle="figure viewer · glTF"
		contextIcon={model ? FileBox : null}
		contextLabel={model?.name ?? ''}
		contextMeta={meshLabel}
	>
		{#snippet actions()}
			<ProjectChip />
			{#if model}
				<button
					class="btn btn--icon"
					onclick={() => viewer?.snapshot()}
					title="snapshot — save the viewport as a PNG"
					aria-label="snapshot"
				>
					<Camera size={16} />
				</button>
			{/if}
			<button class="btn btn--secondary" onclick={open} title="open a figure from this machine">
				<FolderOpen size={15} /> Open…
			</button>
			{#if model}
				<button
					class="btn btn--secondary"
					onclick={exportGltf}
					disabled={exporting}
					title="export as glTF — geometry, skinning and the node tree; the client’s DDS sheets are not re-encoded"
				>
					<Download size={15} /> Export glTF
				</button>
			{/if}
			<input
				bind:this={fileInput}
				type="file"
				multiple
				accept=".gltf,.glb,.bin,.json"
				hidden
				onchange={onPick}
			/>
		{/snippet}
	</AppBar>

	<main class="stage">
		<Library onselect={onSelect} {selectedId} />

		<div class="content">
			{#if loading}
				<div class="center"><LoaderCircle class="spin" size={22} /> Loading…</div>
			{:else if model}
				<Viewer {model} {anim} onfps={onViewerFps} bind:bones onready={onViewerReady} />
				<Inspector
					{model}
					origin={asset?.origin ?? null}
					{states}
					clip={anim}
					{refused}
					{loadingSlot}
					{activeSlot}
					onplay={playState}
					bind:bones
				/>
			{:else}
				<div class="center">
					{#if error}<p class="err">{error}</p>{/if}
					<DropZone onfiles={onFiles} />
				</div>
			{/if}
		</div>
	</main>

	<StatusBar>
		{#snippet left()}
			{#if model}
				<span class="sb-strong sb-nums" title="frames the renderer drew, twice a second"
					>{fps} fps</span
				>
				<span class="sb-nums" title="the figure’s own counts"
					>{model.triangleCount.toLocaleString()} tris · {meshLabel} · {boneCount} bones</span
				>
			{:else}
				<span>no figure loaded</span>
			{/if}
		{/snippet}
		{#snippet center()}
			<span>drag: orbit · wheel: zoom · right-drag: pan · F: frame · space: play</span>
		{/snippet}
		{#snippet right()}
			{#if anim}
				<span class="sb-strong sb-nums" title="the clip on screen"
					>{anim.kfa} · {anim.duration.toFixed(2)} s</span
				>
			{/if}
			<span>viewer {__BUILD_STAMP__}</span>
		{/snippet}
	</StatusBar>
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
