// Debug: inspect scene materials/textures via the exposed __scene.
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://localhost:8760';
const name = process.argv[3] ?? 'bsaracenf';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 800, height: 800 } });
const errors = [];
page.on('console', (m) => {
	if (m.type() === 'error') errors.push(m.text());
});
const qs = new URLSearchParams({ q: name, type: 'nif', limit: '5' });
const res = await fetch(`${base}/api/v1/assets?${qs}`);
const hit = (await res.json()).items[0];
const glb = `/api/v1/assets/${encodeURIComponent(hit.id)}/model`;
await page.goto(`${base}/render?glb=${encodeURIComponent(glb)}&textured=1&norm=1&focus=head`, {
	waitUntil: 'load'
});
await page.waitForFunction('window.__ready === true', { timeout: 30000 }).catch(() => {});
const info = await page.evaluate(() => {
	const scene = window.__scene;
	if (!scene) return { error: 'no scene' };
	const rows = [];
	const maps = new Map();
	scene.traverse((obj) => {
		if (!obj.isMesh) return;
		const mat = obj.material;
		const mid = mat.uuid;
		const tid = mat.map ? mat.map.uuid : null;
		maps.set(mid, (maps.get(mid) ?? 0) + 1);
		rows.push({
			name: obj.name,
			mat: mid,
			tex: tid,
			mapW: mat.map?.width ?? null,
			isSkinned: !!obj.isSkinnedMesh
		});
	});
	return { rows, materials: maps.size, textures: new Set(rows.map((r) => r.tex)).size };
});
console.log('materials:', info.materials, 'textures:', info.textures);
for (const r of info.rows.slice(0, 12)) console.log(JSON.stringify(r));
console.log('console errors:', errors.slice(0, 5));
await browser.close();
