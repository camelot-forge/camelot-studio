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
	/** The leading fixture of the selection (the panel edits it), and the count. */
	let selected = $state<WorldFixture | null>(null);
	let selectionCount = $state(0);

	// The editor (6.3): select vs add, the snap, and the session's state.
	const modes = [
		{ id: 'off', label: 'select', title: 'click to select, drag a box to select several' },
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
	/** The floating panel: closed until something is selected or the panel is
	 * asked for, and it remembers where the operator put it. */
	let panel = $state({ open: false, x: 0, y: 0, dragging: false });

	// The server rows (B2/W-4's first half): the leading row, the rows the
	// selected fixture would carry, and whether to carry them.
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
	/** Everything the journal holds, across the session's zones. */
	const sessionOps = $derived((edit?.zones ?? []).reduce((sum, zone) => sum + zone.ops.length, 0));
	const changes = $derived(sessionOps + editRows);
	/** Like a repository: the session is ahead of what was published (the op
	 * count at the last apply), or it has server rows nothing wrote yet. */
	const dirty = $derived(
		sessionOps !== ((edit?.published?.ops as number | undefined) ?? 0) || editRows > writtenRows
	);
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
		viewer.onEdit = (kind, fixtures) => void commitEdit(kind, fixtures);
		viewer.onRowEdit = (kind, rows) => void commitRows(kind, rows);
		viewer.onSelect = (selection) => selectFrom(selection);
		viewer.onPick = (point) => {
			addPoint = point;
		};
		const observer = new ResizeObserver(() => viewer?.resize());
		observer.observe(canvasEl.parentElement!);
		const onKey = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null;
			if (target && /input|select|textarea/i.test(target.tagName)) return;
			if (event.key === 'f') viewer?.focusSelection();
			if (event.key === 'Escape') selectFrom({ fixtures: [], rows: [] });
			if (event.key === 'Delete' || event.key === 'Backspace') void removeSelection();
		};
		window.addEventListener('keydown', onKey);
		const saved = window.localStorage.getItem('camelot.editorPanel');
		const parent = canvasEl.parentElement;
		if (saved) {
			const parsed = JSON.parse(saved) as { x: number; y: number };
			panel.x = parsed.x;
			panel.y = parsed.y;
		} else {
			// top right, out of the way of the zone's own middle
			panel.x = Math.max(12, (parent?.clientWidth ?? 900) - 372);
			panel.y = 12;
		}
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
		selectionCount = 0;
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
			(window as unknown as { __worldViewer?: WorldViewer }).__worldViewer = viewer;
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
				selection: {
					fixtures: viewer?.selectionView.fixtures.map((fixture) => fixture.id) ?? [],
					rows: viewer?.selectionView.rows.map((row) => row.id) ?? []
				},
				dirty,
				changes,
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

	/** The viewer's selection, mirrored for the panel: the leading fixture is
	 * what the fields edit, and its carried rows are what a move would take. */
	function selectFrom(selection: { fixtures: WorldFixture[]; rows: RowPick[] }) {
		const last = selection.fixtures[selection.fixtures.length - 1] ?? null;
		const known = last && zone ? (zone.fixtures.find((row) => row.id === last.id) ?? last) : last;
		selected = known;
		selectedRow =
			!known && selection.rows.length ? selection.rows[selection.rows.length - 1] : null;
		selectionCount = selection.fixtures.length + selection.rows.length;
		proposals = known ? (viewer?.carriedRows(known.id) ?? []) : [];
		if (selectionCount) panel.open = true;
	}

	/** The page's own selection paths (the lists) go through the viewer, so
	 * there is exactly one selection and one place that settles it. */
	function selectFixture(fixture: WorldFixture | null) {
		viewer?.select(fixture);
	}

	function selectRow(pick: RowPick | null) {
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
	async function applyEdit(op: Record<string, unknown>): Promise<boolean> {
		if (zoneId === null || busyEdit) return false;
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
					viewer?.updateFixture(shown, false);
				}
			}
			// A patch is part of what the zone is now: the viewer must show the
			// rows where the journal puts them, not where the database has them.
			if (result.patches.length) await reloadSpawns();
			editStatus = describe(op, result);
			edit = await api.worldEdit();
			return true;
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
			await reloadFixtures(); // the daemon's state wins after a refusal
			await reloadSpawns();
			return false;
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

	/** A gesture ended (or a field was typed into): the viewer already wrote the
	 * fields locally, the daemon journals them — one op per item, in order. */
	async function commitEdit(kind: EditKind, fixtures: WorldFixture[]) {
		// the daemon knows move/rotate/scale: a lift is a move with an explicit z
		const op = kind === 'height' ? 'move' : kind;
		for (const fixture of fixtures) {
			// The pairs the user was shown when the fixture was picked: the rows of
			// the structure as it stood, which is what a move has to carry. A scale
			// moves nothing, so it carries nothing either.
			const carried =
				carry && op !== 'scale'
					? (viewer?.carriedRows(fixture.id) ?? []).map((row) => ({
							table: row.table,
							key: row.key
						}))
					: [];
			const ok = await applyEdit({
				kind: op,
				id: fixture.id,
				x: fixture.x,
				y: fixture.y,
				z: fixture.z,
				angle: fixture.angle,
				scale: fixture.scale,
				...(carried.length ? { rows: carried } : {})
			});
			if (!ok) break;
		}
	}

	/** A row gesture ended (or the panel placed one): the daemon journals the
	 * patch — `row-move` carries the heading and the height when they changed. */
	async function commitRows(kind: EditKind, picks: RowPick[]) {
		if (!spawns) return;
		for (const pick of picks) {
			const row = rowOf(spawns, pick);
			if (!row) continue;
			const op =
				kind === 'rotate' && 'heading' in row
					? {
							kind: 'row-move',
							table: pick.table,
							key: pick.id,
							x: row.x,
							y: row.y,
							z: row.z,
							heading: row.heading
						}
					: kind === 'height'
						? { kind: 'row-move', table: pick.table, key: pick.id, x: row.x, y: row.y, z: row.z }
						: { kind: 'row-move', table: pick.table, key: pick.id, x: row.x, y: row.y };
			if (!(await applyEdit(op))) break;
		}
	}

	/** The whole selection goes (Delete, or the panel's button). */
	async function removeSelection() {
		const fixtures = [...(viewer?.selectionView.fixtures ?? [])];
		const rows = [...(viewer?.selectionView.rows ?? [])];
		if (!fixtures.length && !rows.length) return;
		selectFrom({ fixtures: [], rows: [] });
		for (const fixture of fixtures) {
			if (!(await applyEdit({ kind: 'remove', id: fixture.id }))) return;
		}
		for (const pick of rows) {
			if (!(await applyEdit({ kind: 'row-remove', table: pick.table, key: pick.id }))) return;
		}
	}

	/** The whole selection goes (Delete, or the panel's button). */
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
			selectFrom({ fixtures: [], rows: [] });
			await reloadFixtures();
			await reloadSpawns();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			busyEdit = false;
		}
	}

	/** A number the panel typed into: the field is the leading item's own, so
	 * the op kind follows what changed. */
	async function typed(kind: EditKind) {
		if (selected) await commitEdit(kind, [selected]);
		else if (selectedRow) await commitRows(kind, [selectedRow]);
	}

	function number(value: string): number | null {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : null;
	}

	/** The panel's fixture fields: write the field, recompose the instance, then
	 * journal the op — a typed edit is an edit like a gesture. */
	function setFixtureField(field: 'x' | 'y' | 'z' | 'angle' | 'scale', value: string) {
		const parsed = number(value);
		if (!selected || parsed === null) return;
		selected[field] = parsed;
		viewer?.updateFixture(selected, false);
		void typed(field === 'angle' ? 'rotate' : field === 'scale' ? 'scale' : 'move');
	}

	function setRowField(field: 'x' | 'y' | 'z' | 'heading', value: string) {
		const parsed = number(value);
		if (!selectedRow || !selectedSpawn || parsed === null) return;
		if (field === 'heading') selectedSpawn.heading = Math.round(parsed) % 4096;
		else selectedSpawn[field] = parsed;
		viewer?.updateRow(selectedRow, false);
		void typed(field === 'heading' ? 'rotate' : 'move');
	}

	/** The in-game commands the operator has to type: the client's clipboard, so
	 * a reload is a paste away (the bridge will make it a button). */
	async function copyCommands() {
		const region = zone?.info.region ?? '';
		const text = ['/reload doors', `/reload mob`, `/reload object`].join('\n');
		try {
			await navigator.clipboard.writeText(text);
			editStatus = `copied the reload commands (region ${region})`;
		} catch {
			editStatus = text.replace(/\n/g, ' · ');
		}
	}

	/** Drop the selection where the ground was last clicked (the add flow's own
	 * point, kept as the panel's "move here"). */
	function moveHere() {
		if (!addPoint || !viewer) return;
		viewer.moveSelection(addPoint);
	}

	// -- the floating panel: drag it by its header, remember where ---------------

	function panelDown(event: PointerEvent) {
		const target = event.target as HTMLElement;
		if (target.closest('button') || target.closest('input')) return;
		panel.dragging = true;
		const from = { x: event.clientX, y: event.clientY, px: panel.x, py: panel.y };
		const move = (moveEvent: PointerEvent) => {
			panel.x = from.px + (moveEvent.clientX - from.x);
			panel.y = from.py + (moveEvent.clientY - from.y);
		};
		const up = () => {
			panel.dragging = false;
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.localStorage.setItem(
				'camelot.editorPanel',
				JSON.stringify({ x: panel.x, y: panel.y })
			);
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
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
			aria-label="the zone, rendered; click to select, drag a box to select several"
		></canvas>
		{#if busy}
			<div class="busy"><LoaderCircle class="spin" size={20} /> loading…</div>
		{/if}

		{#if edit?.available}
			<section class="panel" class:dirty style="left: {panel.x}px; top: {panel.y}px">
				<header
					role="toolbar"
					tabindex="-1"
					onpointerdown={panelDown}
					title="drag to move the panel"
				>
					<h2>Editor <span class="dim">{edit.mod.id}</span></h2>
					<span class="state" class:on={dirty}>
						{dirty ? `${changes} unpublished` : 'published'}
					</span>
					<button
						type="button"
						onclick={() => (panel.open = !panel.open)}
						title={panel.open ? 'collapse' : 'expand'}
					>
						{panel.open ? '–' : '+'}
					</button>
				</header>
				{#if panel.open}
					<div class="body">
						<div class="row modes">
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
							<label class="snap" title="256 units, 15°, 5%, 16 up">
								<input type="checkbox" bind:checked={snap} /> snap
							</label>
						</div>
						<div class="row">
							<button
								type="button"
								onclick={undo}
								disabled={(!editOps && !editRows) || busyEdit}
								title="drop the journal's last op (server rows first)"
							>
								undo
							</button>
							<button
								type="button"
								onclick={build}
								disabled={busyEdit || !changes}
								title="assemble the authoring directory and run mod build"
							>
								build
							</button>
							<button
								type="button"
								class="publish"
								class:on={dirty}
								onclick={apply}
								disabled={busyEdit || !changes}
								title="write the edits: the client's files and the server's rows"
							>
								publish{dirty ? ` (${changes})` : ''}
							</button>
							<button
								type="button"
								onclick={revert}
								disabled={busyEdit}
								title="empty the journal (the client half, if installed, stays — restore all takes it too)"
							>
								revert
							</button>
							<button
								class="danger"
								type="button"
								onclick={restore}
								disabled={busyEdit || (!changes && !editClient?.installed)}
								title="put everything back: the client half, the server rows, the journal"
							>
								restore all
							</button>
						</div>
						<p class="hint">
							{clientNote} · journal: {editOps} fixture op{editOps === 1 ? '' : 's'},
							{editRows} row patch{editRows === 1 ? '' : 'es'}{writtenRows
								? ` (${writtenRows} written)`
								: ''}
						</p>
						{#if writtenRows || editClient?.installed}
							<p class="hint">
								the running game reads the database at startup: in game <code>/reload doors</code>,
								<code>/reload mob</code>, <code>/reload object</code> (region {zone?.info.region})
								<button type="button" class="link" onclick={copyCommands}>copy</button>
							</p>
						{/if}
						{#if edit.pack}
							<p class="hint">
								pack {(edit.pack.size / 1000).toFixed(0)} kB · {new Date(
									edit.pack.modified * 1000
								).toLocaleTimeString()}
							</p>
						{/if}
						{#if editStatus}<p class="hint">{editStatus}</p>{/if}
						{#if editError}<p class="warn">{editError}</p>{/if}

						{#if selected}
							<section class="context">
								<h3>
									Fixture <span class="dim">#{selected.id}</span>
									{selectionCount > 1 ? `· ${selectionCount} selected` : ''}
								</h3>
								<p class="hint">
									{selected.name || '(unnamed)'} · {catalogOf.get(selected.nif_id)?.filename ??
										`nif ${selected.nif_id}`} · {selected.ground ? 'ground' : 'free'}
								</p>
								<div class="fields">
									<label
										>x <input
											type="number"
											value={selected.x.toFixed(0)}
											onchange={(e) => setFixtureField('x', e.currentTarget.value)}
										/></label
									>
									<label
										>y <input
											type="number"
											value={selected.y.toFixed(0)}
											onchange={(e) => setFixtureField('y', e.currentTarget.value)}
										/></label
									>
									{#if !selected.ground}
										<label
											>z <input
												type="number"
												value={selected.z.toFixed(0)}
												onchange={(e) => setFixtureField('z', e.currentTarget.value)}
											/></label
										>
									{/if}
									<label
										>A <input
											type="number"
											value={selected.angle.toFixed(0)}
											onchange={(e) => setFixtureField('angle', e.currentTarget.value)}
										/></label
									>
									<label
										>scale <input
											type="number"
											value={selected.scale.toFixed(0)}
											onchange={(e) => setFixtureField('scale', e.currentTarget.value)}
										/></label
									>
								</div>
								<div class="row">
									<button type="button" onclick={() => viewer?.focusSelection()}>
										<Crosshair size={13} /> Focus (F)
									</button>
									<button
										type="button"
										onclick={moveHere}
										disabled={!addPoint}
										title="drop the selection on the last ground click (add mode)"
									>
										move here
									</button>
									<button class="danger" type="button" onclick={removeSelection}> remove </button>
								</div>
								{#if proposals.length}
									<label class="snap">
										<input type="checkbox" bind:checked={carry} /> carry {proposals.length} server row{proposals.length >
										1
											? 's'
											: ''}
									</label>
									<p class="hint">inside this object's own meshes: {carriedNote}</p>
									<ul class="addlist">
										{#each proposals as row (row.table + ':' + row.key)}
											<li>
												<button
													type="button"
													onclick={() => selectRow({ table: row.table, id: row.key })}
												>
													<span class="zname">{row.name || '(unnamed)'}</span>
													<span class="zmeta">{row.table}</span>
												</button>
											</li>
										{/each}
									</ul>
								{/if}
							</section>
						{:else if selectedRow && selectedSpawn}
							<section class="context">
								<h3>{selectedRow.table} <span class="dim">{selectedRow.id.slice(0, 8)}</span></h3>
								<p class="hint">{selectedSpawn.name || '(unnamed)'}</p>
								<div class="fields">
									<label
										>x <input
											type="number"
											value={selectedSpawn.x.toFixed(0)}
											onchange={(e) => setRowField('x', e.currentTarget.value)}
										/></label
									>
									<label
										>y <input
											type="number"
											value={selectedSpawn.y.toFixed(0)}
											onchange={(e) => setRowField('y', e.currentTarget.value)}
										/></label
									>
									<label
										>z <input
											type="number"
											value={selectedSpawn.z.toFixed(0)}
											onchange={(e) => setRowField('z', e.currentTarget.value)}
										/></label
									>
									<label
										>heading <input
											type="number"
											value={selectedSpawn.heading}
											onchange={(e) => setRowField('heading', e.currentTarget.value)}
										/></label
									>
								</div>
								<div class="row">
									<button
										type="button"
										onclick={moveHere}
										disabled={!addPoint}
										title="drop the row on the last ground click (add mode)"
									>
										move here
									</button>
									<button class="danger" type="button" onclick={removeSelection}>remove</button>
								</div>
							</section>
						{:else}
							<p class="hint controls">
								left: click to select, drag a box for several — and a drag starting on the selection <b
									>moves</b
								> it (add ctrl to scale, alt to lift). The middle button does the same; right orbits,
								shift+right pans, the wheel zooms. F focuses, Del removes, Escape lets go.
							</p>
						{/if}

						{#if editMode === 'add'}
							<section class="context">
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
							</section>
						{/if}
					</div>
				{/if}
			</section>
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
	.info dl {
		display: grid;
		grid-template-columns: 56px 1fr;
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

	/* The editor's own window, over the scene: drag it by its header. */
	.panel {
		position: absolute;
		width: 350px;
		max-height: calc(100% - 24px);
		display: flex;
		flex-direction: column;
		background: rgba(14, 18, 21, 0.94);
		border: 1px solid #2a3438;
		border-radius: 8px;
		box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
		backdrop-filter: blur(4px);
		z-index: 10;
	}
	.panel.dirty {
		border-color: #6b5a25;
	}
	.panel header {
		display: flex;
		gap: 8px;
		align-items: center;
		padding: 8px 10px;
		border-bottom: 1px solid #222b2e;
		cursor: grab;
	}
	.panel header:active {
		cursor: grabbing;
	}
	.panel h2 {
		margin: 0;
		font-size: 12px;
		font-weight: 600;
		flex: 1;
	}
	.panel .state {
		font: 10px monospace;
		color: #7f8a86;
	}
	.panel .state.on {
		color: #ffd166;
	}
	.panel header button {
		width: auto;
		padding: 1px 7px;
		border-color: #333;
		font-size: 12px;
	}
	.panel .body {
		overflow-y: auto;
		padding: 10px;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.panel .context {
		display: flex;
		flex-direction: column;
		gap: 6px;
		padding-top: 8px;
		border-top: 1px solid #222b2e;
	}
	.panel h3 {
		margin: 0;
		font-size: 11px;
		font-weight: 600;
		color: #cfe8e0;
	}
	.panel .row {
		display: flex;
		flex-wrap: wrap;
		gap: 4px;
		align-items: center;
	}
	.panel .row button {
		width: auto;
		padding: 3px 8px;
		border-color: #333;
		font-size: 11px;
	}
	.panel .modes button.active {
		border-color: #2dd4a7;
		background: #17302a;
	}
	.panel .snap {
		display: flex;
		gap: 4px;
		align-items: center;
		font-size: 11px;
		color: #9aa4a0;
	}
	.panel button.danger {
		border-color: #7a2f2f;
		color: #ff8f8f;
	}
	.panel button.publish.on {
		border-color: #6b5a25;
		color: #ffd166;
		background: #2a2415;
	}
	.panel button.link {
		border: none;
		background: none;
		color: #2dd4a7;
		padding: 0 3px;
		text-decoration: underline;
	}
	.panel input[type='search'] {
		font-size: 11px;
	}
	.panel .addlist {
		max-height: 20dvh;
		overflow-y: auto;
	}
	.panel .controls {
		line-height: 1.7;
	}
	.panel code {
		color: #cfe8e0;
	}
	.fields {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}
	.fields label {
		display: flex;
		gap: 3px;
		align-items: center;
		font: 10px monospace;
		color: #7f8a86;
	}
	.fields input {
		width: 68px;
		padding: 2px 4px;
		border: 1px solid #2a3438;
		border-radius: 4px;
		background: #10161a;
		color: inherit;
		font: 11px monospace;
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
