<script lang="ts">
	// The world viewer: browse the client's regions and zones, see the terrain
	// the client renders, the fixtures it places, its bounds, water and jumps.
	// Phase 6.2 — read-only; editing is 6.3.
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, type WorldIndex, type WorldZone } from '$lib/api/client';
	import { WorldViewer } from '$lib/world/viewer';
	import { LoaderCircle } from '@lucide/svelte';

	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let viewer: WorldViewer | null = null;
	let index = $state<WorldIndex | null>(null);
	let regionId = $state<number | null>(null);
	let zoneId = $state<number | null>(null);
	let zone = $state<WorldZone | null>(null);
	let status = $state('loading the client index…');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let toggles = $state({ ground: true, fixtures: true, bounds: false, water: true, jumps: false });
	let query = $state('');

	// Zones of the picked region, filtered by the search box; a zone the
	// client does not ship is listed but cannot be opened.
	const region = $derived(
		index && regionId !== null ? index.regions.find((r) => r.id === regionId) : null
	);
	const zoneRows = $derived.by(() => {
		if (!index || !region) return [];
		const needle = query.trim().toLowerCase();
		return region.zones
			.map((id) => index!.zones.find((z) => z.id === id)!)
			.filter(
				(z) => z && (!needle || z.name.toLowerCase().includes(needle) || String(z.id) === needle)
			);
	});

	onMount(() => {
		if (!canvasEl) return;
		viewer = new WorldViewer(canvasEl);
		viewer.start();
		const observer = new ResizeObserver(() => viewer?.resize());
		observer.observe(canvasEl.parentElement!);
		api
			.worldIndex()
			.then((loaded) => {
				index = loaded;
				const first = loaded.regions.find((r) => r.available > 0) ?? loaded.regions[0];
				regionId = first?.id ?? null;
				status = `${loaded.zones.length} zones in ${loaded.regions.length} regions`;
			})
			.catch((e) => {
				error = e instanceof Error ? e.message : String(e);
				status = 'no client';
			});
		return () => {
			observer.disconnect();
			viewer?.dispose();
			viewer = null;
		};
	});

	$effect(() => {
		viewer?.setToggles(toggles);
	});

	async function open(zoneRow: { id: number; name: string; available: boolean }) {
		if (!viewer || !zoneRow.available || busy) return;
		busy = true;
		error = null;
		status = `loading ${zoneRow.name}…`;
		zoneId = zoneRow.id;
		try {
			const loaded = await api.worldZone(zoneRow.id);
			zone = loaded;
			await viewer.show(zoneRow.id, loaded);
			status = `${loaded.info.name} — ${loaded.fixtures.length} fixtures, ${loaded.catalog.length} catalog entries`;
			// The headless screenshot harness reads this; it also makes the load
			// deterministic to wait on.
			(window as unknown as { __world?: object }).__world = {
				zone: loaded.info.id,
				name: loaded.info.name,
				fixtures: loaded.fixtures.length,
				catalog: loaded.catalog.length,
				bounds: loaded.bounds.length,
				water: loaded.water.length,
				jumps: loaded.jumps.length,
				layers: loaded.patches?.textures.length ?? 0,
				...viewer.stats()
			};
			(window as unknown as { __worldStats?: () => object }).__worldStats = () => ({
				...viewer!.stats()
			});
			(
				window as unknown as { __worldFocus?: (x: number, y: number, d?: number) => void }
			).__worldFocus = (x, y, d) => viewer?.focus(x, y, d);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			status = 'load failed';
		} finally {
			busy = false;
		}
	}

	function kindLabel(kind: string): string {
		return kind === 'normal' ? 'outdoor' : kind;
	}
</script>

<div class="world">
	<aside>
		<h1><a href={resolve('/')}>Camelot Forge</a> <span class="tag">world</span></h1>
		<p class="status" class:err={error !== null}>{error ?? status}</p>

		{#if index}
			<label class="field">
				<span>Region</span>
				<select bind:value={regionId}>
					{#each index.regions as r (r.id)}
						<option value={r.id}>
							region {r.id} — {r.zones.length} zones{r.available === 0 ? ' (none shipped)' : ''}
						</option>
					{/each}
				</select>
			</label>
			<label class="field">
				<span>Search</span>
				<input type="search" bind:value={query} placeholder="name or id" />
			</label>

			<ul class="zones">
				{#each zoneRows as z (z.id)}
					<li>
						<button
							type="button"
							class:active={z.id === zoneId}
							class:unavailable={!z.available}
							onclick={() => open(z)}
							title={z.available ? z.name : `${z.name} — not shipped by this client`}
						>
							<span class="zname">{z.name || '(unnamed)'}</span>
							<span class="zmeta">{z.kind !== 'normal' ? kindLabel(z.kind) : ''} {z.id}</span>
						</button>
					</li>
				{/each}
			</ul>
		{/if}

		{#if zone}
			<section class="info">
				<h2>{zone.info.name}</h2>
				<dl>
					<dt>zone</dt>
					<dd>{zone.info.id} · {kindLabel(zone.info.kind)}</dd>
					<dt>world</dt>
					<dd>
						{zone.info.world_rect[0]}, {zone.info.world_rect[1]} (+{zone.info.world_rect[2]}²)
					</dd>
					<dt>fixtures</dt>
					<dd>{zone.fixtures.length}</dd>
					<dt>catalog</dt>
					<dd>{zone.catalog.filter((c) => c.mesh).length} / {zone.catalog.length} with meshes</dd>
					<dt>bounds</dt>
					<dd>{zone.bounds.length} · water {zone.water.length} · jumps {zone.jumps.length}</dd>
					<dt>ground</dt>
					<dd>{zone.patches?.textures.length ?? 0} layer sheets</dd>
				</dl>
				{#if zone.warnings.length}
					<p class="warn">{zone.warnings.join(' · ')}</p>
				{/if}
			</section>

			<section class="layers">
				<label><input type="checkbox" bind:checked={toggles.ground} /> ground</label>
				<label><input type="checkbox" bind:checked={toggles.fixtures} /> fixtures</label>
				<label><input type="checkbox" bind:checked={toggles.bounds} /> bounds</label>
				<label><input type="checkbox" bind:checked={toggles.water} /> water</label>
				<label><input type="checkbox" bind:checked={toggles.jumps} /> jumps</label>
			</section>
		{/if}
	</aside>

	<div class="stage">
		<canvas bind:this={canvasEl}></canvas>
		{#if busy}
			<div class="busy"><LoaderCircle class="spin" size={20} /> loading…</div>
		{/if}
	</div>
</div>

<style>
	.world {
		display: flex;
		height: 100dvh;
		background: var(--bg, #141414);
		color: var(--text-1, #e8e8e8);
	}
	aside {
		width: 320px;
		min-width: 260px;
		overflow-y: auto;
		padding: 14px;
		border-right: 1px solid #2a2a2a;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.stage {
		flex: 1;
		position: relative;
		min-width: 0;
	}
	canvas {
		width: 100%;
		height: 100%;
		display: block;
	}
	h1 {
		font-size: 15px;
		margin: 0;
	}
	h1 a {
		color: inherit;
		text-decoration: none;
	}
	h1 a:hover {
		text-decoration: underline;
	}
	.tag {
		color: #2dd4a7;
		font-weight: 400;
	}
	h2 {
		font-size: 14px;
		margin: 8px 0 4px;
	}
	.status {
		margin: 0;
		font: 11px/1.4 monospace;
		color: #9aa4a0;
	}
	.status.err {
		color: #ff8f8f;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 3px;
		font-size: 11px;
		color: #9aa4a0;
	}
	select,
	input[type='search'] {
		background: #1c1f1e;
		color: inherit;
		border: 1px solid #333;
		border-radius: 5px;
		padding: 5px 6px;
		font: inherit;
	}
	.zones {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
		max-height: 40dvh;
		overflow-y: auto;
	}
	.zones button {
		width: 100%;
		display: flex;
		justify-content: space-between;
		gap: 8px;
		background: none;
		border: 1px solid transparent;
		border-radius: 5px;
		color: inherit;
		padding: 4px 6px;
		cursor: pointer;
		font: inherit;
		text-align: left;
	}
	.zones button:hover {
		background: #1e2320;
	}
	.zones button.active {
		border-color: #2dd4a7;
		background: #17302a;
	}
	.zones button.unavailable {
		opacity: 0.45;
		cursor: default;
	}
	.zmeta {
		color: #7f8a86;
		font: 10px monospace;
	}
	.info dl {
		display: grid;
		grid-template-columns: 64px 1fr;
		gap: 2px 8px;
		margin: 0;
		font-size: 11px;
	}
	.info dt {
		color: #7f8a86;
	}
	.info dd {
		margin: 0;
	}
	.warn {
		font: 10px/1.4 monospace;
		color: #ffd166;
	}
	.layers {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		font-size: 11px;
	}
	.layers label {
		display: flex;
		gap: 4px;
		align-items: center;
	}
	.busy {
		position: absolute;
		top: 10px;
		left: 12px;
		display: flex;
		gap: 8px;
		align-items: center;
		font: 12px monospace;
		color: #2dd4a7;
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
