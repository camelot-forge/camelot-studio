<script lang="ts">
	import { Bone, ChevronDown, Eye, EyeOff, Search } from '@lucide/svelte';
	import { canonicalJoints, filterJoints, type Joint } from '$lib/inspect';
	import { MAX_VERIFIED } from '$lib/skeleton';

	interface Props {
		/** The figure's own joints (the `.skin.json` sidecar's list). */
		joints: Joint[];
		/** The overlay is on: the viewport draws the skeleton. */
		bones?: boolean;
	}
	let { joints, bones = $bindable(false) }: Props = $props();

	let query = $state('');
	let showTable = $state(false);

	const shown = $derived(filterJoints(joints, query));
	// The figure's joints come first; the canonical table answers for the
	// channels a rig does not name (and stands alone when there is no sidecar).
	const table = $derived(filterJoints(canonicalJoints(), query));
	const tableOpen = $derived(showTable || joints.length === 0);
</script>

<section class="card">
	<h3>
		<Bone size={14} /> Bones
		<span class="card-meta">{joints.length > 0 ? joints.length : 'no .skin.json'}</span>
		<button
			type="button"
			class="eye"
			class:on={bones}
			aria-pressed={bones}
			onclick={() => (bones = !bones)}
			title={bones ? 'hide the skeleton overlay' : 'overlay the skeleton on the figure'}
		>
			{#if bones}<Eye size={14} />{:else}<EyeOff size={14} />{/if}
		</button>
	</h3>

	<div class="search">
		<Search size={13} />
		<input
			class="input"
			type="search"
			placeholder="bone name or index"
			bind:value={query}
			aria-label="filter the bones"
		/>
	</div>

	{#if joints.length === 0}
		<p class="hint">
			No <code>.skin.json</code> file for this figure, so its own joint names are unknown. The table
			below is the client’s <code>animnode.dat</code>: the joint each KFA channel names (175
			channels; 0–{MAX_VERIFIED} checked against the client).
		</p>
	{/if}

	{#if shown.length > 0}
		<ul class="bones">
			{#each shown as joint (joint.name)}
				<li class="bone">
					<span class="idx nums">{joint.index ?? '–'}</span>
					<span class="nm">{joint.name}</span>
				</li>
			{/each}
		</ul>
	{:else if joints.length > 0 && query}
		<p class="hint">No joint of this figure matches “{query}”.</p>
	{/if}

	{#if joints.length > 0}
		<button
			type="button"
			class="link"
			onclick={() => (showTable = !showTable)}
			title={`the client’s own table (figures/animnode.dat) carries 175 channels; 0–${MAX_VERIFIED} are the ones checked against the client`}
		>
			{showTable ? 'Hide' : 'Show'} the channel table (0–{MAX_VERIFIED})
			<ChevronDown size={14} class={showTable ? 'flip' : ''} />
		</button>
	{/if}

	{#if tableOpen}
		{#if query}
			<p class="hint">
				{filterJoints(canonicalJoints(), query).length} of the table’s {MAX_VERIFIED + 1} channels match
				“{query}”.
			</p>
		{/if}
		<ul class="bones table">
			{#each table as joint (joint.name)}
				<li class="bone">
					<span class="idx nums">{joint.index}</span>
					<span class="nm">{joint.name}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.eye {
		display: grid;
		place-items: center;
		width: 26px;
		height: 26px;
		margin-left: 8px;
		border-radius: var(--r-sm);
		color: var(--text-4);
	}
	.eye:hover {
		background: var(--surface-input);
		color: var(--text);
	}
	.eye.on {
		background: var(--accent-tint-14);
		color: var(--accent);
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
		margin-bottom: 10px;
	}
	.search :global(svg) {
		position: absolute;
		left: 9px;
		color: var(--text-4);
	}
	.search .input {
		padding: 6px 10px 6px 28px;
		font-size: var(--fs-dense);
	}
	.bones {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 1px;
		max-height: 300px;
		overflow-y: auto;
	}
	.bones.table {
		margin-top: 8px;
		max-height: 260px;
	}
	.bone {
		display: flex;
		gap: 10px;
		align-items: baseline;
		padding: 3px 6px;
		border-radius: var(--r-sm);
	}
	.bone:hover {
		background: var(--surface-input);
	}
	.idx {
		width: 26px;
		flex: 0 0 auto;
		font-size: var(--fs-small);
		color: var(--accent);
	}
	.nm {
		font-size: var(--fs-dense);
		color: var(--text-2);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.link :global(svg) {
		transition: transform var(--dur) var(--ease);
	}
	.link :global(svg.flip) {
		transform: rotate(180deg);
	}
	.hint {
		margin-bottom: 8px;
	}
</style>
