<script lang="ts">
	import { Pause, Play, Repeat, RepeatOff } from '@lucide/svelte';
	import type { AnimClip } from '$lib/anim';
	import { formatClock, tickRatios } from '$lib/transport';

	interface Props {
		clip: AnimClip;
		/** The clock the viewer's own loop advances; the scrub writes it back. */
		time?: number;
		playing?: boolean;
		/** The operator's loop switch. A `clamp` clip is a one-shot whatever
		 * this says — `clipLoops` is the client's own answer, and the switch is
		 * refused (not silently overridden) when it says no. */
		repeat?: boolean;
		clipLoops: boolean;
		speed?: number;
	}
	let {
		clip,
		time = $bindable(0),
		playing = $bindable(true),
		repeat = $bindable(true),
		clipLoops,
		speed = $bindable(1)
	}: Props = $props();

	/** The speeds a keyframe review actually uses: quarter speed to see a
	 * pose land, double to skim a cycle. */
	const SPEEDS = [0.25, 0.5, 1, 2];

	const ticks = $derived(tickRatios(clip));
	const progress = $derived(clip.duration > 0 ? (time / clip.duration) * 100 : 0);
	const speedLabel = $derived(`${speed}×`);

	function cycleSpeed() {
		const at = SPEEDS.indexOf(speed);
		speed = SPEEDS[(at + 1) % SPEEDS.length];
	}
</script>

<div class="transport" role="group" aria-label="Animation transport">
	<button
		type="button"
		class="play"
		class:on={playing}
		onclick={() => (playing = !playing)}
		title={playing ? 'pause (space)' : 'play (space)'}
		aria-label={playing ? 'pause' : 'play'}
	>
		{#if playing}<Pause size={17} />{:else}<Play size={17} />{/if}
	</button>

	<span class="clock nums" title="playhead / clip length">{formatClock(time, clip.duration)}</span>

	<div class="scrub">
		<div class="overlay" aria-hidden="true">
			{#each ticks as ratio, i (i)}
				<i class="tick" style="left: calc(7px + (100% - 14px) * {ratio});"></i>
			{/each}
			<span class="knob" style="left: calc(7px + (100% - 14px) * {progress / 100});"></span>
		</div>
		<input
			type="range"
			min="0"
			max={clip.duration || 1}
			step="0.005"
			bind:value={time}
			style="--p: {progress}%"
			aria-label="playhead"
			title={`the clip’s ${ticks.length} key times are ticked; ${clip.kfa}`}
		/>
	</div>

	<button
		type="button"
		class="chip"
		class:on={clipLoops && repeat}
		disabled={!clipLoops}
		onclick={() => (repeat = !repeat)}
		title={clipLoops
			? 'loop — off stops at the last frame'
			: 'the client plays this clip once (animnifs type `clamp`): the viewer cannot wrap it'}
	>
		{#if clipLoops && repeat}<Repeat size={16} />{:else}<RepeatOff size={16} />{/if}
	</button>

	<button
		type="button"
		class="chip speed nums"
		onclick={cycleSpeed}
		title="playback speed — the clip's own timing is unchanged"
	>
		{speedLabel}
	</button>
</div>

<style>
	.transport {
		display: flex;
		align-items: center;
		gap: 12px;
		height: 56px;
		padding: 0 14px;
		border: 1px solid var(--border);
		border-radius: var(--r-dock);
		background: var(--surface);
		box-shadow: 0 14px 34px rgb(0 0 0 / 50%);
	}
	.play {
		display: grid;
		place-items: center;
		width: 36px;
		height: 36px;
		border-radius: var(--r-lg);
		background: var(--accent-tint-14);
		color: var(--accent);
		transition:
			background-color var(--dur) var(--ease),
			color var(--dur) var(--ease);
	}
	.play:hover {
		background: var(--accent-tint-12);
	}
	.play.on {
		background: var(--accent);
		color: var(--accent-ink);
	}
	.clock {
		font-size: var(--fs-dense);
		color: var(--text-2);
		min-width: 112px;
	}
	.scrub {
		position: relative;
		flex: 1;
		min-width: 160px;
		height: 24px;
	}
	.overlay {
		position: absolute;
		inset: 0;
		pointer-events: none;
	}
	/* The clip's own key times: what the scrubber is for. */
	.tick {
		position: absolute;
		top: 7px;
		width: 2px;
		height: 10px;
		border-radius: 1px;
		background: var(--border-hover);
	}
	.knob {
		position: absolute;
		top: 5px;
		width: 14px;
		height: 14px;
		margin-left: -7px;
		border-radius: 50%;
		background: var(--text);
		box-shadow: 0 1px 3px rgb(0 0 0 / 60%);
	}
	input[type='range'] {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 24px;
		margin: 0;
		background: transparent;
		appearance: none;
		-webkit-appearance: none;
		cursor: pointer;
	}
	input[type='range']::-webkit-slider-runnable-track {
		height: 6px;
		margin-top: 9px;
		border-radius: 3px;
		background: linear-gradient(
			to right,
			var(--accent) 0 var(--p, 0%),
			var(--surface-input) var(--p, 0%) 100%
		);
	}
	input[type='range']::-webkit-slider-thumb {
		-webkit-appearance: none;
		width: 14px;
		height: 14px;
		margin-top: 5px;
		border: none;
		border-radius: 50%;
		background: transparent;
	}
	input[type='range']::-moz-range-track {
		height: 6px;
		border-radius: 3px;
		background: linear-gradient(
			to right,
			var(--accent) 0 var(--p, 0%),
			var(--surface-input) var(--p, 0%) 100%
		);
	}
	input[type='range']::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border: none;
		border-radius: 50%;
		background: transparent;
	}
	.chip {
		display: grid;
		place-items: center;
		width: 34px;
		height: 34px;
		border-radius: var(--r-lg);
		color: var(--text-3);
		transition:
			background-color var(--dur) var(--ease),
			color var(--dur) var(--ease);
	}
	.chip:hover:not(:disabled) {
		background: var(--surface-input);
		color: var(--text);
	}
	.chip.on {
		background: var(--accent-tint-14);
		color: var(--accent);
	}
	.chip:disabled {
		color: var(--text-4);
		cursor: not-allowed;
	}
	.chip.speed {
		width: auto;
		min-width: 40px;
		font-size: var(--fs-dense);
		color: var(--text-2);
	}
</style>
