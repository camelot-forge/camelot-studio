<script lang="ts">
	import { onMount } from 'svelte';
	import * as THREE from 'three';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import { loadFromGlb } from '$lib/gltf/load';

	// Diagnostic page: /render?glb=<url> draws a model with fixed axes/grid so
	// screenshots can be compared unambiguously (orientation regressions).
	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let status = $state('loading');

	onMount(async () => {
		const canvas = canvasEl;
		if (!canvas) return;
		const url = new URLSearchParams(location.search).get('glb');
		if (!url) {
			status = 'no glb';
			return;
		}
		const res = await fetch(url);
		if (!res.ok) {
			status = `fetch ${res.status}`;
			return;
		}
		const model = await loadFromGlb(await res.arrayBuffer(), 'model.glb');

		const parent = canvas.parentElement!;
		const w = parent.clientWidth;
		const h = parent.clientHeight;
		const scene = new THREE.Scene();
		scene.background = new THREE.Color(0x141414);
		const camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 1e6);
		const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
		renderer.setSize(w, h);
		scene.add(new THREE.AmbientLight(0xffffff, 0.6));
		const light = new THREE.DirectionalLight(0xffffff, 2.0);
		light.position.set(3, 6, 4);
		scene.add(light);
		scene.add(new THREE.AxesHelper(50));
		const grid = new THREE.GridHelper(200, 20, 0x2e2e2e, 0x222222);
		scene.add(grid);

		const root = model.scene;
		scene.add(root);
		root.traverse((obj) => {
			const mesh = obj as THREE.Mesh;
			if (mesh.isMesh) {
				mesh.material = new THREE.MeshStandardMaterial({
					color: 0x9fd8c8,
					flatShading: true,
					side: THREE.DoubleSide
				});
			}
		});

		// fixed 3/4 camera so orientation is consistent across shots
		const box = new THREE.Box3().setFromObject(root);
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		const radius = Math.max(size.length() / 2, 1);
		camera.position.set(center.x + radius, center.y + radius * 0.6, center.z + radius);
		camera.lookAt(center);
		renderer.render(scene, camera);

		const controls = new OrbitControls(camera, canvas);
		controls.target.copy(center);
		controls.update();
		const loop = () => {
			requestAnimationFrame(loop);
			controls.update();
			renderer.render(scene, camera);
		};
		loop();
		status = 'ready';
		(window as unknown as { __ready: boolean }).__ready = true;
	});
</script>

<div class="wrap"><canvas bind:this={canvasEl}></canvas><span class="s">{status}</span></div>

<style>
	.wrap {
		position: fixed;
		inset: 0;
	}
	canvas {
		width: 100%;
		height: 100%;
		display: block;
	}
	.s {
		position: absolute;
		top: 8px;
		left: 10px;
		color: #2dd4a7;
		font: 12px monospace;
	}
</style>
