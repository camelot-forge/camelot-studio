/** Playback helpers for the animation transport (the drafts' floating bar) —
 * pure, so the rules can be tested without a canvas. */
import type { AnimClip } from './anim';

/** The clip's own key times, as ratios of its duration: what the scrubber draws
 * its ticks from. Rotation and position tracks both count, deduplicated (they
 * share most of their grid), and the two ends are left out (they are the ends). */
export function tickRatios(clip: AnimClip): number[] {
	if (!(clip.duration > 0)) return [];
	const rate = clip.rate > 0 ? clip.rate : 1;
	const keys = new Set<number>();
	for (const track of clip.tracks) {
		for (const time of track.times ?? []) keys.add(time);
		for (const time of track.ptimes ?? []) keys.add(time);
	}
	const ratios: number[] = [];
	for (const key of keys) {
		// key seconds → game seconds → ratio of the loop
		const ratio = key / rate / clip.duration;
		if (ratio > 0.001 && ratio < 0.999) ratios.push(ratio);
	}
	return ratios.sort((a, b) => a - b);
}

/** The number of keys a clip carries, rotation and position together — the
 * card's own count (not a sum of tracks: one key time can move two channels). */
export function keyCount(clip: AnimClip): number {
	const keys = new Set<number>();
	for (const track of clip.tracks) {
		for (const time of track.times ?? []) keys.add(time);
		for (const time of track.ptimes ?? []) keys.add(time);
	}
	return keys.size;
}

/** The clock is a float accumulator; trimming its noise keeps a time comparable
 * to a key's own time (and to zero) after a wrap. */
function clean(time: number): number {
	return Math.round(time * 1e6) / 1e6;
}

/** Advance a playback clock. A looping clip wraps; a one-shot (the client's own
 * `clamp` clips) holds its last frame and says it ended. */
export function advance(
	time: number,
	dt: number,
	duration: number,
	loop: boolean
): { time: number; ended: boolean } {
	if (!(duration > 0)) return { time: 0, ended: false };
	const next = clean(time + dt);
	// A clock already inside the clip is left alone: pausing must not drift.
	if (next >= 0 && next < duration) return { time: next, ended: false };
	if (loop) return { time: clean(((next % duration) + duration) % duration), ended: false };
	if (next >= duration) return { time: duration, ended: true };
	return { time: 0, ended: false };
}

/** A time the transport writes: always two decimals, nonsense reads zero. */
function seconds(time: number): string {
	return (Number.isFinite(time) ? time : 0).toFixed(2);
}

/** One end of the read-out: "1.24 s". */
export function formatTime(time: number): string {
	return `${seconds(time)} s`;
}

/** The transport's own read-out: "1.24 / 2.40 s" — one unit, both ends. */
export function formatClock(time: number, duration: number): string {
	return `${seconds(time)} / ${seconds(duration)} s`;
}
