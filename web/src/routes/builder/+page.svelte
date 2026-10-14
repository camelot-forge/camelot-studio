<script lang="ts">
	import {
		Camera,
		Download,
		LoaderCircle,
		RotateCcw,
		Shuffle,
		SlidersHorizontal,
		UserRound
	} from '@lucide/svelte';
	import { onMount } from 'svelte';
	import AppBar from '$lib/components/AppBar.svelte';
	import CharacterCard from '$lib/components/CharacterCard.svelte';
	import Dock from '$lib/components/Dock.svelte';
	import EquipmentCard from '$lib/components/EquipmentCard.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import Stepper from '$lib/components/Stepper.svelte';
	import Viewer, { type ViewerHandles } from '$lib/components/Viewer.svelte';
	import { api, type CharacterOptions } from '$lib/api/client';
	import { animStateSlots, armourMaterialParam, type AnimStateSlot } from '$lib/character';
	import { loadFromGlb as loadGlb, type LoadedModel } from '$lib/gltf/load';
	import { exportFigureGlb } from '$lib/gltf/export';
	import { rigBones } from '$lib/inspect';
	import {
		appearanceRows,
		clampAppearance,
		combinationCount,
		randomAppearance,
		type Appearance,
		type AppearanceTables
	} from '$lib/appearance';
	import type { AnimClip } from '$lib/anim';
	import { RACES, type Race } from '$lib/races';

	let female = $state(true);
	let raceId = $state(4);
	// The picks live in one object: Randomize, Reset and a race switch all move
	// them together, and the pure helpers in lib/appearance take exactly this.
	let pick = $state<Appearance>({
		face: 1,
		hairStyle: 1,
		hairColor: 1,
		eyeColor: 1,
		skinTone: 1,
		cloak: 0
	});
	let material = $state('');
	let animState = $state('Idle');
	let states = $state<AnimStateSlot[]>([]);
	let raceOptions = $state<CharacterOptions | null>(null);

	let model = $state<LoadedModel | null>(null);
	let anim = $state<AnimClip | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let fps = $state(0);
	let exporting = $state(false);
	let viewer = $state<ViewerHandles | null>(null);

	const raceName = $derived(
		RACES.find((r) => r.male === raceId || r.female === raceId)?.name ?? '—'
	);
	const genderLabel = $derived(female ? 'Female' : 'Male');
	const meshLabel = $derived(
		model ? `${model.meshCount} ${model.meshCount === 1 ? 'mesh' : 'meshes'}` : ''
	);
	const tables = $derived<AppearanceTables | null>(raceOptions);
	const rows = $derived(tables ? appearanceRows(tables) : []);
	const combinations = $derived(tables ? combinationCount(tables) : 0);

	async function loadStates() {
		// The states are the figure's own anim set, so a race change resets
		// the pick; a figure that has no Idle falls back to its first state.
		states = animStateSlots(await api.characterStates(raceId, female).catch(() => []));
		if (!states.some((s) => s.slot === animState)) {
			animState = states.find((s) => s.loop)?.slot ?? states[0]?.slot ?? 'Idle';
		}
	}

	/** A race's own tables, and the picks pulled back into their ranges. */
	async function loadRace() {
		raceOptions = await api.characterOptions(raceId, female).catch(() => null);
		if (raceOptions) {
			pick = clampAppearance(raceOptions, pick);
			// Materials are not race- or gender-specific: seed the pick once
			// and keep the user's choice across race/gender switches.
			if (!material) material = raceOptions.material ?? '';
		}
		await loadStates();
	}

	async function onRace(entry: Race) {
		raceId = female ? entry.female : entry.male;
		await loadRace();
		await rebuild();
	}

	async function onGender(next: boolean) {
		female = next;
		const entry = RACES.find((r) => r.male === raceId || r.female === raceId);
		if (entry) raceId = female ? entry.female : entry.male;
		await loadRace();
		await rebuild();
	}

	function onPick(key: keyof Appearance, value: number) {
		pick[key] = value;
		// The sheet-level picks are swapped in place (the trick DAoC Tools uses):
		// only the affected texture re-forms, never the whole figure.
		if (key === 'hairColor') return void swapTexture('hair_color');
		if (key === 'eyeColor') return void swapTexture('eye_color');
		if (key === 'skinTone') return void swapTexture('skin_tone');
		void rebuild();
	}

	async function swapTexture(kind: 'hair_color' | 'eye_color' | 'skin_tone') {
		if (!model) return;
		const url = api.characterTextureUrl(raceId, female, {
			face: pick.face,
			hair_style: pick.hairStyle,
			hair_color: pick.hairColor,
			eye_color: pick.eyeColor,
			skin_tone: pick.skinTone,
			kind
		});
		const slot = { hair_color: 'Hair', eye_color: 'Eyes', skin_tone: 'Head' }[kind];
		await model.swapSlotTexture?.(slot, url).catch(() => rebuild());
	}

	/** A random figure, of the ones these tables describe. */
	async function randomize() {
		if (!tables) return;
		pick = randomAppearance(tables);
		await rebuild();
	}

	async function reset() {
		pick = { face: 1, hairStyle: 1, hairColor: 1, eyeColor: 1, skinTone: 1, cloak: 0 };
		await rebuild();
	}

	async function rebuild() {
		loading = true;
		error = null;
		try {
			const url = api.characterModelUrl(raceId, female, {
				face: pick.face,
				hair_style: pick.hairStyle,
				hair_color: pick.hairColor,
				eye_color: pick.eyeColor,
				skin_tone: pick.skinTone,
				cloak: pick.cloak,
				material: armourMaterialParam(material)
			});
			const res = await fetch(url);
			if (!res.ok) throw new Error(`model failed (${res.status})`);
			const buf = await res.arrayBuffer();
			model = await loadGlb(buf, `${raceName.toLowerCase()}-${female ? 'f' : 'm'}.glb`);
			// A state the daemon refuses leaves the figure in its bind pose; the
			// rail says so rather than playing something else.
			anim = await api.characterAnim(raceId, female, animState).catch(() => null);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			model = null;
			anim = null;
		} finally {
			loading = false;
		}
	}

	async function exportGlb() {
		if (!model) return;
		exporting = true;
		error = null;
		try {
			await exportFigureGlb(model.scene, model.name);
		} catch (e) {
			error = `export failed: ${e instanceof Error ? e.message : String(e)}`;
		} finally {
			exporting = false;
		}
	}

	onMount(() => {
		void boot();
	});

	async function boot() {
		await loadRace();
		await rebuild();
	}

	// The viewer's outputs are named functions, not inline closures: a fresh
	// closure each render would re-fire the viewer's own effects.
	function onViewerFps(value: number) {
		fps = value;
	}

	function onViewerReady(handles: ViewerHandles) {
		viewer = handles;
	}
</script>

<div class="app">
	<AppBar
		section="builder"
		subtitle="character builder · fig3 native"
		contextIcon={UserRound}
		contextLabel={`${raceName} ${genderLabel}`}
		contextMeta={`face ${pick.face}${material ? ` · ${material}` : ''}`}
	>
		{#snippet actions()}
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
			<button
				class="btn btn--primary"
				onclick={exportGlb}
				disabled={!model || exporting}
				title="export as glTF — the assembled figure; the client’s DDS sheets are not re-encoded"
			>
				<Download size={15} /> Export glTF
			</button>
		{/snippet}
	</AppBar>

	<main class="stage">
		<Dock title="Character" sub={`${raceName} ${genderLabel}`} icon={SlidersHorizontal}>
			<section class="section">
				<h4>Gender</h4>
				<div class="segmented" role="group" aria-label="Gender">
					<button
						type="button"
						class:on={!female}
						aria-pressed={!female}
						onclick={() => onGender(false)}
					>
						Male
					</button>
					<button
						type="button"
						class:on={female}
						aria-pressed={female}
						onclick={() => onGender(true)}
					>
						Female
					</button>
				</div>
			</section>

			<section class="section">
				<h4>Race <span class="card-meta">{RACES.length} races</span></h4>
				<ul class="choice-list races">
					{#each RACES as race (race.name)}
						<li>
							<button
								type="button"
								class:on={raceId === (female ? race.female : race.male)}
								aria-pressed={raceId === (female ? race.female : race.male)}
								onclick={() => onRace(race)}
							>
								{race.name}
								<span class="meta">{race.realm}</span>
							</button>
						</li>
					{/each}
				</ul>
			</section>

			<section class="section">
				<h4>Appearance</h4>
				{#if tables}
					<div class="rows">
						{#each rows as row (row.key)}
							<div class="row">
								<span class="lbl">{row.label}</span>
								<Stepper
									name={row.label}
									value={pick[row.key]}
									min={row.min}
									max={row.max}
									onchange={(value) => onPick(row.key, value)}
								/>
							</div>
						{/each}
					</div>
				{:else}
					<p class="hint">Pick a race to load its appearance tables.</p>
				{/if}
			</section>

			<section class="section">
				<h4>Gear &amp; state</h4>
				{#if raceOptions?.materials?.length}
					<div class="row">
						<span class="lbl">Armour</span>
						<select
							value={material}
							aria-label="Armour material"
							onchange={(e) => {
								material = e.currentTarget.value;
								rebuild();
							}}
						>
							{#each raceOptions.materials as name (name)}
								<option value={name}>{name}</option>
							{/each}
						</select>
					</div>
				{/if}
				{#if states.length}
					<div class="row">
						<span class="lbl">State</span>
						<select
							value={animState}
							aria-label="Animation state"
							onchange={(e) => {
								animState = e.currentTarget.value;
								rebuild();
							}}
						>
							{#each states as state (state.slot)}
								<option value={state.slot}>{state.slot}</option>
							{/each}
						</select>
					</div>
					{#if model && !anim}
						<p class="hint">
							The client refuses <strong>{animState}</strong> for this figure — no channel of the clip
							binds to its rig, so it stays in its bind pose. The same rule the viewer applies.
						</p>
					{/if}
				{/if}
			</section>

			{#snippet foot()}
				<div class="foot">
					<div class="foot-row">
						<button class="btn btn--secondary grow" onclick={randomize} disabled={!tables}>
							<Shuffle size={15} /> Randomize
						</button>
						<button class="btn btn--secondary" onclick={reset} disabled={!tables}>
							<RotateCcw size={15} /> Reset
						</button>
					</div>
					<p class="hint">
						{combinations.toLocaleString()} combinations — every pick re-assembles the figure.
					</p>
				</div>
			{/snippet}
		</Dock>

		<div class="content">
			{#if loading}
				<div class="center"><LoaderCircle class="spin" size={22} /> Building…</div>
			{:else if model}
				<Viewer {model} {anim} onfps={onViewerFps} onready={onViewerReady} />
			{:else}
				<div class="center">
					{#if error}<p class="err">{error}</p>{/if}
					<p class="hint">Pick a race and a gender to assemble a figure.</p>
				</div>
			{/if}
		</div>

		<Dock title="Inspect" sub={`${raceName} · ${genderLabel}`} icon={UserRound} side="right">
			<CharacterCard race={raceName} gender={genderLabel} {pick} {tables} />
			{#if model}
				<EquipmentCard meshNames={model.meshNames} {material} />
			{/if}
		</Dock>
	</main>

	<StatusBar>
		{#snippet left()}
			{#if model}
				<span class="sb-strong sb-nums" title="frames the renderer drew, twice a second"
					>{fps} fps</span
				>
				<span class="sb-nums"
					>{model.triangleCount.toLocaleString()} tris · {meshLabel} · {rigBones(
						model.scene,
						model.skinJson
					)} bones · face {pick.face}/{tables?.faces ?? '—'}</span
				>
			{:else}
				<span>no figure yet</span>
			{/if}
		{/snippet}
		{#snippet center()}
			<span>drag: orbit · wheel: zoom · right-drag: pan · F: frame · space: play</span>
		{/snippet}
		{#snippet right()}
			{#if anim}
				<span class="sb-strong sb-nums" title="the clip on screen"
					>{animState} · {anim.duration.toFixed(2)} s</span
				>
			{/if}
			<span>viewer {__BUILD_STAMP__}</span>
		{/snippet}
	</StatusBar>
</div>

<style>
	.app {
		height: 100dvh;
		display: flex;
		flex-direction: column;
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
	.races {
		max-height: 132px;
		overflow-y: auto;
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.row .lbl {
		font-size: var(--fs-body);
		color: var(--text-2);
	}
	select {
		flex: 1;
		min-width: 0;
		height: var(--h-control-md);
		padding: 0 8px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-input);
		background: var(--surface-input);
		color: var(--text);
		font-size: var(--fs-dense);
	}
	.hint {
		font-size: var(--fs-small);
		line-height: 1.45;
		color: var(--text-4);
	}
	.foot {
		display: flex;
		flex-direction: column;
		gap: 8px;
		width: 100%;
	}
	.foot-row {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	:global(.btn.grow) {
		flex: 1;
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
