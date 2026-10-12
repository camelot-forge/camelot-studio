// The `/world` scene: the client's terrain, its fixtures, and the overlays.
//
// Everything here consumes the daemon's artefacts (`/api/v1/world/…`): the
// CAMT terrain planes, glTF meshes with the sheets the NIF itself names, and
// the DDS→PNG ground sheets. Nothing parses a client file in the browser.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { api, type WorldFixture, type WorldSpawns, type WorldTextureLayer, type WorldZone } from '$lib/api/client';
import { loadFromGlb, textureFromDds, tintColor, textureSide } from '$lib/gltf/load';
import { groundMaterial, groundSlots, type GroundSlot } from './ground';
import {
	PATCH_CELLS,
	ZONE_SIZE,
	ZONE_UNIT,
	fixturePlacement,
	parseTerrain,
	patchGeometry,
	zoneNormals,
	type TerrainData
} from './terrain';

/** Bounds/water/jumps sit a hair above the ground so they never z-fight it. */
const OVERLAY_LIFT = 8;

/** The soft dot the row points are drawn with (one texture, shared). */
function dotSprite(): THREE.Texture {
	const size = 64;
	const canvas = document.createElement('canvas');
	canvas.width = size;
	canvas.height = size;
	const context = canvas.getContext('2d')!;
	const gradient = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
	gradient.addColorStop(0, 'rgba(255,255,255,1)');
	gradient.addColorStop(0.55, 'rgba(255,255,255,0.85)');
	gradient.addColorStop(1, 'rgba(255,255,255,0)');
	context.fillStyle = gradient;
	context.fillRect(0, 0, size, size);
	const texture = new THREE.CanvasTexture(canvas);
	texture.needsUpdate = true;
	return texture;
}

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

export interface WorldToggles {
	fixtures: boolean;
	ground: boolean;
	spawns: boolean;
	statics: boolean;
	doors: boolean;
	patrols: boolean;
	bounds: boolean;
	water: boolean;
	jumps: boolean;
}

interface LoadedMesh {
	model: Awaited<ReturnType<typeof loadFromGlb>>;
}

/** One instanced group: the mesh drawn and the fixtures it stands for, in
 * instance order — what a click raycasts against (Phase 6.3's selection). */
interface PickGroup {
	mesh: THREE.InstancedMesh;
	fixtures: WorldFixture[];
}

export interface Picked {
	fixture: WorldFixture;
	point: THREE.Vector3;
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
		spawns: new THREE.Group(),
		statics: new THREE.Group(),
		doors: new THREE.Group(),
		patrols: new THREE.Group(),
		bounds: new THREE.Group(),
		water: new THREE.Group(),
		jumps: new THREE.Group()
	};
	private dot: THREE.Texture;
	private spawnCounts = { mobs: 0, statics: 0, doors: 0, patrols: 0, patrolPoints: 0 };
	private meshCache = new Map<string, LoadedMesh>();
	private groundTextures = new Map<string, THREE.Texture>();
	private terrain: TerrainData | null = null;
	private texturedPatches = 0;
	private composedPatches = 0;
	private picks: PickGroup[] = [];
	private marker = new THREE.Group();
	private selected: WorldFixture | null = null;
	private token = 0;
	private frame = 0;

	constructor(canvas: HTMLCanvasElement) {
		this.canvas = canvas;
		this.dot = dotSprite();
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
		this.marker.visible = false;
		this.buildMarker();
		this.scene.add(this.marker);
		this.resize();
	}

	private buildMarker(): void {
		const ring = new THREE.Mesh(
			new THREE.RingGeometry(150, 210, 40),
			new THREE.MeshBasicMaterial({
				color: 0x67e8f9,
				side: THREE.DoubleSide,
				transparent: true,
				opacity: 0.9,
				depthTest: false
			})
		);
		ring.rotation.x = -Math.PI / 2;
		ring.position.y = 12;
		const pin = new THREE.Mesh(
			new THREE.ConeGeometry(70, 280, 12),
			new THREE.MeshBasicMaterial({ color: 0x67e8f9, depthTest: false })
		);
		pin.position.y = 1000;
		pin.rotation.x = Math.PI; // point down, at the object
		this.marker.add(ring, pin);
	}

	setToggles(toggles: WorldToggles): void {
		this.groups.fixtures.visible = toggles.fixtures;
		this.groups.ground.visible = toggles.ground;
		this.groups.spawns.visible = toggles.spawns;
		this.groups.statics.visible = toggles.statics;
		this.groups.doors.visible = toggles.doors;
		this.groups.patrols.visible = toggles.patrols;
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
			mobs: this.spawnCounts.mobs,
			statics: this.spawnCounts.statics,
			doors: this.spawnCounts.doors,
			patrols: this.spawnCounts.patrols,
			patrolPoints: this.spawnCounts.patrolPoints,
			bounds: this.groups.bounds.children.length,
			water: this.groups.water.children.length,
			jumps: this.groups.jumps.children.length,
			sheets: this.texturedPatches,
			composed: this.composedPatches
		};
	}

	// -- selection and focus (the map-editor ergonomics, 6.3's first slice) -----

	/** The fixture under a click, in normalised device coordinates. */
	pick(ndcX: number, ndcY: number): Picked | null {
		const ray = new THREE.Raycaster();
		ray.setFromCamera(new THREE.Vector2(ndcX, ndcY), this.camera);
		const hits = ray.intersectObjects(this.groups.fixtures.children, false);
		for (const hit of hits) {
			const group = this.picks.find((entry) => entry.mesh === hit.object);
			if (!group || hit.instanceId === undefined) continue;
			const fixture = group.fixtures[hit.instanceId];
			if (fixture) return { fixture, point: hit.point.clone() };
		}
		return null;
	}

	/** Mark a fixture: a ring at its feet and a pin above it, so a small
	 * object stays findable from a zone-wide camera. */
	select(fixture: WorldFixture | null): void {
		this.selected = fixture;
		if (!fixture || !this.terrain) {
			this.marker.visible = false;
			return;
		}
		const placement = fixturePlacement(this.terrain, fixture);
		this.marker.position.set(...placement.position);
		this.marker.visible = true;
	}

	get selection(): WorldFixture | null {
		return this.selected;
	}

	/** Recentre the camera on a fixture (Unity's F): close, slightly above. */
	focusFixture(fixture: WorldFixture, distance = 1400): void {
		if (!this.terrain) return;
		const placement = fixturePlacement(this.terrain, fixture);
		const target = new THREE.Vector3(...placement.position);
		this.controls.target.copy(target);
		this.camera.position.set(
			target.x + distance * 0.8,
			target.y + distance * 0.55,
			target.z + distance
		);
		this.controls.update();
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
		this.picks = [];
		this.selected = null;
		this.marker.visible = false;
		this.texturedPatches = 0;
		this.composedPatches = 0;
		this.spawnCounts = { mobs: 0, statics: 0, doors: 0, patrols: 0, patrolPoints: 0 };
		for (const texture of this.groundTextures.values()) texture.dispose();
		this.groundTextures.clear();
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

	/** The server's rows for the zone just loaded (spec §11 B1): mobs, statics,
	 * doors and patrols, in the same zone-local space as the fixtures. Drawn as
	 * points — a mob row is a position the server owns, not an actor with a
	 * pose — and a patrol as its whole line (the daemon keeps it unclipped).
	 * Call after `show`, which clears the groups. */
	showSpawns(spawns: WorldSpawns): void {
		this.spawnCounts.mobs = spawns.mobs.length;
		this.spawnCounts.statics = spawns.objects.length;
		this.spawnCounts.doors = spawns.doors.length;
		const mobs = this.points(spawns.mobs, 0xe0b23e, 90);
		if (mobs) this.groups.spawns.add(mobs);
		const statics = this.points(spawns.objects, 0x64b5f6, 150);
		if (statics) this.groups.statics.add(statics);
		const doors = this.points(spawns.doors, 0xef5350, 220);
		if (doors) this.groups.doors.add(doors);
		for (const path of spawns.paths) {
			if (path.points.length < 2) continue;
			const line = new THREE.Line(
				new THREE.BufferGeometry().setFromPoints(
					path.points.map((point) => new THREE.Vector3(point.x, point.z, point.y))
				),
				new THREE.LineBasicMaterial({ color: 0x8fd07a })
			);
			line.name = `patrol-${path.name}`;
			line.frustumCulled = false;
			this.groups.patrols.add(line);
			this.spawnCounts.patrols += 1;
			this.spawnCounts.patrolPoints += path.points.length;
		}
	}

	private points(rows: Array<{ x: number; y: number; z: number }>, color: number,
		size: number): THREE.Points | null {
		if (!rows.length) return null;
		const geometry = new THREE.BufferGeometry();
		geometry.setAttribute(
			'position',
			new THREE.Float32BufferAttribute(rows.flatMap((row) => [row.x, row.z, row.y]), 3)
		);
		const cloud = new THREE.Points(
			geometry,
			new THREE.PointsMaterial({
				color,
				size,
				sizeAttenuation: true,
				map: this.dot,
				transparent: true,
				alphaTest: 0.1,
				depthWrite: false
			})
		);
		cloud.frustumCulled = false; // the points span the zone
		return cloud;
	}

	private buildGround(zoneId: number, zone: WorldZone, data: TerrainData, token: number): void {
		const entries = new Set((zone.archives.tex?.entries ?? []).map((entry) => entry.toLowerCase()));
		// One normal field for the whole zone: the patches then share their
		// boundary normals and the grid does not show as 64 shaded tiles.
		const normals = zoneNormals(data);
		const patches = zone.patches;
		const layersByPatch = new Map<string, WorldTextureLayer[]>();
		for (const layer of patches?.layers ?? []) {
			const key = `${layer.patch_x},${layer.patch_y}`;
			const list = layersByPatch.get(key);
			if (list) list.push(layer);
			else layersByPatch.set(key, [layer]);
		}
		for (let py = 0; py < PATCH_CELLS; py++) {
			for (let px = 0; px < PATCH_CELLS; px++) {
				const patch = patchGeometry(data, px, py, normals);
				const geometry = new THREE.BufferGeometry();
				geometry.setAttribute('position', new THREE.BufferAttribute(patch.positions, 3));
				geometry.setAttribute('normal', new THREE.BufferAttribute(patch.normals, 3));
				geometry.setAttribute('uv', new THREE.BufferAttribute(patch.uvs, 2));
				geometry.setIndex(new THREE.BufferAttribute(patch.indices, 1));
				const material = new THREE.MeshLambertMaterial({ color: 0x66705a });
				const mesh = new THREE.Mesh(geometry, material);
				mesh.name = `patch-${px}-${py}`;
				this.groups.ground.add(mesh);
				// The client's own ground: its layer stack (up to 9 layers over the
				// masks) when the zone ships every file those layers name, the
				// pre-baked `texXX-YY` sheet (the same composition, 512²) else.
				const slots = groundSlots(
					layersByPatch.get(`${px},${py}`) ?? [],
					patches?.masks?.[`${px},${py}`] ?? []
				);
				const shippable =
					slots !== null && slots.every((slot) => (patches?.available ?? []).includes(slot.sheet));
				if (shippable) {
					void this.composePatch(zoneId, mesh, slots, [px, py], token);
					continue;
				}
				const entry = `tex${pad(px)}-${pad(py)}.dds`;
				if (!entries.has(entry)) continue;
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
						texture.anisotropy = this.anisotropy();
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

	/** The renderer's anisotropic filtering ceiling: the ground is the one
	 * surface seen at grazing angles for kilometres — low anisotropy is what
	 * made the far field read as a blur (the layer composition fixes the near
	 * field). */
	private anisotropy(): number {
		return Math.min(16, this.renderer.capabilities.getMaxAnisotropy());
	}

	/** One ground texture, loaded once and shared by every patch that names it. */
	private async groundTexture(
		url: string,
		flipY: boolean,
		wrap: THREE.Wrapping,
		token: number
	): Promise<THREE.Texture | null> {
		const cached = this.groundTextures.get(url);
		if (cached) return cached;
		try {
			const texture = await new THREE.TextureLoader().loadAsync(url);
			if (token !== this.token) {
				texture.dispose();
				return null;
			}
			texture.colorSpace = THREE.SRGBColorSpace;
			texture.wrapS = wrap;
			texture.wrapT = wrap;
			texture.anisotropy = this.anisotropy();
			// The layer sheets are read bottom-up (their row 0 is the tile's
			// south edge — see ground.ts); the masks are north-up images. A mask
			// covers its patch exactly, so it clamps: repeating it would blend a
			// patch's edge weights with the opposite edge, and every patch
			// boundary would show as a seam.
			texture.flipY = flipY;
			texture.needsUpdate = true;
			this.groundTextures.set(url, texture);
			return texture;
		} catch (error) {
			console.warn(`world: ground texture ${url} failed`, error);
			return null;
		}
	}

	/** The patch's material, once its sheets and masks are loaded. */
	private async composePatch(
		zoneId: number,
		mesh: THREE.Mesh,
		slots: GroundSlot[],
		patch: [number, number],
		token: number
	): Promise<void> {
		const wanted = new Map<string, { url: string; flipY: boolean; wrap: THREE.Wrapping }>();
		for (const slot of slots) {
			wanted.set(`sheet:${slot.sheet}`, {
				url: api.worldTerrainTexUrl(`${slot.sheet}.dds`),
				flipY: false,
				wrap: THREE.RepeatWrapping
			});
			wanted.set(`mask:${slot.mask}`, {
				url: api.worldTextureUrl(zoneId, 'ter', slot.mask),
				flipY: true,
				wrap: THREE.ClampToEdgeWrapping
			});
		}
		const loaded = new Map<string, THREE.Texture>();
		for (const [key, source] of wanted) {
			const texture = await this.groundTexture(source.url, source.flipY, source.wrap, token);
			if (!texture) return;
			loaded.set(key, texture);
		}
		if (token !== this.token) return;
		const material = groundMaterial(
			slots,
			{
				sheet: (slot) => loaded.get(`sheet:${slot.sheet}`) ?? null,
				mask: (name) => loaded.get(`mask:${name}`) ?? null
			},
			patch
		);
		if (!material) return;
		const previous = mesh.material as THREE.Material;
		mesh.material = material;
		previous.dispose();
		this.composedPatches += 1;
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
			this.picks.push({ mesh: instanced, fixtures });
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
				([x, y]) => new THREE.Vector3(x, data.heightAt(x, y) + OVERLAY_LIFT, y)
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
				for (const [x, y] of quad) positions.push(x, river.height + OVERLAY_LIFT, y);
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
			marker.position.set(jump.x, jump.z + ZONE_UNIT / 32, jump.y);
			marker.name = `jump-${jump.name}`;
			this.groups.jumps.add(marker);
		}
	}

	/** Move the camera to a zone-local point (the screenshot harness). */
	focus(x: number, y: number, distance = ZONE_UNIT * 1.5): void {
		const z = this.terrain ? this.terrain.heightAt(x, y) : 0;
		this.controls.target.set(x, z, y);
		this.camera.position.set(x + distance * 0.7, z + distance * 0.55, y + distance);
		this.controls.update();
	}

	/** Stand on a circle around a zone-local point and look at it: azimuth 0 is
	 * the scene's +Z — the client's *south* (its +y is scene +Z) — 90 is east,
	 * 180 north. The screenshot harness uses it to see a structure from a named
	 * side; north stands on a north-up view, framing it as the game does. */
	lookFrom(x: number, y: number, azimuthDeg: number, distance = ZONE_UNIT * 0.45): void {
		if (!this.terrain) return;
		const z = this.terrain.heightAt(x, y);
		const radians = (azimuthDeg * Math.PI) / 180;
		this.controls.target.set(x, z, y);
		this.camera.position.set(
			x + Math.sin(radians) * distance,
			z + distance * 0.4,
			y + Math.cos(radians) * distance
		);
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
		// North up, east right: the camera stands south of the zone centre
		// (scene +Z is the client's south) and looks north.
		const center = new THREE.Vector3(ZONE_SIZE / 2, mid, ZONE_SIZE / 2);
		this.controls.target.copy(center);
		this.camera.position.set(center.x, mid + ZONE_SIZE * 0.55, center.z + ZONE_SIZE * 0.75);
		this.controls.update();
	}
}
