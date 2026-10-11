<script lang="ts">
	// The world viewer: browse the client's regions and zones, see the terrain
	// the client renders, the fixtures it places, its bounds, water and jumps —
	// and pick an object to inspect and recentre on (the editor's first slice).
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, type WorldFixture, type WorldIndex, type WorldZone } from '$lib/api/client';
	import { WorldViewer } from '$lib/world/viewer';
	import { LoaderCircle, Crosshair } from '@lucide/svelte';

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
	let fixtureQuery = $state('');
	let selected = $state<WorldFixture | null>(null);

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
	// The zone's fixtures, searchable by name, catalog file or id — the way to
	// find one pine among 1536 without hunting for it on screen.
	const fixtureRows = $derived.by(() => {
		if (!zone) return [];
		const catalog = new Map(zone.catalog.map((entry) => [entry.id, entry]));
		const needle = fixtureQuery.trim().toLowerCase();
		const rows = zone.fixtures.filter((fixture) => {
			if (!needle) return true;
			const entry = catalog.get(fixture.nif_id);
			return (
				fixture.name.toLowerCase().includes(needle) ||
				String(fixture.id) === needle ||
				(entry?.filename.toLowerCase().includes(needle) ?? false)
			);
		});
		return rows.slice(0, 300);
	});
	const catalogOf = $derived(new Map((zone?.catalog ?? []).map((entry) => [entry.id, entry])));

	onMount(() => {
		if (!canvasEl) return;
		viewer = new WorldViewer(canvasEl);
		viewer.start();
		const observer = new ResizeObserver(() => viewer?.resize());
		observer.observe(canvasEl.parentElement!);
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'f' && selected) focusFixture(selected);
			if (event.key === 'Escape') selectFixture(null);
		};
		window.addEventListener('keydown', onKey);
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
			window.removeEventListener('keydown', onKey);
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
		selected = null;
		try {
			const loaded = await api.worldZone(zoneRow.id);
			zone = loaded;
			await viewer.show(zoneRow.id, loaded);
			status = `${loaded.info.name} — ${loaded.fixtures.length} fixtures, ${loaded.catalog.length} catalog entries`;
			// The headless screenshot harness reads this; it also makes the
			// load deterministic to wait on.
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
			(window as unknown as { __worldSelect?: (id: number) => void }).__worldSelect = (
				id: number
			) => {
				const found = loaded.fixtures.find((fixture) => fixture.id === id);
				if (found) selectFixture(found);
			};
			(
				window as unknown as { __worldFocus?: (x: number, y: number, d?: number) => void }
			).__worldFocus = (x, y, d) => viewer?.focus(x, y, d);
			(
				window as unknown as {
					__worldLook?: (x: number, y: number, az: number, d?: number) => void;
				}
			).__worldLook = (x, y, az, d) => viewer?.lookFrom(x, y, az, d);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			status = 'load failed';
		} finally {
			busy = false;
		}
	}

	function selectFixture(fixture: WorldFixture | null) {
		selected = fixture;
		viewer?.select(fixture);
	}

	function focusFixture(fixture: WorldFixture) {
		viewer?.focusFixture(fixture);
	}

	function onCanvasClick(event: MouseEvent) {
		if (!viewer || !canvasEl) return;
		const rect = canvasEl.getBoundingClientRect();
		const ndcX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
		const ndcY = -(((event.clientY - rect.top) / rect.height) * 2 - 1);
		const picked = viewer.pick(ndcX, ndcY);
		selectFixture(picked ? picked.fixture : null);
	}

	function onCanvasDoubleClick(event: MouseEvent) {
		if (!viewer || !canvasEl) return;
		const rect = canvasEl.getBoundingClientRect();
		const ndcX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
		const ndcY = -(((event.clientY - rect.top) / rect.height) * 2 - 1);
		const picked = viewer.pick(ndcX, ndcY);
		if (picked) {
			selectFixture(picked.fixture);
			focusFixture(picked.fixture);
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
				<input type="search" bind:value={query} placeholder="zone name or id" />
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

			<section class="picked">
				<h2>Selected</h2>
				{#if selected}
					<dl>
						<dt>name</dt>
						<dd>{selected.name || '(unnamed)'} <span class="dim">#{selected.id}</span></dd>
						<dt>mesh</dt>
						<dd>{catalogOf.get(selected.nif_id)?.filename ?? `nif ${selected.nif_id}`}</dd>
						<dt>at</dt>
						<dd>{selected.x.toFixed(0)}, {selected.y.toFixed(0)}, {selected.z.toFixed(0)}</dd>
						<dt>angle</dt>
						<dd>{selected.angle.toFixed(0)}° · scale {selected.scale.toFixed(0)}%</dd>
						<dt>flags</dt>
						<dd>
							{selected.ground ? 'ground ' : ''}{selected.animate ? 'animate ' : ''}{selected.flip
								? 'flip '
								: ''}{selected.cave ? 'cave' : ''}
						</dd>
					</dl>
					<button class="focus" type="button" onclick={() => focusFixture(selected!)}>
						<Crosshair size={13} /> Focus (F)
					</button>
				{:else}
					<p class="hint">click an object in the scene, or a row below — double-click recentres</p>
				{/if}
			</section>

			<section class="fixtures">
				<label class="field">
					<span>Fixtures</span>
					<input type="search" bind:value={fixtureQuery} placeholder="name, file or id" />
				</label>
				<ul>
					{#each fixtureRows as fixture (fixture.id)}
						<li>
							<button
								type="button"
								class:active={fixture.id === selected?.id}
								onclick={() => selectFixture(fixture)}
								ondblclick={() => focusFixture(fixture)}
								title="click to select, double-click to focus"
							>
								<span class="zname">{fixture.name || '(unnamed)'}</span>
								<span class="zmeta">{fixture.x.toFixed(0)}, {fixture.y.toFixed(0)}</span>
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	</aside>

	<div class="stage">
		<canvas
			bind:this={canvasEl}
			onclick={onCanvasClick}
			ondblclick={onCanvasDoubleClick}
			aria-label="the zone, rendered; click to select an object"
		></canvas>
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
		width: 330px;
		min-width: 270px;
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
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.zones {
		max-height: 30dvh;
		overflow-y: auto;
	}
	.fixtures ul {
		max-height: 32dvh;
		overflow-y: auto;
	}
	button {
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
	button:hover {
		background: #1e2320;
	}
	button.active {
		border-color: #2dd4a7;
		background: #17302a;
	}
	button.unavailable {
		opacity: 0.45;
		cursor: default;
	}
	.zmeta {
		color: #7f8a86;
		font: 10px monospace;
	}
	.info dl,
	.picked dl {
		display: grid;
		grid-template-columns: 56px 1fr;
		gap: 2px 8px;
		margin: 0;
		font-size: 11px;
	}
	.info dt,
	.picked dt {
		color: #7f8a86;
	}
	.info dd,
	.picked dd {
		margin: 0;
	}
	.dim {
		color: #7f8a86;
	}
	.warn {
		font: 10px/1.4 monospace;
		color: #ffd166;
	}
	.hint {
		margin: 0;
		font: 10px/1.5 monospace;
		color: #7f8a86;
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
	button.focus {
		justify-content: center;
		gap: 6px;
		margin-top: 6px;
		border-color: #2dd4a7;
		color: #2dd4a7;
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
