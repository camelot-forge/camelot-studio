<script lang="ts">
	import { LoaderCircle, UserPlus } from '@lucide/svelte';
	import Viewer from '$lib/components/Viewer.svelte';
	import Stepper from '$lib/components/Stepper.svelte';
	import { api } from '$lib/api/client';
	import { loadFromGlb as loadGlb, type LoadedModel } from '$lib/gltf/load';
	import type { CharacterOptions } from '$lib/api/client';

	// The 18 playable races, ids from the client's own enumeration (eRace
	// 1..21, the 3 Minotaurs fold into 19..21 and reuse the Half-Ogre key).
	// Each playable race maps to TWO fig3 rows: (male, female), alternating
	// numbered by eRace — male = 2k-1 (Briton=1/2 …). The ids below are the
	// eRace codes from the emulators' eRace enum which is also the row id of
	// `fig3map.csv`/`fig3descriptions.csv`.
	const RACES: Array<{ male: number; female: number; name: string; realm: string }> = [
		{ male: 1, female: 2, name: 'Briton', realm: 'Albion' },
		{ male: 3, female: 4, name: 'Saracen', realm: 'Albion' },
		{ male: 5, female: 6, name: 'Frostalf', realm: 'Midgard' },
		{ male: 7, female: 8, name: 'Shar', realm: 'Hibernia' },
		{ male: 9, female: 10, name: 'Avalonian', realm: 'Albion' },
		{ male: 11, female: 12, name: 'Elf', realm: 'Hibernia' },
		{ male: 13, female: 14, name: 'Highlander', realm: 'Albion' },
		{ male: 15, female: 16, name: 'Norseman', realm: 'Midgard' },
		{ male: 17, female: 18, name: 'Lurikeen', realm: 'Hibernia' },
		{ male: 19, female: 20, name: 'Kobold', realm: 'Midgard' },
		{ male: 21, female: 22, name: 'Dwarf', realm: 'Midgard' },
		{ male: 23, female: 24, name: 'Inconnu', realm: 'Albion' },
		{ male: 25, female: 26, name: 'Half Ogre', realm: 'Albion' },
		{ male: 27, female: 28, name: 'Valkyn', realm: 'Midgard' },
		{ male: 29, female: 30, name: 'Firbolg', realm: 'Hibernia' }
	];

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
	let raceOptions = $state<CharacterOptions | null>(null);

	let model = $state<LoadedModel | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(false);

	async function onRace(entry: { male: number; female: number; name: string; realm: string }) {
		raceId = female ? entry.female : entry.male;
		raceOptions = await api.characterOptions(raceId, female).catch(() => null);
		if (raceOptions && face > raceOptions.faces) face = 1;
		if (raceOptions && hairStyle > raceOptions.hair_styles) hairStyle = 1;
		await rebuild();
	}

	async function onGender(next: boolean) {
		female = next;
		const entry = RACES.find((r) => r.male === raceId || r.female === raceId);
		if (entry) raceId = female ? entry.female : entry.male;
		raceOptions = await api.characterOptions(raceId, female).catch(() => null);
		await rebuild();
	}

	async function swapTexture(kind: 'hair_style' | 'hair_color' | 'eye_color' | 'skin_tone') {
		// The server exposes a per-slot texture swap route; re-apply the
		// single texture to the live scene (same trick DAoC Tools uses for
		// hair-colour changes).
		if (!model) return;
		const url = api.characterTextureUrl(raceId, female, {
			face: face,
			hair_style: hairStyle,
			hair_color: hairColor,
			eye_color: eyeColor,
			skin_tone: skinTone,
			kind
		});
		const slot = kind === 'hair_color' ? 'Hair' : 'Eyes';
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
				cloak: cloak
			});
			const res = await fetch(url);
			if (!res.ok) throw new Error(`model failed (${res.status})`);
			const buf = await res.arrayBuffer();
			model = await loadGlb(buf, `character-${raceId}-${female ? 'f' : 'm'}.glb`);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			model = null;
		} finally {
			loading = false;
		}
	}
</script>

<div class="app">
	<header class="header">
		<div class="brand">
			<span class="logo"><UserPlus size={18} /></span>
			<div class="titles">
				<span class="name">Character Builder</span>
				<span class="sub">pixie-accurate, fig3 native</span>
			</div>
		</div>
	</header>

	<main class="stage">
		<aside class="rail">
			<section class="panel">
				<h3>Gender</h3>
				<div class="gender">
					<button class:on={() => !female} onclick={() => onGender(false)}>Male</button>
					<button class:on={() => female} onclick={() => onGender(true)}>Female</button>
				</div>

				<h3>Race</h3>
				<ul class="races">
					{#each RACES as r (r.name)}
						<li
							class:on={() => raceId === (female ? r.female : r.male)}
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
									rebuild();
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
				<Viewer {model} />
			{:else}
				<div class="center">
					{#if error}<p class="err">{error}</p>{/if}
					<p class="hint">Pick a race/gender to assemble a figure.</p>
				</div>
			{/if}
		</div>
	</main>
</div>

<style>
	.app {
		height: 100vh;
		display: flex;
		flex-direction: column;
	}
	.header {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 14px 20px;
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}
	.logo {
		width: 32px;
		height: 32px;
		display: grid;
		place-items: center;
		border-radius: 8px;
		background: var(--surface-2);
		color: var(--accent);
	}
	.brand {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.name {
		font-weight: 600;
		display: block;
	}
	.sub {
		color: var(--muted);
		font-size: 12px;
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
		color: var(--muted);
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
		background: var(--accent-soft);
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
		background: var(--accent-soft);
		color: var(--accent);
	}
	.races small {
		color: var(--muted);
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
		color: var(--muted);
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
		color: var(--muted);
	}
</style>
