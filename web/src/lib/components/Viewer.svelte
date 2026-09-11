<script lang="ts">
	import * as THREE from 'three';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import { DDSLoader } from 'three/addons/loaders/DDSLoader.js';
	import { Axis3d, Grid3x3, Maximize2, Rotate3d, RotateCw } from '@lucide/svelte';
	import type { LoadedModel } from '$lib/gltf/load';

	interface Props {
		model: LoadedModel;
	}
	let { model }: Props = $props();

	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let wireframe = $state(false);
	let showAxes = $state(true);
	let showGrid = $state(true);
	let rotX = $state(0);

	let scene: THREE.Scene | null = null;
	let camera: THREE.PerspectiveCamera | null = null;
	let renderer: THREE.WebGLRenderer | null = null;
	let controls: OrbitControls | null = null;
	let axesHelper: THREE.AxesHelper | null = null;
	let gridHelper: THREE.GridHelper | null = null;
	let root: THREE.Group | null = null;
	let frame = 0;

	function fit(scene: THREE.Scene) {
		const box = new THREE.Box3().setFromObject(scene);
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		if (!camera || !controls) return;
		const dist = Math.max(size.length(), 1) * 2.4;
		camera.position.set(center.x, center.y + dist * 0.6, center.z + dist);
		camera.near = dist / 200;
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
			}
		});

		// Apply embedded DAoC textures (DDS via DDSLoader) to the meshes.
		if (model.textures.length) {
			const sceneRoot = model.scene;
			const dds = new DDSLoader();
			for (const tex of model.textures) {
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
				sceneRoot.traverse((obj) => {
					const mesh = obj as THREE.Mesh;
					if (mesh.isMesh && mesh.material) {
						const mat = mesh.material as THREE.MeshStandardMaterial;
						mat.map = dt;
						mat.color = new THREE.Color(0xffffff);
						mat.needsUpdate = true;
					}
				});
			}
		}

		// DAoC uses its own unit scale (figures can span ~80 units) and offsets
		// the model far from the origin. Normalise every figure to a consistent
		// size, centred on the origin, so the three.js scene scale stays sane.
		const box = new THREE.Box3().setFromObject(root);
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
		root.rotation.set(rotX, 0, 0);

		fit(scene);

		// render loop
		const loop = () => {
			frame = requestAnimationFrame(loop);
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
			root.rotation.set(rotX, 0, 0);
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
		<button class="btn btn--icon" title="Reset view" onclick={() => scene && fit(scene)}>
			<Maximize2 size={16} />
		</button>
		<button
			class="btn btn--icon"
			title="Rotate 90° (DAoC models have no consistent up axis)"
			onclick={() => (rotX += Math.PI / 2)}
		>
			<RotateCw size={16} />
		</button>
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
</style>
