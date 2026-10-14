<script module lang="ts">
	/** What the top bar can ask the viewport to do. */
	export interface ViewerHandles {
		snapshot: () => void;
	}
</script>

<script lang="ts">
	import * as THREE from 'three';
	import { untrack } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import {
		Axis3d,
		Box,
		Camera,
		Grid3x3,
		GripHorizontal,
		Maximize2,
		Rotate3d,
		RotateCcw,
		RotateCw
	} from '@lucide/svelte';
	import Transport from './Transport.svelte';
	import {
		rebindSkinnedMeshes,
		bindBoxOf,
		textureFromDds,
		textureSide,
		tintColor,
		type LoadedModel
	} from '$lib/gltf/load';
	import { sampleClip, samplePositions, gameToKeyTime, type AnimClip } from '$lib/anim';
	import { advance } from '$lib/transport';

	interface Props {
		model: LoadedModel;
		anim?: AnimClip | null;
		/** Frames per second, twice a second: what the status bar reads. An output
		 * is a callback here, the way the library's own `onselect` is. */
		onfps?: (fps: number) => void;
		/** The bones overlay, driven by the inspector's eye. */
		bones?: boolean;
		onready?: (handles: ViewerHandles) => void;
	}
	let { model, anim = null, bones = $bindable(false), onfps, onready }: Props = $props();

	type Axis = 'x' | 'y' | 'z';

	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let viewerEl = $state<HTMLElement | null>(null);
	let announced = false;

	let wireframe = $state(false);
	let showAxes = $state(true);
	let showGrid = $state(true);
	let turntable = $state(false);

	// The transport's own state. `repeat` is the operator's switch; `clipLoops`
	// is the client's — a `clamp` clip is a one-shot whatever the switch says.
	let playing = $state(true);
	let time = $state(0);
	let repeat = $state(true);
	let speed = $state(1);
	const clipLoops = $derived(anim ? anim.loop !== false : true);

	let axis = $state<Axis>('y');
	let rot = $state({ x: 0, y: 0, z: 0 });

	function rotate(axisName: Axis, dir: 1 | -1) {
		rot[axisName] += (dir * Math.PI) / 2;
	}

	// --- the floating strip (the drafts' vertical toolbar, dragged by its grip) --

	const STRIP_KEY = 'camelot.viewer.strip';
	const STRIP_W = 52;

	function readStrip(): { x: number; y: number } {
		// A stored spot is a hint, never a trap: it is clamped to the viewport
		// when applied, so a window that shrank cannot hide the tools.
		if (typeof localStorage === 'undefined') return { x: 12, y: 12 };
		try {
			const raw = localStorage.getItem(STRIP_KEY);
			if (!raw) return { x: 12, y: 12 };
			const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown };
			if (typeof parsed.x !== 'number' || typeof parsed.y !== 'number') return { x: 12, y: 12 };
			return { x: parsed.x, y: parsed.y };
		} catch {
			return { x: 12, y: 12 };
		}
	}

	let strip = $state(readStrip());
	let grab: { dx: number; dy: number; x: number; y: number } | null = null;

	const clampStrip = (p: { x: number; y: number }) => ({
		x: Math.max(0, Math.min(p.x, (viewerEl?.clientWidth ?? 0) - STRIP_W)),
		y: Math.max(0, Math.min(p.y, (viewerEl?.clientHeight ?? 0) - 40))
	});

	function stripDown(event: PointerEvent) {
		grab = {
			dx: event.clientX - strip.x,
			dy: event.clientY - strip.y,
			x: event.clientX,
			y: event.clientY
		};
		// The capture is a convenience, not the gesture: a pointer the browser no
		// longer tracks (one that already left the window, a synthetic one in a
		// harness) throws here and must not abort the drag.
		try {
			(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		} catch {
			/* the drag still works without capture */
		}
	}

	function stripMove(event: PointerEvent) {
		if (!grab) return;
		strip = clampStrip({ x: event.clientX - grab.dx, y: event.clientY - grab.dy });
	}

	function stripUp(event: PointerEvent) {
		if (!grab) return;
		// A drag is a *travelled* pointer, decided at release (`movementX/Y` stay
		// at 0 for a pointer the harness synthesises).
		const moved = Math.hypot(event.clientX - grab.x, event.clientY - grab.y) > 3;
		grab = null;
		try {
			(event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
		} catch {
			/* not captured: nothing to release */
		}
		// A plain click (no drag) puts the tools back: the keyboard's way out.
		if (!moved) strip = { x: 12, y: 12 };
		strip = clampStrip(strip);
		try {
			localStorage.setItem(STRIP_KEY, JSON.stringify(strip));
		} catch {
			/* a full or blocked storage is not worth a broken toolbar */
		}
	}

	// --- three.js --------------------------------------------------------------

	// The handles are reactive so every switch's own effect can re-apply itself
	// when the scene is rebuilt (a new figure, another clip): three.js objects
	// are class instances, so `$state` stores them without proxying them.
	//
	// The setup effect below WRITES these and never reads them — it works with
	// its own locals instead. Reading a handle it just wrote would make it
	// depend on itself, and Svelte would run it again and again (measured: 1001
	// runs on one figure).
	let scene = $state<THREE.Scene | null>(null);
	let camera = $state<THREE.PerspectiveCamera | null>(null);
	let renderer = $state<THREE.WebGLRenderer | null>(null);
	let controls = $state<OrbitControls | null>(null);
	let axesHelper = $state<THREE.AxesHelper | null>(null);
	let gridHelper = $state<THREE.GridHelper | null>(null);
	let skelHelper = $state<THREE.SkeletonHelper | null>(null);
	let root: THREE.Group | null = null;
	let frame = 0;
	let frameBox: THREE.Box3 | null = null;

	function fit() {
		if (!camera || !controls || !root) return;
		// Frame the model only: the grid/axes helpers must not drive the
		// zoom (they span 10 units and were making every figure tiny).
		// frameBox covers the whole idle loop (engine bind + sampled poses);
		// without a clip it falls back to the engine bind measured live.
		const box = frameBox ? frameBox.clone() : bindBoxOf(root);
		const center = box.getCenter(new THREE.Vector3());
		const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.5);
		const vFov = THREE.MathUtils.degToRad(camera.fov);
		const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
		const dist = (radius / Math.sin(Math.min(vFov, hFov) / 2)) * 1.1;
		camera.position.set(center.x, center.y + dist * 0.12, center.z + dist);
		camera.near = dist / 100;
		camera.far = dist * 100;
		camera.updateProjectionMatrix();
		controls.target.copy(center);
		controls.update();
	}

	/** The viewport as a PNG: the loop's own frame, re-rendered so the buffer
	 * is the one just drawn (no `preserveDrawingBuffer` cost on every frame). */
	function snapshot() {
		if (!renderer || !scene || !camera) return;
		renderer.render(scene, camera);
		const link = document.createElement('a');
		link.href = renderer.domElement.toDataURL('image/png');
		link.download = `${model.name.replace(/\.\w+$/, '')}.png`;
		link.click();
	}

	$effect(() => {
		// Announced once: a caller passing a fresh closure each render must not
		// turn this into an effect loop (it writes the caller's own state).
		if (announced || !onready) return;
		announced = true;
		onready({ snapshot });
	});

	$effect(() => {
		const canvas = canvasEl;
		if (!canvas) return;
		const parent = canvas.parentElement;
		if (!parent) return;
		frameBox = null;
		// A clip change re-grounds and re-frames the figure (its extent is the
		// clip's own), so the clock starts over with it.
		time = 0;
		let applied = -1;

		// The switches are read *untracked*: this effect owns the scene's
		// lifetime, each switch has its own effect below and applies itself
		// again when the scene is rebuilt.
		const initial = untrack(() => ({
			wireframe,
			showAxes,
			showGrid,
			turntable,
			bones,
			rot: { ...rot }
		}));

		const w = parent.clientWidth;
		const h = parent.clientHeight;
		const scn = new THREE.Scene();
		scn.background = new THREE.Color(0x141414);
		const cam = new THREE.PerspectiveCamera(50, w / h, 0.1, 1000);
		cam.position.set(1.5, 1.5, 2.5);

		const rend = new THREE.WebGLRenderer({ canvas, antialias: true });
		rend.setPixelRatio(Math.min(window.devicePixelRatio, 2));
		rend.setSize(w, h);
		rend.shadowMap.enabled = false;

		const light = new THREE.DirectionalLight(0xffffff, 2.4);
		light.position.set(3, 6, 4);
		scn.add(light);
		const rim = new THREE.DirectionalLight(0x8fb7ff, 0.8);
		rim.position.set(-4, 2, -3);
		scn.add(rim);
		scn.add(new THREE.AmbientLight(0xffffff, 0.35));

		const ctl = new OrbitControls(cam, canvas);
		ctl.enableDamping = true;
		ctl.dampingFactor = 0.08;
		ctl.autoRotate = initial.turntable;
		ctl.autoRotateSpeed = 1.0;

		const ax = new THREE.AxesHelper(1);
		ax.visible = initial.showAxes;
		const gr = new THREE.GridHelper(10, 20, 0x2e2e2e, 0x222222);
		gr.position.y = 0; // ground plane (models are grounded at Y = 0)
		gr.visible = initial.showGrid;
		scn.add(ax);
		scn.add(gr);

		root = model.scene;
		scn.add(root);
		// Fresh world matrices first (see render page): Box3 and the skin
		// rebind below must observe composed glTF worlds, not stale ones.
		root.updateMatrixWorld(true);
		root.traverse((obj) => {
			const mesh = obj as THREE.Mesh;
			if (mesh.isMesh) {
				mesh.material = new THREE.MeshStandardMaterial({
					color: 0x9fd8c8,
					metalness: 0.05,
					roughness: 0.9,
					// Smooth shading: the NIF exporter authors smooth normals and
					// DAoC's engine renders with them — flatShading would re-facet
					// every figure into the low-poly preview look.
					flatShading: false,
					side: THREE.DoubleSide,
					wireframe: initial.wireframe
				});
				const skinned = obj as THREE.SkinnedMesh;
				if (skinned.isSkinnedMesh) skinned.normalizeSkinWeights();
			}
		});

		// Bones overlay: the inspector's eye. `SkeletonHelper` rebuilds its
		// segments from the bones' *world* matrices every frame, so it follows
		// the pose — added to the scene, not to the scaled root.
		const sk = new THREE.SkeletonHelper(root);
		sk.setColors(new THREE.Color(0x2dd4a7), new THREE.Color(0x6a6a6a));
		sk.visible = initial.bones && sk.bones.length > 0;
		scn.add(sk);

		// Published now that they exist: the switch effects below (and the
		// toolbar) read them, this effect only writes them.
		scene = scn;
		camera = cam;
		renderer = rend;
		controls = ctl;
		axesHelper = ax;
		gridHelper = gr;
		skelHelper = sk;

		// Idle animation: map joint names to scene nodes once; the loop below
		// retargets local rotations (translations stay at the figure bind).
		// NOTE: three.js sanitizes node names (spaces become underscores).
		const joints = new SvelteMap<string, THREE.Object3D>();
		if (anim) {
			const wanted = new Set(anim.tracks.map((t) => t.node));
			const byName = new SvelteMap<string, THREE.Object3D>();
			root.traverse((obj) => {
				if (!byName.has(obj.name)) byName.set(obj.name, obj);
				const desanitized = obj.name.replace(/_/g, ' ');
				if (!byName.has(desanitized)) byName.set(desanitized, obj);
			});
			for (const name of wanted) {
				const joint = byName.get(name) ?? byName.get(name.replace(/ /g, '_')) ?? null;
				if (joint && !joints.has(name)) joints.set(name, joint);
			}
		}

		// Apply embedded DAoC textures (DDS via the shared decode path), one per mesh (skin slot).
		for (const [mesh, tex] of model.meshTextures) {
			const dt = textureFromDds(tex.bytes);
			const mat = mesh.material as THREE.MeshStandardMaterial;
			mat.map = dt;
			mat.color = tintColor(tex.tint);
			if (tex.doubleSided !== undefined) {
				mat.side = textureSide(tex.doubleSided);
			}
			if (tex.alpha) {
				mat.alphaTest = 0.5;
			}
			mat.needsUpdate = true;
		}

		// DAoC uses its own unit scale (figures can span ~80 units) and offsets
		// the model far from the origin. Normalise every figure to a consistent
		// size, centred on the origin, so the three.js scene scale stays sane.
		// The reference box covers the whole idle loop, engine bind included:
		// a root track that travels (amb_bird flies, the afanc dives) must
		// stay in frame and never clip through the grid; for calm idles the
		// union equals the bind box and nothing changes.
		const applyPoseAt = (tGame: number) => {
			if (!anim || anim.duration <= 0) return;
			const tKey = gameToKeyTime(anim, tGame);
			const pose = sampleClip(anim, tKey);
			const placement = samplePositions(anim, tKey);
			for (const [name, q] of pose) {
				const joint = joints.get(name);
				if (joint) joint.quaternion.set(q[1], q[2], q[3], q[0]);
			}
			for (const [name, p] of placement) {
				const joint = joints.get(name);
				if (joint) joint.position.set(p[0], p[1], p[2]);
			}
		};
		const box = bindBoxOf(root);
		if (anim && joints.size && anim.duration > 0) {
			// Dense enough to catch the loop's lowest point (the root track
			// can dip a few percent between keys); t=0 included so the
			// starting frame is never below the grid.
			for (let k = 0; k < 8; k++) {
				applyPoseAt((anim.duration * k) / 8);
				root.updateMatrixWorld(true);
				box.union(bindBoxOf(root));
			}
		}
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		const target = 5;
		const scale = target / Math.max(size.x, size.y, size.z, 1e-6);
		root.scale.setScalar(scale);
		// Ground the model: centre horizontally, put the loop's lowest point
		// on Y = 0.
		root.position.set(
			-center.x * scale,
			-center.y * scale + (size.y * scale) / 2,
			-center.z * scale
		);
		root.rotation.set(initial.rot.x, initial.rot.y, initial.rot.z);

		// Correct three.js skin binding (see rebindSkinnedMeshes): after the
		// model is scaled/grounded so the captured bind world is final.
		rebindSkinnedMeshes(scn);

		// Frame on the full animation extent afterwards (fit() runs on
		// resize / the frame button too). `fit` reads the published handles, so
		// it goes in untracked here.
		root.updateMatrixWorld(true);
		frameBox = box.clone().applyMatrix4(root.matrixWorld);
		untrack(fit);

		// render loop (drives the idle animation when present and playing)
		let drawn = 0;
		let mark = performance.now();
		let last = mark;
		const loop = () => {
			frame = requestAnimationFrame(loop);
			const now = performance.now();
			const dt = Math.min((now - last) / 1000, 0.1);
			last = now;
			if (anim && joints.size && anim.duration > 0) {
				if (playing) {
					// A one-shot parked at its end starts over when play is pressed
					// again: the button and the space bar only flip `playing`.
					if (time >= anim.duration) time = 0;
					const step = advance(time, dt * speed, anim.duration, clipLoops && repeat);
					time = step.time;
					if (step.ended) playing = false;
				}
				// A scrub (or a paused clock) still has to land on the figure.
				if (time !== applied) {
					applyPoseAt(time);
					applied = time;
				}
			}
			ctl.update();
			rend.render(scn, cam);

			drawn++;
			if (now - mark >= 500) {
				onfps?.(Math.round((drawn * 1000) / (now - mark)));
				drawn = 0;
				mark = now;
			}
		};
		// Started on the next frame, never from inside this effect: a synchronous
		// first call would make the clock and the switches dependencies of the
		// scene's own setup (a write to the clock would rebuild the scene).
		frame = requestAnimationFrame(loop);

		const onResize = () => {
			const cw = parent.clientWidth;
			const ch = parent.clientHeight;
			cam.aspect = cw / ch;
			cam.updateProjectionMatrix();
			rend.setSize(cw, ch);
		};
		const ro = new ResizeObserver(onResize);
		ro.observe(parent);

		return () => {
			cancelAnimationFrame(frame);
			ro.disconnect();
			sk.dispose();
			ctl.dispose();
			root?.traverse((obj) => {
				const mesh = obj as THREE.Mesh;
				mesh.geometry?.dispose();
				const m = mesh.material;
				if (Array.isArray(m)) m.forEach((mm) => mm.dispose());
				else m?.dispose();
			});
			rend.dispose();
			root = null;
			scene = camera = renderer = controls = axesHelper = gridHelper = skelHelper = null;
		};
	});

	$effect(() => {
		if (!scene) return;
		scene.traverse((obj) => {
			const mesh = obj as THREE.Mesh;
			const mat = mesh.material as THREE.MeshStandardMaterial | undefined;
			if (mat) mat.wireframe = wireframe;
		});
	});
	$effect(() => {
		if (root) {
			root.rotation.set(rot.x, rot.y, rot.z);
		}
	});
	$effect(() => {
		if (axesHelper) axesHelper.visible = showAxes;
		if (gridHelper) gridHelper.visible = showGrid;
	});
	$effect(() => {
		if (skelHelper) skelHelper.visible = bones;
	});
	$effect(() => {
		if (!controls) return;
		controls.autoRotate = turntable;
		controls.update();
	});

	/** F frames, space plays: the two gestures the status bar promises. */
	$effect(() => {
		const onKey = (event: KeyboardEvent) => {
			if (event.metaKey || event.ctrlKey || event.altKey) return;
			const el = event.target as HTMLElement | null;
			if (
				el &&
				(el.tagName === 'INPUT' ||
					el.tagName === 'SELECT' ||
					el.tagName === 'TEXTAREA' ||
					el.isContentEditable)
			) {
				return;
			}
			if (event.key === 'f' || event.key === 'F') {
				fit();
				event.preventDefault();
			} else if (event.key === ' ') {
				playing = !playing;
				event.preventDefault();
			}
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});
</script>

<div class="viewer" bind:this={viewerEl}>
	<canvas bind:this={canvasEl} aria-label="the figure, rendered"></canvas>

	<div
		class="strip floating"
		role="toolbar"
		aria-label="View"
		aria-orientation="vertical"
		style="left: {strip.x}px; top: {strip.y}px"
	>
		<button
			type="button"
			class="grip"
			title="drag to move the tools — click to put them back"
			aria-label="move the tools"
			onpointerdown={stripDown}
			onpointermove={stripMove}
			onpointerup={stripUp}
			onpointercancel={stripUp}
		>
			<GripHorizontal size={16} />
		</button>
		<button type="button" class="tool" onclick={fit} title="frame the figure (F)">
			<Maximize2 size={17} />
		</button>
		<button
			type="button"
			class="tool"
			class:on={turntable}
			aria-pressed={turntable}
			onclick={() => (turntable = !turntable)}
			title="turntable — let the camera orbit on its own"
		>
			<Rotate3d size={17} />
		</button>
		<button
			type="button"
			class="tool"
			class:on={wireframe}
			aria-pressed={wireframe}
			onclick={() => (wireframe = !wireframe)}
			title="wireframe — see the triangles the exporter wrote"
		>
			<Box size={17} />
		</button>
		<button
			type="button"
			class="tool"
			class:on={showGrid}
			aria-pressed={showGrid}
			onclick={() => (showGrid = !showGrid)}
			title="grid — 10 units, the figure stands on Y = 0"
		>
			<Grid3x3 size={17} />
		</button>
		<button
			type="button"
			class="tool"
			class:on={showAxes}
			aria-pressed={showAxes}
			onclick={() => (showAxes = !showAxes)}
			title="axes — the engine's own X/Y/Z at the origin"
		>
			<Axis3d size={17} />
		</button>
		<button
			type="button"
			class="tool"
			onclick={snapshot}
			title="snapshot — save the viewport as a PNG"
		>
			<Camera size={17} />
		</button>
		<span class="strip-sep"></span>
		<button
			type="button"
			class="tool"
			onclick={() => rotate(axis, -1)}
			title={`rotate ${axis.toUpperCase()} −90°`}
			aria-label={`rotate ${axis.toUpperCase()} anticlockwise 90 degrees`}
		>
			<RotateCcw size={17} />
		</button>
		<button
			type="button"
			class="tool axis-chip"
			onclick={() => (axis = axis === 'x' ? 'y' : axis === 'y' ? 'z' : 'x')}
			title="which axis the two rotation tools turn — click to change"
			aria-label={`turning ${axis.toUpperCase()}, click for the next axis`}
		>
			{axis.toUpperCase()}
		</button>
		<button
			type="button"
			class="tool"
			onclick={() => rotate(axis, 1)}
			title={`rotate ${axis.toUpperCase()} +90°`}
			aria-label={`rotate ${axis.toUpperCase()} clockwise 90 degrees`}
		>
			<RotateCw size={17} />
		</button>
	</div>

	{#if anim}
		<div class="transport-dock">
			<Transport clip={anim} bind:time bind:playing bind:repeat bind:speed {clipLoops} />
		</div>
	{/if}
</div>

<style>
	.viewer {
		position: relative;
		flex: 1;
		min-height: 0;
		height: 100%;
		overflow: hidden;
	}
	canvas {
		width: 100%;
		height: 100%;
		display: block;
	}
	.floating {
		position: absolute;
		z-index: 2;
	}
	.axis-chip {
		font-size: 13px;
		font-weight: 600;
	}
	.transport-dock {
		position: absolute;
		left: 50%;
		bottom: 16px;
		transform: translateX(-50%);
		width: min(520px, calc(100% - 48px));
		z-index: 2;
	}
</style>
