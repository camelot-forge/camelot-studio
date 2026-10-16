<script lang="ts">
	import { Ban, ChevronDown, Film, LoaderCircle, Play } from '@lucide/svelte';
	import type { AnimClip, AnimStateSummary } from '$lib/api/client';

	interface Row {
		slot: string;
		kfa: string;
		duration: number;
		loop: boolean;
	}

	interface Props {
		/** The figure's own set, from the client's tables (may be empty). */
		states: AnimStateSummary[];
		/** The clip on screen: its `kfa` lights the row, and it is the fallback
		 * row when the tables name no set for this figure at all. */
		clip: AnimClip | null;
		/** Slots the daemon refused, with its own words. */
		refused: Record<string, string>;
		/** The slot the daemon is being asked for right now. */
		loading?: string | null;
		/** The slot the operator last asked for; without one, the row that carries
		 * the clip on screen lights up. */
		activeSlot?: string | null;
		onplay: (slot: string) => void;
	}
	let { states, clip, refused, loading = null, activeSlot = null, onplay }: Props = $props();

	const rows = $derived<Row[]>(
		states.length > 0
			? states.map((s) => ({ slot: s.slot, kfa: s.kfa, duration: s.duration, loop: s.loop }))
			: clip
				? [
						{
							slot: clip.kfa.replace(/\.kfa$/i, ''),
							kfa: clip.kfa,
							duration: clip.duration,
							loop: clip.loop !== false
						}
					]
				: []
	);
	const fromTables = $derived(states.length > 0);
	// One lit row, not every alias of it: the client's set repeats a clip across
	// slots (`Idle`, `Idle1`, `Idle2`, `Idle3` all point at `boar_idle.kfa`), and
	// lighting four rows says nothing. The operator's own pick wins; before one,
	// the *first* slot carrying the clip on screen is the row that answers.
	const activeIndex = $derived.by(() => {
		if (activeSlot) {
			const picked = rows.findIndex((row) => row.slot === activeSlot);
			if (picked >= 0) return picked;
		}
		if (!clip) return -1;
		return rows.findIndex((row) => row.kfa.toLowerCase() === clip.kfa.toLowerCase());
	});

	// A creature's set carries every slot the client's row declares — a boar's
	// 96, of which most are player emotes its rig cannot bind. The list opens on
	// the client's own order (movement, attacks, then the shared tail) and the
	// rest is a click away, rather than a wall of rows.
	const VISIBLE = 8;
	let showAll = $state(false);
	// The window grows to keep the lit row in view: clicking the 34th slot and
	// seeing nothing light up would read as a broken click.
	const shown = $derived(showAll ? rows : rows.slice(0, Math.max(VISIBLE, activeIndex + 1)));
</script>

<section class="card">
	<h3>
		<Film size={14} /> Animation
		<span class="card-meta">{fromTables ? 'the figure’s own set' : 'the idle'}</span>
	</h3>

	{#if rows.length === 0}
		<p class="hint">The client’s tables name no animation for this figure.</p>
	{:else}
		{#if !fromTables}
			<p class="hint">
				The tables name no animation set for this figure (<code>monnifs</code> → <code>anims</code>
				→
				<code>animnifs</code>), so the viewer plays the idle.
			</p>
		{/if}
		<ul class="states">
			{#each shown as row, i (row.slot + row.kfa)}
				{@const refusal = refused[row.slot]}
				<li>
					<button
						type="button"
						class="state"
						class:on={i === activeIndex}
						class:refused={!!refusal}
						disabled={loading === row.slot}
						onclick={() => onplay(row.slot)}
						title={refusal ? refusal : `play ${row.kfa}`}
					>
						{#if loading === row.slot}
							<LoaderCircle size={13} class="spin" />
						{:else if refusal}
							<Ban size={13} />
						{:else}
							<Play size={13} />
						{/if}
						<span class="slot">{row.slot}</span>
						<span class="len nums">
							{row.duration.toFixed(2)} s{#if !row.loop}<span class="once">once</span>{/if}
						</span>
						{#if refusal}<span class="tag">refused</span>{/if}
					</button>
					{#if refusal}<p class="why">{refusal}</p>{/if}
				</li>
			{/each}
		</ul>
		{#if rows.length > VISIBLE && !showAll}
			<button type="button" class="link" onclick={() => (showAll = true)}>
				Show all {rows.length} slots
				<ChevronDown size={14} />
			</button>
		{:else if showAll}
			<button type="button" class="link" onclick={() => (showAll = false)}>
				Show fewer
				<ChevronDown size={14} class="flip" />
			</button>
		{/if}
	{/if}
</section>

<style>
	.states {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.state {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		height: 34px;
		padding: 0 10px;
		border-radius: var(--r-md);
		font-size: var(--fs-dense);
		color: var(--text-2);
		text-align: left;
	}
	.state:hover:not(:disabled) {
		background: var(--surface-input);
		color: var(--text);
	}
	.state.on {
		background: var(--accent-tint-12);
		color: var(--text);
	}
	.state :global(svg) {
		color: var(--text-4);
		flex: 0 0 auto;
	}
	.state.on :global(svg) {
		color: var(--accent);
	}
	.state.refused :global(svg) {
		color: var(--warn-text);
	}
	.slot {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.len {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.once {
		color: var(--text-4);
		text-transform: uppercase;
		font-size: var(--fs-label);
		letter-spacing: 0.04em;
	}
	.tag {
		font-size: var(--fs-label);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--warn-text);
	}
	.why {
		padding: 2px 10px 8px;
		font-size: var(--fs-small);
		line-height: 1.4;
		color: var(--text-4);
	}
	.state.refused .slot {
		text-decoration: line-through;
		text-decoration-color: var(--text-4);
	}
	.hint {
		margin-bottom: 10px;
	}
	.link :global(svg) {
		transition: transform var(--dur) var(--ease);
	}
	.link :global(svg.flip) {
		transform: rotate(180deg);
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
