<script lang="ts">
	import * as THREE from 'three';
	import { SvelteMap } from 'svelte/reactivity';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import { DDSLoader } from 'three/addons/loaders/DDSLoader.js';
	import { Axis3d, Grid3x3, Maximize2, Pause, Play, Rotate3d } from '@lucide/svelte';
	import { rebindSkinnedMeshes, bindBoxOf, type LoadedModel } from '$lib/gltf/load';
	import { sampleClip, samplePositions, gameToKeyTime, type AnimClip } from '$lib/anim';

	interface Props {
		model: LoadedModel;
		anim?: AnimClip | null;
	}
	let { model, anim = null }: Props = $props();

	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let wireframe = $state(false);
	let showAxes = $state(true);
	let showGrid = $state(true);
	let playing = $state(true);
	let rotX = $state(0);
	let rotY = $state(0);
	let rotZ = $state(0);

	const AXES: Array<{ axis: 'x' | 'y' | 'z'; dir: 1 | -1 }> = [
		{ axis: 'x', dir: -1 },
		{ axis: 'x', dir: 1 },
		{ axis: 'y', dir: -1 },
		{ axis: 'y', dir: 1 },
		{ axis: 'z', dir: -1 },
		{ axis: 'z', dir: 1 }
	];

	function rotate(axis: 'x' | 'y' | 'z', dir: 1 | -1) {
		const delta = (dir * Math.PI) / 2;
		if (axis === 'x') rotX += delta;
		else if (axis === 'y') rotY += delta;
		else rotZ += delta;
	}

	let scene: THREE.Scene | null = null;
	let camera: THREE.PerspectiveCamera | null = null;
	let renderer: THREE.WebGLRenderer | null = null;
	let controls: OrbitControls | null = null;
	let axesHelper: THREE.AxesHelper | null = null;
	let gridHelper: THREE.GridHelper | null = null;
	let root: THREE.Group | null = null;
	let frame = 0;

	function fit() {
		if (!camera || !controls || !root) return;
		// Frame the model only: the grid/axes helpers must not drive the
		// zoom (they span 10 units and were making every figure tiny).
		// bindBoxOf, not setFromObject: the SkinnedMesh path folds the live
		// skeleton into the measurement instead of the bind geometry.
		const box = bindBoxOf(root);
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

	$effect(() => {
		const canvas = canvasEl;
		if (!canvas) return;
		const parent = canvas.parentElement;
		if (!parent) return;

		const w = parent.clientWidth;
		const h = parent.clientHeight;
		scene = new THREE.Scene();
		scene.background = new THREE.Color(0x141414);
		camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 1000);
		camera.position.set(1.5, 1.5, 2.5);

		renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
		renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
		renderer.setSize(w, h);
		renderer.shadowMap.enabled = false;

		const light = new THREE.DirectionalLight(0xffffff, 2.4);
		light.position.set(3, 6, 4);
		scene.add(light);
		const rim = new THREE.DirectionalLight(0x8fb7ff, 0.8);
		rim.position.set(-4, 2, -3);
		scene.add(rim);
		scene.add(new THREE.AmbientLight(0xffffff, 0.35));

		controls = new OrbitControls(camera, canvas);
		controls.enableDamping = true;
		controls.dampingFactor = 0.08;

		axesHelper = new THREE.AxesHelper(1);
		gridHelper = new THREE.GridHelper(10, 20, 0x2e2e2e, 0x222222);
		gridHelper.position.y = 0; // ground plane (models are grounded at Y = 0)
		scene.add(axesHelper);
		scene.add(gridHelper);

		root = model.scene;
		scene.add(root);
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
					flatShading: true,
					side: THREE.DoubleSide
				});
				const skinned = obj as THREE.SkinnedMesh;
				if (skinned.isSkinnedMesh) skinned.normalizeSkinWeights();
			}
		});

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

		// Apply embedded DAoC textures (DDS via DDSLoader), one per mesh (skin slot).
		if (model.meshTextures.size) {
			const dds = new DDSLoader();
			for (const [mesh, tex] of model.meshTextures) {
				const data = dds.parse(tex.bytes, true);
				const dt = new THREE.CompressedTexture(
					data.mipmaps,
					data.width,
					data.height,
					data.format as THREE.CompressedPixelFormat,
					THREE.UnsignedByteType
				);
				dt.colorSpace = THREE.SRGBColorSpace;
				dt.anisotropy = 4;
				dt.needsUpdate = true;
				const mat = mesh.material as THREE.MeshStandardMaterial;
				mat.map = dt;
				mat.color = new THREE.Color(0xffffff);
				mat.needsUpdate = true;
			}
		}

		// DAoC uses its own unit scale (figures can span ~80 units) and offsets
		// the model far from the origin. Normalise every figure to a consistent
		// size, centred on the origin, so the three.js scene scale stays sane.
		// bindBoxOf: Box3.setFromObject would measure skinned (posed)
		// positions instead of the bind geometry.
		const box = bindBoxOf(root);
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		const target = 5;
		const scale = target / Math.max(size.x, size.y, size.z, 1e-6);
		root.scale.setScalar(scale);
		// Ground the model: centre horizontally but sit the feet on Y = 0.
		root.position.set(
			-center.x * scale,
			-center.y * scale + (size.y * scale) / 2,
			-center.z * scale
		);
		root.rotation.set(rotX, rotY, rotZ);

		// Correct three.js skin binding (see rebindSkinnedMeshes): after the
		// model is scaled/grounded so the captured bind world is final.
		rebindSkinnedMeshes(scene);

		fit();

		// render loop (drives the idle animation when present and playing)
		const clock = new THREE.Clock();
		let animTime = 0;
		const loop = () => {
			frame = requestAnimationFrame(loop);
			const dt = Math.min(clock.getDelta(), 0.1);
			if (anim && playing && joints.size && anim.duration > 0) {
				animTime = (animTime + dt) % anim.duration;
				const tKey = gameToKeyTime(anim, animTime);
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
			}
			controls?.update();
			renderer?.render(scene as THREE.Scene, camera as THREE.PerspectiveCamera);
		};
		loop();

		const onResize = () => {
			if (!renderer || !camera || !parent) return;
			const cw = parent.clientWidth;
			const ch = parent.clientHeight;
			camera.aspect = cw / ch;
			camera.updateProjectionMatrix();
			renderer.setSize(cw, ch);
		};
		const ro = new ResizeObserver(onResize);
		ro.observe(parent);

		return () => {
			cancelAnimationFrame(frame);
			ro.disconnect();
			controls?.dispose();
			root?.traverse((obj) => {
				const mesh = obj as THREE.Mesh;
				mesh.geometry?.dispose();
				const m = mesh.material;
				if (Array.isArray(m)) m.forEach((mm) => mm.dispose());
				else m?.dispose();
			});
			renderer?.dispose();
			root = null;
			scene = camera = renderer = controls = null;
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
			root.rotation.set(rotX, rotY, rotZ);
		}
	});
	$effect(() => {
		if (axesHelper) axesHelper.visible = showAxes;
		if (gridHelper) gridHelper.visible = showGrid;
	});
</script>

<div class="viewer">
	<canvas bind:this={canvasEl}></canvas>
	<div class="toolbar">
		<button
			class="btn btn--icon"
			class:on={wireframe}
			title="Wireframe"
			aria-pressed={wireframe}
			onclick={() => (wireframe = !wireframe)}
		>
			<Axis3d size={16} />
		</button>
		<button
			class="btn btn--icon"
			class:on={showAxes}
			title="Axes"
			aria-pressed={showAxes}
			onclick={() => (showAxes = !showAxes)}
		>
			<Rotate3d size={16} />
		</button>
		<button
			class="btn btn--icon"
			class:on={showGrid}
			title="Grid"
			aria-pressed={showGrid}
			onclick={() => (showGrid = !showGrid)}
		>
			<Grid3x3 size={16} />
		</button>
		<button class="btn btn--icon" title="Reset view" onclick={fit}>
			<Maximize2 size={16} />
		</button>
		{#if anim}
			<button
				class="btn btn--icon"
				class:on={playing}
				title={playing ? `Pause idle (${anim.kfa})` : `Play idle (${anim.kfa})`}
				aria-pressed={playing}
				onclick={() => (playing = !playing)}
			>
				{#if playing}<Pause size={16} />{:else}<Play size={16} />{/if}
			</button>
		{/if}
		<span class="sep"></span>
		{#each AXES as { axis, dir } (axis + dir)}
			<button
				class="btn btn--icon axis"
				title={`Rotate ${axis.toUpperCase()} ${dir > 0 ? '+' : '−'}90°`}
				onclick={() => rotate(axis, dir)}
			>
				{axis.toUpperCase()}<span class="arrow">{dir > 0 ? '↻' : '↺'}</span>
			</button>
		{/each}
	</div>
</div>

<style>
	.viewer {
		position: relative;
		flex: 1;
		min-height: 0;
		height: 100%;
	}
	canvas {
		width: 100%;
		height: 100%;
		display: block;
	}
	.toolbar {
		position: absolute;
		top: 14px;
		right: 14px;
		display: flex;
		gap: 6px;
		padding: 6px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--r-input);
		box-shadow: var(--shadow-card);
	}
	.btn.on {
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	.sep {
		width: 1px;
		align-self: stretch;
		margin: 2px 4px;
		background: var(--border);
	}
	.btn--icon.axis {
		font-size: 11px;
		font-weight: 650;
		font-variant-numeric: tabular-nums;
		gap: 1px;
		padding: 6px 7px;
		line-height: 1;
	}
	.arrow {
		font-size: 12px;
		color: var(--accent);
	}
</style>
