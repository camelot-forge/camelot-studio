<script lang="ts">
	import { onMount } from 'svelte';
	import * as THREE from 'three';
	import { SvelteMap } from 'svelte/reactivity';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import { loadFromGlb, rebindSkinnedMeshes, bindBoxOf } from '$lib/gltf/load';

	// Diagnostic page: /render?glb=<url> draws a model with fixed axes/grid so
	// screenshots can be compared unambiguously (orientation regressions).
	let canvasEl = $state<HTMLCanvasElement | null>(null);
	let status = $state('loading');

	onMount(async () => {
		const canvas = canvasEl;
		if (!canvas) return;
		const params = new URLSearchParams(location.search);
		const url = params.get('glb');
		const animUrl = params.get('anim');
		const at = Number(params.get('t') ?? '0');
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
		// Fresh world matrices first: Box3/rebind below must measure the
		// composed glTF worlds, not parse-time identity matrices.
		root.updateMatrixWorld(true);
		(window as unknown as { __axw?: number[] }).__axw = [
			...(root.children[0]?.matrixWorld.elements as unknown as number[])
		].map((x) => +x.toFixed(3));
		(window as unknown as { __wmark?: string }).__wmark = 'updated-v2';
		// Optional Viewer-equivalent normalization (?norm=1): scale to ~5
		// units and ground the bind box at Y = 0, exactly like Viewer.svelte.
		// Rebind after positioning so the captured bind world is final.
		if (params.get('norm') === '1') {
			const nbox = bindBoxOf(root);
			const ncenter = nbox.getCenter(new THREE.Vector3());
			const nsize = nbox.getSize(new THREE.Vector3());
			const nscale = 5 / Math.max(nsize.x, nsize.y, nsize.z, 1e-6);
			root.scale.setScalar(nscale);
			root.position.set(
				-ncenter.x * nscale,
				-ncenter.y * nscale + (nsize.y * nscale) / 2,
				-ncenter.z * nscale
			);
			root.updateMatrixWorld(true);
			(window as unknown as { __nbox?: object }).__nbox = {
				min: nbox.min.toArray().map((x) => +x.toFixed(4)),
				max: nbox.max.toArray().map((x) => +x.toFixed(4))
			};
		}
		(window as unknown as { __norm?: object }).__norm = {
			pos: root.position.toArray().map((x) => +x.toFixed(4)),
			scl: root.scale.toArray().map((x) => +x.toFixed(6))
		};
		rebindSkinnedMeshes(scene);
		const flatShading = params.get('flat') === '1';
		root.traverse((obj) => {
			const mesh = obj as THREE.Mesh;
			if (mesh.isMesh) {
				mesh.material = flatShading
					? new THREE.MeshBasicMaterial({ color: 0x9fd8c8, side: THREE.DoubleSide })
					: new THREE.MeshStandardMaterial({
							color: 0x9fd8c8,
							flatShading: true,
							side: THREE.DoubleSide
						});
				const skinned = obj as THREE.SkinnedMesh;
				if (skinned.isSkinnedMesh) skinned.normalizeSkinWeights();
			}
		});

		// Optional posed render: sample the idle clip at game time ?t=.
		// NOTE: three.js sanitizes node names (spaces become underscores), so
		// match tracks against both the raw and the sanitized scene names.
		let posedJoints = 0;
		if (animUrl) {
			const ares = await fetch(animUrl);
			if (ares.ok) {
				const { sampleClip, samplePositions, gameToKeyTime } = await import('$lib/anim');
				const clip = (await ares.json()) as Parameters<typeof sampleClip>[0];
				const tKey = gameToKeyTime(clip, Number.isFinite(at) ? at : 0);
				const byName = new SvelteMap<string, THREE.Object3D>();
				root.traverse((obj) => {
					if (!byName.has(obj.name)) byName.set(obj.name, obj);
					const desanitized = obj.name.replace(/_/g, ' ');
					if (!byName.has(desanitized)) byName.set(desanitized, obj);
				});
				const place = samplePositions(clip, tKey);
				for (const [name, q] of sampleClip(clip, tKey)) {
					const joint = byName.get(name) ?? byName.get(name.replace(/ /g, '_')) ?? null;
					if (joint) {
						joint.quaternion.set(q[1], q[2], q[3], q[0]);
						const p = place.get(name);
						if (p) joint.position.set(p[0], p[1], p[2]);
						posedJoints++;
					}
				}
				root.updateMatrixWorld(true);
			}
		}

		// fixed 3/4 camera so orientation is consistent across shots.
		// bindBoxOf: Box3.setFromObject would measure skinned (posed)
		// positions instead of the bind geometry.
		const box = bindBoxOf(root);
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		const radius = Math.max(size.length() / 2, 1);
		if (params.get('fixedcam') === '1') {
			camera.position.set(90, 70, 150);
			camera.lookAt(0, 18, 8);
		} else {
			camera.position.set(center.x + radius, center.y + radius * 0.6, center.z + radius);
			camera.lookAt(center);
		}
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
		const wbox = bindBoxOf(root);
		const wsize = wbox.getSize(new THREE.Vector3());
		const wcenter = wbox.getCenter(new THREE.Vector3());
		const win = window as unknown as {
			__ready: boolean;
			__dims?: number[];
			__center?: number[];
			__skinnedDims?: number[];
			__skinnedCenter?: number[];
			__joints?: number;
			__posed?: number;
		};
		win.__ready = true;
		win.__dims = [wsize.x, wsize.y, wsize.z];
		win.__center = [wcenter.x, wcenter.y, wcenter.z];
		win.__posed = posedJoints;

		// Diagnostic (?debug=1): true deformed bounds via a CPU skinning
		// replicate (Box3 ignores GPU skinning), for the anim_check harness.
		if (params.get('debug') === '1') {
			const raw: THREE.Vector3[] = [];
			root.traverse((obj) => {
				const mesh = obj as THREE.Mesh;
				if (!mesh.isMesh || !(mesh.geometry.attributes.position as THREE.BufferAttribute)) return;
				const pos = mesh.geometry.attributes.position as THREE.BufferAttribute;
				for (let vi = 0; vi < pos.count; vi++)
					raw.push(new THREE.Vector3().fromBufferAttribute(pos, vi).applyMatrix4(obj.matrixWorld));
			});
			const rawBox = new THREE.Box3().setFromPoints(raw);
			(win as unknown as { __rawMin?: number[] }).__rawMin = rawBox.min
				.toArray()
				.map((x) => +x.toFixed(4));
			const pts: THREE.Vector3[] = [];
			let joints = 0;
			const m = new THREE.Matrix4();
			const skinM = new THREE.Matrix4();
			const v = new THREE.Vector3();
			root.traverse((obj) => {
				const skinned = obj as THREE.SkinnedMesh;
				if (!skinned.isSkinnedMesh) return;
				skinned.skeleton.update();
				joints = skinned.skeleton.bones.length;
				const pos = skinned.geometry.attributes.position as THREE.BufferAttribute;
				const sIdx = skinned.geometry.attributes.skinIndex as THREE.BufferAttribute;
				const sW = skinned.geometry.attributes.skinWeight as THREE.BufferAttribute;
				const bm = skinned.skeleton.boneMatrices as unknown as Float32Array;
				for (let vi = 0; vi < pos.count; vi++) {
					skinM.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
					for (let k = 0; k < 4; k++) {
						const w = sW.getComponent(vi, k);
						if (!w) continue;
						const j = sIdx.getComponent(vi, k);
						m.fromArray(bm, j * 16);
						for (let e = 0; e < 16; e++) skinM.elements[e] += m.elements[e] * w;
					}
					v.fromBufferAttribute(pos, vi).applyMatrix4(skinned.bindMatrix);
					v.applyMatrix4(skinM).applyMatrix4(skinned.bindMatrixInverse);
					v.applyMatrix4(skinned.matrixWorld);
					pts.push(v.clone());
				}
			});
			const sbox = new THREE.Box3().setFromPoints(pts);
			const ssize = sbox.getSize(new THREE.Vector3());
			const scenter = sbox.getCenter(new THREE.Vector3());
			win.__skinnedDims = [ssize.x, ssize.y, ssize.z];
			win.__skinnedCenter = [scenter.x, scenter.y, scenter.z];
			win.__joints = joints;
		}
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
