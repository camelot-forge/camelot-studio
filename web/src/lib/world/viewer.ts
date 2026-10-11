// The `/world` scene: the client's terrain, its fixtures, and the overlays.
//
// Everything here consumes the daemon's artefacts (`/api/v1/world/…`): the
// CAMT terrain planes, glTF meshes with the sheets the NIF itself names, and
// the DDS→PNG ground sheets. Nothing parses a client file in the browser.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { api, type WorldFixture, type WorldZone } from '$lib/api/client';
import { loadFromGlb, textureFromDds, tintColor, textureSide } from '$lib/gltf/load';
import {
	PATCH_CELLS,
	ZONE_SIZE,
	ZONE_UNIT,
	fixturePlacement,
	parseTerrain,
	patchGeometry,
	type TerrainData
} from './terrain';

/** Bounds/water/jumps sit a hair above the ground so they never z-fight it. */
const OVERLAY_LIFT = 8;

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

export interface WorldToggles {
	fixtures: boolean;
	ground: boolean;
	bounds: boolean;
	water: boolean;
	jumps: boolean;
}

interface LoadedMesh {
	model: Awaited<ReturnType<typeof loadFromGlb>>;
}

export class WorldViewer {
	private canvas: HTMLCanvasElement;
	private renderer: THREE.WebGLRenderer;
	private scene = new THREE.Scene();
	private camera: THREE.PerspectiveCamera;
	private controls: OrbitControls;
	private groups = {
		ground: new THREE.Group(),
		fixtures: new THREE.Group(),
		bounds: new THREE.Group(),
		water: new THREE.Group(),
		jumps: new THREE.Group()
	};
	private meshCache = new Map<string, LoadedMesh>();
	private terrain: TerrainData | null = null;
	private texturedPatches = 0;
	private token = 0;
	private frame = 0;

	constructor(canvas: HTMLCanvasElement) {
		this.canvas = canvas;
		this.scene.background = new THREE.Color(0x0f1418);
		this.scene.fog = new THREE.Fog(0x0f1418, 60000, 160000);
		this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
		this.camera = new THREE.PerspectiveCamera(50, 1, 10, 400000);
		this.controls = new OrbitControls(this.camera, canvas);
		this.controls.enableDamping = true;
		this.controls.dampingFactor = 0.08;
		this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
		this.scene.add(
			new THREE.HemisphereLight(0xdfeaff, 0x3a3f35, 1.0),
			new THREE.DirectionalLight(0xffffff, 1.6)
		);
		const sun = this.scene.children[this.scene.children.length - 1] as THREE.DirectionalLight;
		sun.position.set(0.55, 1, 0.35).multiplyScalar(50000);
		for (const group of Object.values(this.groups)) this.scene.add(group);
		this.resize();
	}

	setToggles(toggles: WorldToggles): void {
		this.groups.fixtures.visible = toggles.fixtures;
		this.groups.ground.visible = toggles.ground;
		this.groups.bounds.visible = toggles.bounds;
		this.groups.water.visible = toggles.water;
		this.groups.jumps.visible = toggles.jumps;
	}

	/** What the scene currently holds — the diagnostic hook the screenshots read. */
	stats(): Record<string, number | string> {
		const instances = this.groups.fixtures.children.reduce(
			(sum, child) => sum + ((child as THREE.InstancedMesh).count ?? 0),
			0
		);
		return {
			ground: this.groups.ground.children.length,
			fixtureGroups: this.groups.fixtures.children.length,
			fixtureInstances: instances,
			bounds: this.groups.bounds.children.length,
			water: this.groups.water.children.length,
			jumps: this.groups.jumps.children.length,
			sheets: this.texturedPatches
		};
	}

	resize(): void {
		const parent = this.canvas.parentElement;
		if (!parent) return;
		const width = parent.clientWidth || 1;
		const height = parent.clientHeight || 1;
		this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
		this.renderer.setSize(width, height, false);
		this.camera.aspect = width / height;
		this.camera.updateProjectionMatrix();
	}

	start(): void {
		const loop = () => {
			this.frame = requestAnimationFrame(loop);
			this.controls.update();
			this.renderer.render(this.scene, this.camera);
		};
		loop();
	}

	dispose(): void {
		cancelAnimationFrame(this.frame);
		this.clear();
		this.controls.dispose();
		this.renderer.dispose();
	}

	private clear(): void {
		for (const group of Object.values(this.groups)) {
			for (const child of [...group.children]) {
				group.remove(child);
				const mesh = child as THREE.Mesh;
				mesh.geometry?.dispose?.();
				const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
				if (Array.isArray(material)) material.forEach((m) => m.dispose());
				else material?.dispose?.();
			}
		}
	}

	/** Load one zone; a second call supersedes the first mid-flight. */
	async show(zoneId: number, zone: WorldZone): Promise<void> {
		const token = ++this.token;
		this.clear();
		this.meshCache.clear();
		this.texturedPatches = 0;
		const response = await fetch(api.worldTerrainUrl(zoneId));
		if (!response.ok) throw new Error(`terrain fetch failed (${response.status})`);
		const data = parseTerrain(await response.arrayBuffer());
		if (token !== this.token) return;
		this.terrain = data;
		this.buildGround(zoneId, zone, data, token);
		await this.buildFixtures(zoneId, zone, data, token);
		if (token !== this.token) return;
		this.buildOverlays(zone, data);
		this.frameZone(data);
	}

	private buildGround(zoneId: number, zone: WorldZone, data: TerrainData, token: number): void {
		const entries = new Set((zone.archives.tex?.entries ?? []).map((entry) => entry.toLowerCase()));
		for (let py = 0; py < PATCH_CELLS; py++) {
			for (let px = 0; px < PATCH_CELLS; px++) {
				const patch = patchGeometry(data, px, py);
				const geometry = new THREE.BufferGeometry();
				geometry.setAttribute('position', new THREE.BufferAttribute(patch.positions, 3));
				geometry.setAttribute('uv', new THREE.BufferAttribute(patch.uvs, 2));
				geometry.setIndex(new THREE.BufferAttribute(patch.indices, 1));
				geometry.computeVertexNormals();
				const material = new THREE.MeshLambertMaterial({ color: 0x66705a });
				const mesh = new THREE.Mesh(geometry, material);
				mesh.name = `patch-${px}-${py}`;
				this.groups.ground.add(mesh);
				const entry = `tex${pad(px)}-${pad(py)}.dds`;
				if (!entries.has(entry)) continue;
				// The client's pre-baked ground sheet for this patch (the
				// near-field layer composition is not emulated — see docs).
				new THREE.TextureLoader()
					.loadAsync(api.worldTextureUrl(zoneId, 'tex', entry))
					.then((texture) => {
						if (token !== this.token) {
							texture.dispose();
							return;
						}
						texture.colorSpace = THREE.SRGBColorSpace;
						texture.wrapS = THREE.ClampToEdgeWrapping;
						texture.wrapT = THREE.ClampToEdgeWrapping;
						texture.anisotropy = 4;
						material.map = texture;
						material.color.set(0xffffff);
						material.needsUpdate = true;
						this.texturedPatches += 1;
					})
					.catch((error) => {
						console.warn(`world: ground sheet ${entry} failed`, error);
					});
			}
		}
	}

	private async buildFixtures(
		zoneId: number,
		zone: WorldZone,
		data: TerrainData,
		token: number
	): Promise<void> {
		const byCatalog = new Map<number, WorldFixture[]>();
		for (const fixture of zone.fixtures) {
			if (!fixture.nif_id) continue;
			const list = byCatalog.get(fixture.nif_id);
			if (list) list.push(fixture);
			else byCatalog.set(fixture.nif_id, [fixture]);
		}
		for (const entry of zone.catalog) {
			const fixtures = byCatalog.get(entry.id);
			if (!entry.mesh || !fixtures?.length) continue;
			let loaded: LoadedMesh | undefined;
			try {
				loaded = await this.loadMesh(zoneId, entry.filename);
			} catch (error) {
				console.warn(`world: mesh ${entry.filename} failed`, error);
				continue; // a mesh the client cannot export: skip, the rest stands
			}
			if (token !== this.token) return;
			if (!loaded) continue;
			this.instance(loaded.model, fixtures, data);
		}
	}

	private async loadMesh(zoneId: number, filename: string): Promise<LoadedMesh | undefined> {
		const url = api.worldMeshUrl(zoneId, filename);
		const cached = this.meshCache.get(url);
		if (cached) return cached;
		const response = await fetch(url);
		if (!response.ok) return undefined;
		const model = await loadFromGlb(await response.arrayBuffer(), filename);
		const loaded = { model };
		this.meshCache.set(url, loaded);
		return loaded;
	}

	private instance(model: LoadedMesh['model'], fixtures: WorldFixture[], data: TerrainData): void {
		model.scene.updateMatrixWorld(true);
		const inner: THREE.Mesh[] = [];
		model.scene.traverse((object) => {
			if ((object as THREE.Mesh).isMesh) inner.push(object as THREE.Mesh);
		});
		for (const mesh of inner) {
			const material = this.materialFor(mesh, model);
			const instanced = new THREE.InstancedMesh(mesh.geometry, material, fixtures.length);
			const matrix = new THREE.Matrix4();
			const local = mesh.matrixWorld.clone();
			for (let i = 0; i < fixtures.length; i++) {
				const placement = fixturePlacement(data, fixtures[i]);
				matrix.compose(
					new THREE.Vector3(...placement.position),
					new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), placement.yaw),
					new THREE.Vector3(placement.scale, placement.scale, placement.scale)
				);
				matrix.multiply(local);
				instanced.setMatrixAt(i, matrix);
			}
			instanced.instanceMatrix.needsUpdate = true;
			instanced.frustumCulled = false; // the instances span the zone
			instanced.name = `fixtures-${mesh.name || 'mesh'}`;
			this.groups.fixtures.add(instanced);
		}
	}

	private materialFor(mesh: THREE.Mesh, model: LoadedMesh['model']): THREE.Material {
		const found = model.meshTextures.get(mesh);
		const material = (mesh.material as THREE.MeshStandardMaterial).clone();
		material.metalness = 0.05;
		material.roughness = 0.9;
		material.side = THREE.DoubleSide;
		if (found) {
			material.map = textureFromDds(found.bytes);
			material.color = tintColor(found.tint);
			if (found.doubleSided !== undefined) material.side = textureSide(found.doubleSided);
			if (found.alpha) material.alphaTest = 0.5;
		}
		material.needsUpdate = true;
		return material;
	}

	private buildOverlays(zone: WorldZone, data: TerrainData): void {
		for (const bound of zone.bounds) {
			if (bound.points.length < 2) continue;
			const points = bound.points.map(
				([x, y]) => new THREE.Vector3(x, data.heightAt(x, y) + OVERLAY_LIFT, -y)
			);
			const geometry = new THREE.BufferGeometry().setFromPoints(points);
			const line = new THREE.LineLoop(geometry, new THREE.LineBasicMaterial({ color: 0xffd166 }));
			this.groups.bounds.add(line);
		}
		for (const river of zone.water) {
			const pairs = Math.min(river.left.length, river.right.length);
			if (pairs < 2) continue;
			const positions: number[] = [];
			for (let i = 0; i < pairs - 1; i++) {
				const [lx0, ly0] = river.left[i];
				const [rx0, ry0] = river.right[i];
				const [lx1, ly1] = river.left[i + 1];
				const [rx1, ry1] = river.right[i + 1];
				const quad = [
					[lx0, ly0],
					[rx0, ry0],
					[rx1, ry1],
					[lx0, ly0],
					[rx1, ry1],
					[lx1, ly1]
				];
				for (const [x, y] of quad) positions.push(x, river.height + OVERLAY_LIFT, -y);
			}
			const geometry = new THREE.BufferGeometry();
			geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
			geometry.computeVertexNormals();
			const mesh = new THREE.Mesh(
				geometry,
				new THREE.MeshLambertMaterial({
					color: river.kind.toUpperCase() === 'LAVA' ? 0xc1440e : 0x2f6f9f,
					transparent: true,
					opacity: 0.6,
					side: THREE.DoubleSide
				})
			);
			mesh.name = `water-${river.name}`;
			this.groups.water.add(mesh);
		}
		for (const jump of zone.jumps) {
			const geometry = new THREE.ConeGeometry(ZONE_UNIT / 64, ZONE_UNIT / 16, 12);
			const marker = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x67e8f9 }));
			marker.position.set(jump.x, jump.z + ZONE_UNIT / 32, -jump.y);
			marker.name = `jump-${jump.name}`;
			this.groups.jumps.add(marker);
		}
	}

	/** Move the camera to a zone-local point (the screenshot harness). */
	focus(x: number, y: number, distance = ZONE_UNIT * 1.5): void {
		const z = this.terrain ? this.terrain.heightAt(x, y) : 0;
		this.controls.target.set(x, z, -y);
		this.camera.position.set(x + distance * 0.7, z + distance * 0.55, -y + distance);
		this.controls.update();
	}

	private frameZone(data: TerrainData): void {
		let minZ = Infinity;
		let maxZ = -Infinity;
		for (let i = 0; i <= 16; i++) {
			for (let j = 0; j <= 16; j++) {
				const z = data.heightAt((i * ZONE_SIZE) / 16, (j * ZONE_SIZE) / 16);
				minZ = Math.min(minZ, z);
				maxZ = Math.max(maxZ, z);
			}
		}
		const mid = (minZ + maxZ) / 2;
		const center = new THREE.Vector3(ZONE_SIZE / 2, mid, -ZONE_SIZE / 2);
		this.controls.target.copy(center);
		this.camera.position.set(center.x, mid + ZONE_SIZE * 0.55, center.z + ZONE_SIZE * 0.75);
		this.controls.update();
	}
}
