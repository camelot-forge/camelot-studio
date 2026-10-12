<script lang="ts">
	// The world viewer: browse the client's regions and zones, see the terrain
	// the client renders, the fixtures it places, its bounds, water and jumps —
	// and pick an object to inspect and recentre on (the editor's first slice).
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import {
		api,
		type EditorEdit,
		type EditorState,
		type WorldFixture,
		type WorldIndex,
		type WorldSpawnDoor,
		type WorldSpawnMob,
		type WorldSpawnObject,
		type WorldSpawns,
		type WorldZone
	} from '$lib/api/client';
	import {
		WorldViewer,
		type EditKind,
		type EditMode,
		type RowPick,
		type RowProposal
	} from '$lib/world/viewer';
	import { LoaderCircle, Crosshair } from '@lucide/svelte';

	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let viewer: WorldViewer | null = null;
	let index = $state<WorldIndex | null>(null);
	let regionId = $state<number | null>(null);
	let zoneId = $state<number | null>(null);
	let zone = $state<WorldZone | null>(null);
	let spawns = $state<WorldSpawns | null>(null);
	let status = $state('loading the client index…');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let toggles = $state({
		ground: true,
		fixtures: true,
		spawns: true,
		statics: true,
		doors: true,
		patrols: true,
		bounds: false,
		water: true,
		jumps: false
	});
	let query = $state('');
	let fixtureQuery = $state('');
	let selected = $state<WorldFixture | null>(null);

	// The editor (6.3): the mode, the snap, and the authoring session's state.
	const modes = [
		{ id: 'off', label: 'select', title: 'click to select; no gizmo' },
		{
			id: 'translate',
			label: 'move',
			title: 'drag the handles (a ground row follows the terrain)'
		},
		{ id: 'rotate', label: 'rotate', title: 'A, degrees, about the vertical axis' },
		{ id: 'scale', label: 'scale', title: 'Scale, percent' },
		{ id: 'add', label: 'add', title: 'click the ground, then pick a catalog entry' }
	] as const;
	let editMode = $state<EditMode>('off');
	let snap = $state(true);
	let addPoint = $state<{ x: number; y: number; z: number } | null>(null);
	let addQuery = $state('');
	let edit = $state<EditorState | null>(null);
	let editStatus = $state<string | null>(null);
	let editError = $state<string | null>(null);
	let busyEdit = $state(false);

	// The server rows (B2/W-4's first half): one row selected for editing, the
	// rows the selected fixture would carry, and whether to carry them.
	type SpawnRowOf = WorldSpawnMob | WorldSpawnObject | WorldSpawnDoor;
	let selectedRow = $state<RowPick | null>(null);
	let proposals = $state<RowProposal[]>([]);
	let carry = $state(true);

	const editOps = $derived(edit?.zones.find((z) => z.id === zoneId)?.ops.length ?? 0);
	const editPatches = $derived((edit?.rows ?? []).flatMap((entry) => entry.patches));
	const editRows = $derived(editPatches.length);
	// Written = in the server's database; the rest is the journal's promise.
	const writtenRows = $derived(editPatches.filter((patch) => patch.applied === true).length);
	const editClient = $derived(edit?.client ?? null);
	// The client's half is the client's own authority: `revert` cannot touch it.
	const clientNote = $derived.by(() => {
		const client = edit?.client;
		if (!client) return 'client: unknown';
		if (!client.installed) return `client: no ${client.mod} half installed`;
		const claims = client.claims.length;
		return `client: ${client.mod} installed (${claims} claim${claims === 1 ? '' : 's'})`;
	});
	const selectedSpawn = $derived(selectedRow && spawns ? rowOf(spawns, selectedRow) : null);
	// What the proposal is made of, named: a castle's box holds its doors *and*
	// everyone standing in the courtyard, and the operator decides.
	const carriedNote = $derived.by(() => {
		const counts: Record<string, number> = {};
		for (const row of proposals) counts[row.table] = (counts[row.table] ?? 0) + 1;
		const names: Record<string, string> = { mob: 'mobs', worldobject: 'statics', door: 'doors' };
		return Object.entries(counts)
			.map(([table, count]) => `${count} ${names[table] ?? table}`)
			.join(' · ');
	});
	const catalogRows = $derived.by(() => {
		if (!zone) return [];
		const needle = addQuery.trim().toLowerCase();
		return zone.catalog
			.filter(
				(entry) =>
					!needle ||
					entry.filename.toLowerCase().includes(needle) ||
					entry.name.toLowerCase().includes(needle)
			)
			.slice(0, 200);
	});

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
		viewer.onEdit = (kind, fixture) => void commitDrag(kind, fixture);
		viewer.onRowMove = (pick) => void commitRow(pick);
		viewer.onPick = (point) => {
			addPoint = point;
		};
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
		api
			.worldEdit()
			.then((state) => {
				edit = state;
			})
			.catch(() => {
				// an older daemon without the editor routes: the panel stays hidden
				edit = null;
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

	$effect(() => {
		viewer?.setEditMode(editMode, snap);
	});

	async function open(zoneRow: { id: number; name: string; available: boolean }) {
		if (!viewer || !zoneRow.available || busy) return;
		busy = true;
		error = null;
		status = `loading ${zoneRow.name}…`;
		zoneId = zoneRow.id;
		selected = null;
		selectedRow = null;
		proposals = [];
		spawns = null;
		try {
			const [loaded, rows] = await Promise.all([
				api.worldZone(zoneRow.id),
				api.worldSpawns(zoneRow.id).catch(() => null)
			]);
			zone = loaded;
			spawns = rows;
			await viewer.show(zoneRow.id, loaded);
			if (rows) viewer.showSpawns(rows);
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
				spawns: rows?.counts ?? null,
				spawnSource: rows?.source ?? null,
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
			// The editor, scriptable: the verification harness drives the same
			// code path the panel does, never a private shortcut.
			(window as unknown as { __worldEdit?: (op: object) => Promise<object | null> }).__worldEdit =
				async (op: object) => {
					await applyEdit(op as Record<string, unknown>);
					return edit;
				};
			(
				window as unknown as {
					__worldEditMode?: (mode: EditMode, snapOn?: boolean) => void;
				}
			).__worldEditMode = (mode: EditMode, snapOn?: boolean) => {
				editMode = mode;
				if (snapOn !== undefined) snap = snapOn;
			};
			(window as unknown as { __worldEditor?: () => object }).__worldEditor = () => ({
				mode: editMode,
				mops: editOps,
				rows: editRows,
				written: writtenRows,
				client: editClient,
				row: selectedRow,
				carried: proposals.map((row) => ({
					table: row.table,
					key: row.key,
					name: row.name,
					distance: Math.round(row.distance)
				})),
				carry,
				status: editStatus,
				error: editError,
				pack: edit?.pack ?? null,
				add: addPoint
			});
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
		// One row, one object: a pick hands back the viewer's plain object, the
		// lists hand back the `$state` proxy — settle on the page's own row so
		// the inspector, the viewer and every later edit talk about the same one.
		const known =
			fixture && zone ? (zone.fixtures.find((row) => row.id === fixture.id) ?? fixture) : fixture;
		selected = known;
		selectedRow = null;
		viewer?.select(known);
		// The rows this fixture would take with it (B2): proposed from its own
		// meshes' boxes, shown before anything is dragged, sent with the op.
		proposals = known ? (viewer?.carriedRows(known.id) ?? []) : [];
	}

	function selectRow(pick: RowPick | null) {
		selectedRow = pick;
		if (pick) selected = null;
		viewer?.selectRow(pick);
	}

	function rowOf(rows: WorldSpawns, pick: RowPick): SpawnRowOf | null {
		if (pick.table === 'mob') return rows.mobs.find((row) => row.id === pick.id) ?? null;
		if (pick.table === 'worldobject') return rows.objects.find((row) => row.id === pick.id) ?? null;
		return rows.doors.find((row) => row.id === pick.id) ?? null;
	}

	function focusFixture(fixture: WorldFixture) {
		viewer?.focusFixture(fixture);
	}

	async function reloadFixtures() {
		if (zoneId === null || !viewer) return;
		const loaded = await api.worldZone(zoneId);
		zone = loaded;
		await viewer.rebuildFixtures(zoneId, loaded);
	}

	/** The server's rows as the daemon now holds them: the journal's patches are
	 * part of what the zone *is* (a moved door is drawn where it was recorded),
	 * and a rebased payload re-proposes the pairs a fixture carries. */
	async function reloadSpawns() {
		if (zoneId === null || !viewer) return;
		const rows = await api.worldSpawns(zoneId).catch(() => null);
		if (!rows) return;
		spawns = rows;
		viewer.showSpawns(rows);
		if (selectedRow) viewer.selectRow(selectedRow);
		if (selected) proposals = viewer.carriedRows(selected.id);
	}

	/** One edit, through the daemon (D2): the answer is the row as it stands,
	 * plus whatever server rows the op carried (B2). */
	async function applyEdit(op: Record<string, unknown>) {
		if (zoneId === null || busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			const result = await api.worldEditApply(zoneId, op);
			if (op.kind === 'add' || op.kind === 'remove' || op.kind === 'row-remove') {
				await reloadFixtures();
			} else if (result.fixture) {
				const row = result.fixture;
				const shown = zone?.fixtures.find((fixture) => fixture.id === row.id);
				if (shown) {
					Object.assign(shown, row);
					viewer?.updateFixture(shown);
				}
			}
			// A patch is part of what the zone is now: the viewer must show the
			// rows where the journal puts them, not where the database has them.
			if (result.patches.length) await reloadSpawns();
			editStatus = describe(op, result);
			edit = await api.worldEdit();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
			await reloadFixtures(); // the daemon's state wins after a refusal
			await reloadSpawns();
		} finally {
			busyEdit = false;
		}
	}

	function describe(op: Record<string, unknown>, result: EditorEdit): string {
		const id = result.fixture?.id ?? op.id;
		if (op.kind === 'remove') return `removed #${id}`;
		if (op.kind === 'add' && result.fixture)
			return `added #${result.fixture.id} ${result.fixture.name}`;
		if (op.kind === 'row-remove') return `removed ${op.table} row ${op.key}`;
		if (op.kind === 'row-move') return `moved ${op.table} row ${op.key}`;
		const rows = result.patches.length
			? ` · ${result.patches.length} server row${result.patches.length > 1 ? 's' : ''}`
			: '';
		return `${op.kind} #${id}${rows}`;
	}

	/** A gizmo drag ended: the viewer already wrote the fields locally; the
	 * daemon journals them (it fills `expect` from the row it holds). */
	async function commitDrag(kind: EditKind, fixture: WorldFixture) {
		// The pairs the user was shown when the fixture was picked: the rows of
		// the structure as it stood, which is what a move has to carry. A scale
		// moves nothing, so it carries nothing either.
		const rows =
			carry && kind !== 'scale' ? proposals.map((row) => ({ table: row.table, key: row.key })) : [];
		await applyEdit({
			kind,
			id: fixture.id,
			x: fixture.x,
			y: fixture.y,
			z: fixture.z,
			angle: fixture.angle,
			scale: fixture.scale,
			...(rows.length ? { rows } : {})
		});
	}

	/** A gizmo drag on a server row ended, or a ground click placed one: the
	 * viewer already moved the point locally; the daemon journals the patch. */
	async function commitRow(pick: RowPick) {
		if (!spawns) return;
		const row = rowOf(spawns, pick);
		if (!row) return;
		await applyEdit({
			kind: 'row-move',
			table: pick.table,
			key: pick.id,
			x: row.x,
			y: row.y,
			heading: row.heading
		});
	}

	async function removeRow() {
		if (!selectedRow) return;
		const pick = selectedRow;
		await applyEdit({ kind: 'row-remove', table: pick.table, key: pick.id });
		selectRow(null);
	}

	async function undo() {
		if (zoneId === null || busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			const result = await api.worldEditUndo(zoneId);
			editStatus = result.op.action
				? `undid ${result.op.action} #${result.op.id}`
				: 'nothing to undo';
			await reloadFixtures();
			await reloadSpawns();
			edit = await api.worldEdit();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	async function removeSelected() {
		if (!selected) return;
		const id = selected.id;
		selectFixture(null);
		await applyEdit({ kind: 'remove', id });
	}

	async function addCatalog(entry: { id: number; name: string; filename: string }) {
		if (!addPoint) return;
		await applyEdit({
			kind: 'add',
			nif_id: entry.id,
			x: addPoint.x,
			y: addPoint.y,
			name: entry.name
		});
	}

	async function build() {
		if (busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			const result = await api.worldModBuild();
			editStatus = result.pack
				? `built ${result.pack}`
				: `journaled ${result.patches} server row patch${result.patches === 1 ? '' : 'es'}`;
			edit = await api.worldEdit();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	async function apply() {
		if (busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			const result = await api.worldModApply();
			const mod = (result.install ?? {}).mod ?? edit?.mod.id ?? '';
			editStatus = result.install
				? `installed ${mod}`
				: `wrote ${result.patches} server row${result.patches === 1 ? '' : 's'} (no client half)`;
			await reloadSpawns();
			edit = await api.worldEdit();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	async function revert() {
		if (busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			await api.worldModRevert();
			editStatus = 'journal cleared (the client half, if installed, stays)';
			await reloadFixtures();
			await reloadSpawns();
			edit = await api.worldEdit();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	/** Everything back, the client half included: the client records what it
	 * carries, so a journal-only revert would leave it showing an edit nothing
	 * else knows about (the operator hit exactly that). */
	async function restore() {
		if (busyEdit) return;
		busyEdit = true;
		editError = null;
		try {
			const result = await api.worldModRestore();
			const half = result.client.installed
				? `${result.client.mod} removed from the client`
				: 'no client half to remove';
			const rows = result.rows
				? ` · ${result.rows} server row${result.rows === 1 ? '' : 's'} put back`
				: '';
			editStatus = `restored: ${half}${rows}`;
			edit = result.state;
			selectFixture(null);
			selectRow(null);
			await reloadFixtures();
			await reloadSpawns();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	function onCanvasClick(event: MouseEvent) {
		if (!viewer || !canvasEl) return;
		if (viewer.editing) return; // the gizmo owns this click
		const rect = canvasEl.getBoundingClientRect();
		const ndcX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
		const ndcY = -(((event.clientY - rect.top) / rect.height) * 2 - 1);
		if (editMode === 'add') {
			addPoint = viewer.pickGround(ndcX, ndcY);
			return;
		}
		const picked = viewer.pick(ndcX, ndcY);
		if (picked) {
			selectFixture(picked.fixture);
			return;
		}
		const row = viewer.pickRow(ndcX, ndcY);
		if (row) {
			selectRow(row);
			return;
		}
		if (selectedRow) {
			// a ground click with a row in hand places it (the daemon's own rule
			// keeps its height above the terrain; the gizmo refines it after)
			const point = viewer.pickGround(ndcX, ndcY);
			if (point) {
				const pick = selectedRow;
				viewer.moveRow(pick, point);
				void commitRow(pick);
				return;
			}
		}
		selectFixture(null);
		selectRow(null);
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
					<dt>server</dt>
					<dd>
						{#if spawns}
							{spawns.counts.mob} mobs · {spawns.counts.worldobject} statics · {spawns.counts.door} doors
							· {spawns.counts.path} patrols
						{:else}
							(response missing)
						{/if}
					</dd>
				</dl>
				{#if zone.warnings.length || spawns?.warnings.length}
					<p class="warn">{[...zone.warnings, ...(spawns?.warnings ?? [])].join(' · ')}</p>
				{/if}
			</section>

			<section class="layers">
				<label><input type="checkbox" bind:checked={toggles.ground} /> ground</label>
				<label><input type="checkbox" bind:checked={toggles.fixtures} /> fixtures</label>
				<label><input type="checkbox" bind:checked={toggles.spawns} /> mobs</label>
				<label><input type="checkbox" bind:checked={toggles.statics} /> statics</label>
				<label><input type="checkbox" bind:checked={toggles.doors} /> doors</label>
				<label><input type="checkbox" bind:checked={toggles.patrols} /> patrols</label>
				<label><input type="checkbox" bind:checked={toggles.bounds} /> bounds</label>
				<label><input type="checkbox" bind:checked={toggles.water} /> water</label>
				<label><input type="checkbox" bind:checked={toggles.jumps} /> jumps</label>
			</section>

			{#if edit?.available}
				<section class="editor">
					<h2>Editor <span class="dim">{edit.mod.id}</span></h2>
					<div class="modes">
						{#each modes as mode (mode.id)}
							<button
								type="button"
								class:active={editMode === mode.id}
								title={mode.title}
								onclick={() => (editMode = mode.id)}
							>
								{mode.label}
							</button>
						{/each}
					</div>
					<label class="snap">
						<input type="checkbox" bind:checked={snap} /> snap — 256 units, 15°, 5%
					</label>
					{#if selected}
						<button class="danger" type="button" onclick={removeSelected}>
							remove #{selected.id}
						</button>
					{/if}
					<div class="modes">
						<button
							type="button"
							onclick={undo}
							disabled={(!editOps && !editRows) || busyEdit}
							title="drop the journal's last op (server rows first)"
						>
							undo ({editOps}{editRows ? ` +${editRows} rows` : ''})
						</button>
						<button
							type="button"
							onclick={build}
							disabled={busyEdit}
							title="assemble the authoring directory and run mod build"
						>
							build
						</button>
						<button
							type="button"
							onclick={apply}
							disabled={busyEdit}
							title="build, then install the client half"
						>
							apply
						</button>
						<button
							type="button"
							onclick={revert}
							disabled={busyEdit}
							title="empty the journal and put the server rows back (the installed client half stays)"
						>
							revert
						</button>
						<button
							class="danger"
							type="button"
							onclick={restore}
							disabled={busyEdit || (!editOps && !editRows && !editClient?.installed)}
							title="put everything back: uninstall the client half, put the server rows back, empty the journal"
						>
							restore all
						</button>
					</div>
					{#if editMode === 'add'}
						<p class="hint">
							{addPoint
								? `add at ${addPoint.x.toFixed(0)}, ${addPoint.y.toFixed(0)}`
								: 'click the ground to aim'}
						</p>
						<input type="search" bind:value={addQuery} placeholder="catalog: file or name" />
						<ul class="addlist">
							{#each catalogRows as entry (entry.id)}
								<li>
									<button
										type="button"
										disabled={!addPoint || busyEdit}
										onclick={() => addCatalog(entry)}
									>
										<span class="zname">{entry.filename}</span>
										<span class="zmeta">{entry.name}</span>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
					{#if edit.pack}
						<p class="hint">
							pack {(edit.pack.size / 1000).toFixed(0)} kB · {new Date(
								edit.pack.modified * 1000
							).toLocaleTimeString()}
						</p>
					{/if}
					{#if editClient}
						<p class="hint">{clientNote}</p>
					{/if}
					{#if editRows}
						<p class="hint">
							{editRows} server row patch{editRows === 1 ? '' : 'es'} in the journal{writtenRows
								? ` · ${writtenRows} written to the database`
								: ''}{editRows > writtenRows ? ` · ${editRows - writtenRows} pending` : ''}
						</p>
					{/if}
					{#if writtenRows || editClient?.installed}
						<p class="hint">
							the running game reads these at startup: in game, <code>/reload doors</code>,
							<code>/reload mob</code> and <code>/reload object</code> reread them (region {zone
								.info.region})
						</p>
					{/if}
					{#if editStatus}<p class="hint">{editStatus}</p>{/if}
					{#if editError}<p class="warn">{editError}</p>{/if}
				</section>
			{/if}

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
					{#if proposals.length}
						<label class="snap">
							<input type="checkbox" bind:checked={carry} /> carry {proposals.length} server row{proposals.length >
							1
								? 's'
								: ''}
						</label>
						<p class="hint">
							rows inside this object's own meshes: {carriedNote} — they move with it (B2)
						</p>
						<ul class="addlist">
							{#each proposals as row (row.table + ':' + row.key)}
								<li>
									<button
										type="button"
										onclick={() => selectRow({ table: row.table, id: row.key })}
										title="inspect this server row"
									>
										<span class="zname">{row.name || '(unnamed)'}</span>
										<span class="zmeta">{row.table} {row.key}</span>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				{:else}
					<p class="hint">
						click an object in the scene, or a row below — a coloured dot is a server row,
						double-click recentres
					</p>
				{/if}
			</section>

			{#if selectedRow && selectedSpawn}
				<section class="picked">
					<h2>Server row</h2>
					<dl>
						<dt>table</dt>
						<dd>{selectedRow.table} <span class="dim">{selectedRow.id}</span></dd>
						<dt>name</dt>
						<dd>{selectedSpawn.name || '(unnamed)'}</dd>
						<dt>at</dt>
						<dd>
							{selectedSpawn.x.toFixed(0)}, {selectedSpawn.y.toFixed(0)}, {selectedSpawn.z.toFixed(
								0
							)}
						</dd>
						<dt>heading</dt>
						<dd>{selectedSpawn.heading} <span class="dim">of 4096</span></dd>
					</dl>
					<div class="modes">
						<button
							class="danger"
							type="button"
							onclick={removeRow}
							disabled={busyEdit}
							title="the row is dropped on apply; revert puts it back whole"
						>
							remove row
						</button>
					</div>
					<p class="hint">
						drag the gizmo, or click the ground, to move it — it keeps its height above the terrain
					</p>
				</section>
			{/if}

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
	.editor .modes {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
	}
	.editor .modes button {
		width: auto;
		padding: 3px 8px;
		border-color: #333;
		font-size: 11px;
	}
	.editor .snap {
		display: flex;
		gap: 4px;
		align-items: center;
		font-size: 11px;
		color: #9aa4a0;
	}
	.editor button.danger {
		width: auto;
		align-self: flex-start;
		border-color: #7a2f2f;
		color: #ff8f8f;
		font-size: 11px;
	}
	.editor input[type='search'] {
		font-size: 11px;
	}
	.editor .addlist {
		max-height: 22dvh;
		overflow-y: auto;
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
