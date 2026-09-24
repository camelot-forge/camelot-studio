<script lang="ts">
	import { onMount } from 'svelte';
	import * as THREE from 'three';
	import { SvelteMap } from 'svelte/reactivity';
	import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
	import {
		loadFromGlb,
		rebindSkinnedMeshes,
		bindBoxOf,
		textureFromDds,
		textureSide,
		tintColor
	} from '$lib/gltf/load';
	import { sampleClip, samplePositions, gameToKeyTime, type AnimClip } from '$lib/anim';

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

		// Optional posed render: sample the idle clip at game time ?t=.
		// NOTE: three.js sanitizes node names (spaces become underscores), so
		// match tracks against both the raw and the sanitized scene names.
		let clip: AnimClip | null = null;
		const joints = new SvelteMap<string, THREE.Object3D>();
		if (animUrl) {
			const ares = await fetch(animUrl);
			if (ares.ok) {
				clip = (await ares.json()) as AnimClip;
				const byName = new SvelteMap<string, THREE.Object3D>();
				root.traverse((obj) => {
					if (!byName.has(obj.name)) byName.set(obj.name, obj);
					const desanitized = obj.name.replace(/_/g, ' ');
					if (!byName.has(desanitized)) byName.set(desanitized, obj);
				});
				for (const track of clip.tracks) {
					const name = track.node;
					const joint = byName.get(name) ?? byName.get(name.replace(/ /g, '_')) ?? null;
					if (joint && !joints.has(name)) joints.set(name, joint);
				}
			}
		}
		const applyPoseAt = (tGame: number): number => {
			if (!clip || clip.duration <= 0 || !joints.size) return 0;
			const tKey = gameToKeyTime(clip, tGame);
			const place = samplePositions(clip, tKey);
			let applied = 0;
			for (const [name, q] of sampleClip(clip, tKey)) {
				const joint = joints.get(name);
				if (!joint) continue;
				joint.quaternion.set(q[1], q[2], q[3], q[0]);
				applied++;
			}
			// Positions in their own pass: translation-only tracks (Beetle's
			// root bob) have no quats and were silently skipped here.
			for (const [name, p] of place) {
				const joint = joints.get(name);
				if (joint) joint.position.set(p[0], p[1], p[2]);
			}
			return applied;
		};

		// Optional Viewer-equivalent normalization (?norm=1): scale to ~5
		// units and ground the *animation loop's* lowest point at Y = 0
		// (engine bind union sampled poses), exactly like Viewer.svelte.
		// Rebind after positioning so the captured bind world is final.
		if (params.get('norm') === '1') {
			const nbox = bindBoxOf(root);
			if (clip) {
				for (let k = 0; k < 8; k++) {
					applyPoseAt((clip.duration * k) / 8);
					root.updateMatrixWorld(true);
					nbox.union(bindBoxOf(root));
				}
			}
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
		// ?textured=1 keeps the embedded DAoC DDS textures (same path as the
		// Viewer) so head/face texture regressions can be validated headlessly;
		// otherwise every mesh is overridden with a solid diagnostic colour.
		if (params.get('textured') === '1' && model.meshTextures.size) {
			for (const [mesh, tex] of model.meshTextures) {
				const dt = textureFromDds(tex.bytes);
				const mat = mesh.material as THREE.MeshStandardMaterial;
				mat.map = dt;
				mat.color = tintColor(tex.tint);
				// glTF's default material is fully metallic; without an env
				// map that renders near-black. Match the Viewer's shading.
				mat.metalness = 0.05;
				mat.roughness = 0.9;
				if (tex.doubleSided !== undefined) {
					mat.side = textureSide(tex.doubleSided);
				}
				if (tex.alpha) {
					mat.alphaTest = 0.5;
				}
				mat.needsUpdate = true;
			}
		} else {
			const flatShading = params.get('flat') === '1';
			root.traverse((obj) => {
				const mesh = obj as THREE.Mesh;
				if (mesh.isMesh) {
					mesh.material = flatShading
						? new THREE.MeshBasicMaterial({ color: 0x9fd8c8, side: THREE.DoubleSide })
						: new THREE.MeshStandardMaterial({
								color: 0x9fd8c8,
								flatShading: false,
								side: THREE.DoubleSide
							});
					const skinned = obj as THREE.SkinnedMesh;
					if (skinned.isSkinnedMesh) skinned.normalizeSkinWeights();
				}
			});
		}
		const posedJoints = clip ? applyPoseAt(Number.isFinite(at) ? at : 0) : 0;
		if (clip) root.updateMatrixWorld(true);

		// fixed 3/4 camera so orientation is consistent across shots.
		// bindBoxOf: the engine bind, not raw geometry — rigs whose stored
		// NiSkinData bind disagrees with their node tree render shifted.
		const box = bindBoxOf(root);
		const center = box.getCenter(new THREE.Vector3());
		const size = box.getSize(new THREE.Vector3());
		const radius = Math.max(size.length() / 2, 1);
		let target = center;
		if (params.get('fixedcam') === '1') {
			camera.position.set(90, 70, 150);
			camera.lookAt(0, 18, 8);
			target = new THREE.Vector3(0, 18, 8);
		} else if (params.get('focus') === 'head') {
			// Close-up on the head bone: head/hair regressions are invisible
			// in a full-body shot.
			const head =
				root.getObjectByName('Bip01 Head') ??
				root.getObjectByName('Bip01_Head') ??
				root.getObjectByName('Head');
			target = head ? new THREE.Vector3().setFromMatrixPosition(head.matrixWorld) : center;
			const d = Math.max(size.length() * 0.3, 1e-3);
			camera.position.set(target.x + d * 0.45, target.y + d * 0.12, target.z + d);
			camera.lookAt(target);
		} else {
			camera.position.set(center.x + radius, center.y + radius * 0.6, center.z + radius);
			camera.lookAt(center);
		}
		renderer.render(scene, camera);

		const controls = new OrbitControls(camera, canvas);
		controls.target.copy(target);
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
		(window as unknown as { __scene?: object }).__scene = scene;
		win.__dims = [wsize.x, wsize.y, wsize.z];
		win.__center = [wcenter.x, wcenter.y, wcenter.z];
		win.__posed = posedJoints;
		(window as unknown as { __textures?: number }).__textures = model.meshTextures.size;

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
			// Same path as bindBoxOf/applyBoneTransform (three's own skinning
			// math). A hand-rolled boneMatrices mix-up here silently shifted
			// the bounds; this stays in sync with what the GPU draws.
			const pts: THREE.Vector3[] = [];
			let joints = 0;
			const v = new THREE.Vector3();
			root.traverse((obj) => {
				const skinned = obj as THREE.SkinnedMesh;
				if (!skinned.isSkinnedMesh) return;
				skinned.skeleton.update();
				joints = skinned.skeleton.bones.length;
				skinned.bind(skinned.skeleton, skinned.matrixWorld);
				const pos = skinned.geometry.attributes.position as THREE.BufferAttribute;
				for (let vi = 0; vi < pos.count; vi++) {
					v.fromBufferAttribute(pos, vi);
					skinned.applyBoneTransform(vi, v);
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
