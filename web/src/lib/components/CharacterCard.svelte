<script lang="ts">
	import { UserRound } from '@lucide/svelte';
	import type { Appearance, AppearanceTables } from '$lib/appearance';

	interface Props {
		race: string;
		gender: string;
		pick: Appearance;
		/** The race's own counts; null until its tables have loaded. */
		tables: AppearanceTables | null;
	}
	let { race, gender, pick, tables }: Props = $props();

	const of = (value: number, max: number | undefined) => (max ? `${value} / ${max}` : `${value}`);
</script>

<section class="card">
	<h3>
		<UserRound size={14} /> Character
		<span class="card-meta">fig3 native</span>
	</h3>
	<dl class="meta">
		<div>
			<dt>Race</dt>
			<dd>{race}</dd>
		</div>
		<div>
			<dt>Gender</dt>
			<dd>{gender}</dd>
		</div>
		<div>
			<dt>Face</dt>
			<dd class="nums">{of(pick.face, tables?.faces)}</dd>
		</div>
		<div>
			<dt>Hair</dt>
			<dd class="nums">
				{of(pick.hairStyle, tables?.hair_styles)} · colour {of(pick.hairColor, tables?.hair_colors)}
			</dd>
		</div>
		<div>
			<dt>Skin</dt>
			<dd class="nums">{of(pick.skinTone, tables?.skin_tones)}</dd>
		</div>
		<div>
			<dt>Eye</dt>
			<dd class="nums">{of(pick.eyeColor, tables?.eye_colors)}</dd>
		</div>
		{#if tables?.cloak_variants}
			<div>
				<dt>Cloak</dt>
				<dd class="nums">{of(pick.cloak, tables.cloak_variants)}</dd>
			</div>
		{/if}
	</dl>
</section>
