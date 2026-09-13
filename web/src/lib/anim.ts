// Idle animation playback for DAoC figures.
//
// The serve daemon resolves a figure's idle KFA and returns joint rotation
// tracks keyed by NiNode name (`GET /api/v1/assets/{id}/anim`). Translations
// stay at the figure bind (proportions preserved); only local rotations are
// retargeted, matching the CPU bake in core/camelot/gltf.py.

export interface AnimTrack {
	node: string;
	// A channel can carry rotations, translations, or both: some roots have
	// only a position track (Beetle's bob), so both fields are optional.
	times?: number[];
	quats?: Array<[number, number, number, number]>; // (w, x, y, z)
	ptimes?: number[];
	positions?: Array<[number, number, number]>;
}

export interface AnimClip {
	kfa: string;
	duration: number; // loop length, game seconds
	rate: number; // game seconds -> KFA key seconds multiplier
	tracks: AnimTrack[];
}

export type Quat = [number, number, number, number];

export function slerp(a: Quat, b: Quat, u: number): Quat {
	let [bw, bx, by, bz] = b;
	let dot = a[0] * bw + a[1] * bx + a[2] * by + a[3] * bz;
	if (dot < 0) {
		bw = -bw;
		bx = -bx;
		by = -by;
		bz = -bz;
		dot = -dot;
	}
	let out: Quat;
	if (dot > 0.9995) {
		out = [
			a[0] + u * (bw - a[0]),
			a[1] + u * (bx - a[1]),
			a[2] + u * (by - a[2]),
			a[3] + u * (bz - a[3])
		];
	} else {
		const theta0 = Math.acos(Math.max(-1, Math.min(1, dot)));
		const theta = theta0 * u;
		const sin0 = Math.sin(theta0);
		const s0 = Math.cos(theta) - dot * (Math.sin(theta) / sin0);
		const s1 = Math.sin(theta) / sin0;
		out = [a[0] * s0 + bw * s1, a[1] * s0 + bx * s1, a[2] * s0 + by * s1, a[3] * s0 + bz * s1];
	}
	const n = Math.hypot(out[0], out[1], out[2], out[3]) || 1;
	return [out[0] / n, out[1] / n, out[2] / n, out[3] / n];
}

/** Sample one track at time *t* (clamped). Returns null when empty. */
export function sampleTrack(track: AnimTrack, t: number): Quat | null {
	const times = track.times ?? [];
	const quats = track.quats ?? [];
	if (!times.length || !quats.length) return null;
	if (t <= times[0]) return quats[0];
	if (t >= times[times.length - 1]) return quats[times.length - 1];
	for (let i = 1; i < times.length; i++) {
		if (t <= times[i]) {
			const span = times[i] - times[i - 1];
			const u = span <= 0 ? 0 : (t - times[i - 1]) / span;
			return slerp(quats[i - 1], quats[i], u);
		}
	}
	return quats[quats.length - 1];
}

/** Sample every track of a clip at key time *t*: node name -> quaternion. */
export function sampleClip(clip: AnimClip, t: number): Map<string, Quat> {
	const out = new Map<string, Quat>();
	for (const track of clip.tracks) {
		const q = sampleTrack(track, t);
		if (q) out.set(track.node, q);
	}
	return out;
}

export type Vec3 = [number, number, number];

/** Sample every position track of a clip at key time *t*: node -> position. */
export function samplePositions(clip: AnimClip, t: number): Map<string, Vec3> {
	const out = new Map<string, Vec3>();
	for (const track of clip.tracks) {
		if (!track.ptimes || !track.positions || !track.ptimes.length) continue;
		const { ptimes, positions } = track;
		let p: Vec3;
		if (t <= ptimes[0]) p = positions[0];
		else if (t >= ptimes[ptimes.length - 1]) p = positions[positions.length - 1];
		else {
			let i = 1;
			while (t > ptimes[i]) i++;
			const span = ptimes[i] - ptimes[i - 1];
			const u = span <= 0 ? 0 : (t - ptimes[i - 1]) / span;
			const a = positions[i - 1],
				b = positions[i];
			p = [a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1]), a[2] + u * (b[2] - a[2])];
		}
		out.set(track.node, p);
	}
	return out;
}

/** Map a game-loop time to KFA key seconds (animnifs rate). */
export function gameToKeyTime(clip: AnimClip, tGame: number): number {
	if (!(clip.duration > 0)) return tGame;
	const rate = clip.rate > 0 ? clip.rate : 1;
	return (((tGame % clip.duration) + clip.duration) % clip.duration) * rate;
}
