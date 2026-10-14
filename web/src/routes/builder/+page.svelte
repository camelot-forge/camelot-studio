<script lang="ts">
	import { LoaderCircle, UserRound } from '@lucide/svelte';
	import AppBar from '$lib/components/AppBar.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import Viewer from '$lib/components/Viewer.svelte';
	import Stepper from '$lib/components/Stepper.svelte';
	import { api } from '$lib/api/client';
	import { animStateSlots, armourMaterialParam, type AnimStateSlot } from '$lib/character';
	import { loadFromGlb as loadGlb, type LoadedModel } from '$lib/gltf/load';
	import type { AnimClip } from '$lib/anim';
	import type { CharacterOptions } from '$lib/api/client';
	import { RACES, type Race } from '$lib/races';

	const OPT_FALLBACK = {
		faces: 7,
		hair_styles: 4,
		hair_colors: 16,
		eye_colors: 8,
		skin_tones: 8
	};

	let female = $state(true);
	let raceId = $state(4);
	let face = $state(1);
	let hairStyle = $state(1);
	let hairColor = $state(1);
	let eyeColor = $state(1);
	let skinTone = $state(1);
	let cloak = $state(0);
	let material = $state('');
	let animState = $state('Idle');
	let states = $state<AnimStateSlot[]>([]);
	let raceOptions = $state<CharacterOptions | null>(null);

	let model = $state<LoadedModel | null>(null);
	let anim = $state<AnimClip | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);

	const raceName = $derived(
		RACES.find((r) => r.male === raceId || r.female === raceId)?.name ?? '—'
	);
	const genderLabel = $derived(female ? 'Female' : 'Male');
	const meshLabel = $derived(
		model ? `${model.meshCount} ${model.meshCount === 1 ? 'mesh' : 'meshes'}` : ''
	);

	async function loadStates() {
		// The states are the figure's own anim set, so a race change resets
		// the pick; a figure that has no Idle falls back to its first state.
		states = animStateSlots(await api.characterStates(raceId, female).catch(() => []));
		if (!states.some((s) => s.slot === animState)) {
			animState = states.find((s) => s.loop)?.slot ?? states[0]?.slot ?? 'Idle';
		}
	}

	async function onRace(entry: Race) {
		raceId = female ? entry.female : entry.male;
		raceOptions = await api.characterOptions(raceId, female).catch(() => null);
		if (raceOptions && face > raceOptions.faces) face = 1;
		if (raceOptions && hairStyle > raceOptions.hair_styles) hairStyle = 1;
		// Materials are not race- or gender-specific: seed the pick once
		// and keep the user's choice across race/gender switches.
		if (!material) material = raceOptions?.material ?? '';
		await loadStates();
		await rebuild();
	}

	async function onGender(next: boolean) {
		female = next;
		const entry = RACES.find((r) => r.male === raceId || r.female === raceId);
		if (entry) raceId = female ? entry.female : entry.male;
		raceOptions = await api.characterOptions(raceId, female).catch(() => null);
		await loadStates();
		await rebuild();
	}

	async function swapTexture(kind: 'hair_color' | 'eye_color' | 'skin_tone') {
		// The server exposes a per-slot texture swap route (sheet + engine
		// tint header); re-apply the single texture to the live scene (the
		// same trick DAoC Tools uses for hair-colour changes).
		if (!model) return;
		const url = api.characterTextureUrl(raceId, female, {
			face: face,
			hair_style: hairStyle,
			hair_color: hairColor,
			eye_color: eyeColor,
			skin_tone: skinTone,
			kind
		});
		const slot = { hair_color: 'Hair', eye_color: 'Eyes', skin_tone: 'Head' }[kind];
		await model.swapSlotTexture?.(slot, url).catch(() => rebuild());
	}

	async function rebuild() {
		loading = true;
		error = null;
		try {
			const url = api.characterModelUrl(raceId, female, {
				face: face,
				hair_style: hairStyle,
				hair_color: hairColor,
				eye_color: eyeColor,
				skin_tone: skinTone,
				cloak: cloak,
				material: armourMaterialParam(material)
			});
			const res = await fetch(url);
			if (!res.ok) throw new Error(`model failed (${res.status})`);
			const buf = await res.arrayBuffer();
			model = await loadGlb(buf, `character-${raceId}-${female ? 'f' : 'm'}.glb`);
			anim = await api.characterAnim(raceId, female, animState).catch(() => null);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			model = null;
			anim = null;
		} finally {
			loading = false;
		}
	}
</script>

<div class="app">
	<AppBar
		section="builder"
		subtitle="character builder · fig3 native"
		contextIcon={UserRound}
		contextLabel={`${raceName} ${genderLabel}`}
		contextMeta={`face ${face}${material ? ` · ${material}` : ''}`}
	/>

	<main class="stage">
		<aside class="rail">
			<section class="panel">
				<h3>Gender</h3>
				<div class="gender">
					<button class:on={!female} aria-pressed={!female} onclick={() => onGender(false)}
						>Male</button
					>
					<button class:on={female} aria-pressed={female} onclick={() => onGender(true)}
						>Female</button
					>
				</div>

				<h3>Race</h3>
				<ul class="races">
					{#each RACES as r (r.name)}
						<li
							class:on={raceId === (female ? r.female : r.male)}
							aria-pressed={raceId === (female ? r.female : r.male)}
							role="button"
							tabindex="0"
							onclick={() => onRace(r)}
							onkeydown={(e) => {
								if (e.key === 'Enter' || e.key === ' ') onRace(r);
							}}
						>
							{r.name}
							<small>{r.realm}</small>
						</li>
					{/each}
				</ul>
			</section>

			<section class="panel">
				<h3>Appearance</h3>
				{#if raceOptions}
					<div class="steppers">
						<div class="row">
							<span class="lbl">Face</span>
							<Stepper
								value={face}
								max={raceOptions.faces}
								label={(v) =>
									`Face ${v}/${(raceOptions as CharacterOptions | null)?.faces ?? OPT_FALLBACK.faces}`}
								onchange={(v: number) => {
									face = v;
									rebuild();
								}}
							/>
						</div>
						<div class="row">
							<span class="lbl">Hair style</span>
							<Stepper
								value={hairStyle}
								max={raceOptions.hair_styles}
								label={(v) =>
									`Style ${v}/${(raceOptions as CharacterOptions | null)?.hair_styles ?? OPT_FALLBACK.hair_styles}`}
								onchange={(v: number) => {
									hairStyle = v;
									rebuild();
								}}
							/>
						</div>
						<div class="row">
							<span class="lbl">Hair color</span>
							<Stepper
								value={hairColor}
								max={raceOptions.hair_colors}
								label={(v) =>
									`Color ${v}/${(raceOptions as CharacterOptions | null)?.hair_colors ?? OPT_FALLBACK.hair_colors}`}
								onchange={(v: number) => {
									hairColor = v;
									swapTexture('hair_color');
								}}
							/>
						</div>
						<div class="row">
							<span class="lbl">Eye color</span>
							<Stepper
								value={eyeColor}
								max={raceOptions.eye_colors}
								label={(v) =>
									`Eye ${v}/${(raceOptions as CharacterOptions | null)?.eye_colors ?? OPT_FALLBACK.eye_colors}`}
								onchange={(v: number) => {
									eyeColor = v;
									swapTexture('eye_color');
								}}
							/>
						</div>
						<div class="row">
							<span class="lbl">Skin tone</span>
							<Stepper
								value={skinTone}
								max={raceOptions.skin_tones}
								label={(v) =>
									`Tone ${v}/${(raceOptions as CharacterOptions | null)?.skin_tones ?? OPT_FALLBACK.skin_tones}`}
								onchange={(v: number) => {
									skinTone = v;
									swapTexture('skin_tone');
								}}
							/>
						</div>
						{#if raceOptions.cloak_variants > 0}
							<div class="row">
								<span class="lbl">Cloak</span>
								<Stepper
									value={cloak}
									max={raceOptions.cloak_variants}
									onchange={(v: number) => {
										cloak = v;
										rebuild();
									}}
								/>
							</div>
						{/if}
						{#if raceOptions.materials?.length}
							<div class="row">
								<span class="lbl">Armour</span>
								<select
									value={material}
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
									onchange={(e) => {
										animState = e.currentTarget.value;
										rebuild();
									}}
								>
									{#each states as s (s.slot)}
										<option value={s.slot}>{s.slot}</option>
									{/each}
								</select>
							</div>
						{/if}
					</div>
				{:else}
					<p class="muted">Pick a race to load its appearance tables.</p>
				{/if}
			</section>
		</aside>

		<div class="content">
			{#if loading}
				<div class="center"><LoaderCircle class="spin" size={22} /> Building…</div>
			{:else if model}
				<Viewer {model} {anim} />
			{:else}
				<div class="center">
					{#if error}<p class="err">{error}</p>{/if}
					<p class="hint">Pick a race/gender to assemble a figure.</p>
				</div>
			{/if}
		</div>
	</main>

	<StatusBar>
		{#snippet left()}
			{#if model}
				<span class="sb-strong sb-nums"
					>{meshLabel} · {model.triangleCount.toLocaleString()} tris</span
				>
			{:else}
				<span>no figure yet</span>
			{/if}
		{/snippet}
		{#snippet center()}
			<span>drag: orbit · wheel: zoom</span>
		{/snippet}
		{#snippet right()}
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
	.rail {
		width: 320px;
		border-right: 1px solid var(--border);
		overflow: auto;
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.panel {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 12px;
	}
	h3 {
		font-size: 11px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--text-3);
		margin: 0 0 8px;
	}
	.gender {
		display: flex;
		gap: 8px;
		margin-bottom: 12px;
	}
	.gender button {
		flex: 1;
		padding: 8px 0;
		border-radius: 8px;
		border: 1px solid var(--border);
		background: transparent;
		color: var(--text);
		cursor: pointer;
	}
	.gender button.on {
		background: var(--accent-tint-12);
		border-color: var(--accent);
	}
	.races {
		list-style: none;
		margin: 0;
		padding: 0;
		max-height: 40vh;
		overflow: auto;
	}
	.races li {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		padding: 6px 8px;
		border-radius: 6px;
		cursor: pointer;
		font-size: 13px;
	}
	.races li:hover {
		background: var(--surface-2);
	}
	.races li.on {
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	.races small {
		color: var(--text-4);
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 13px;
		margin-bottom: 10px;
	}
	.row .lbl {
		width: 84px;
		color: var(--text-3);
	}
	.panel input[type='range'] {
		flex: 1;
	}
	select {
		flex: 1;
		padding: 6px 8px;
		border-radius: 6px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
	}
	.content {
		flex: 1;
		display: flex;
		min-width: 0;
	}
	.center {
		flex: 1;
		display: grid;
		place-items: center;
		flex-direction: column;
		gap: 12px;
		text-align: center;
	}
	.spin {
		animation: spin 1s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	.err {
		color: var(--danger);
	}
	.hint {
		color: var(--text-3);
	}
</style>
