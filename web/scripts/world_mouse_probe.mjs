// The mouse grammar, driven like a hand and *measured*: one selection, one
// gesture, one transform — every item of a group must take the same numbers.
// Throwaway harness; nothing is published. Exits non-zero on a failed check.
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8762';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
const zoneName = process.argv[4] ?? 'Camelot Hills';
mkdirSync(outDir, { recursive: true });
const SHOT = { timeout: 120000 };

let failures = 0;
function check(label, ok, detail = '') {
	console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ` — ${detail}` : ''}`);
	if (!ok) failures++;
}

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

const state = () => page.evaluate('window.__worldEditor ? window.__worldEditor() : null');
const journal = () =>
	page.evaluate(
		`(async () => (await fetch('/api/v1/world/edit', { cache: 'no-store' }).then((r) => r.json())).zones)()`
	);
const journalRows = () =>
	page.evaluate(
		`(async () => (await fetch('/api/v1/world/edit', { cache: 'no-store' }).then((r) => r.json())).rows)()`
	);
const items = () => page.evaluate('window.__worldViewer.items()');
const focus = (x, y, distance) => page.evaluate(`window.__worldFocus(${x}, ${y}, ${distance})`);
const selectedFixtures = () =>
	page.evaluate('window.__worldViewer.selectionView.fixtures.map((f) => f.id)');
const selectedRows = () =>
	page.evaluate('window.__worldViewer.selectionView.rows.map((r) => r.table + ":" + r.id)');
const groundAt = (x, y) => page.evaluate(`window.__worldViewer.groundAt(${x}, ${y})`);
const screenAt = (x, y, height) =>
	page.evaluate(`window.__worldViewer.screenAt(${x}, ${y}, ${height})`);
const planeAt = (x, y, height) =>
	page.evaluate(`window.__worldViewer.planeAt(${x}, ${y}, ${height})`);

async function snapshot() {
	const data = await items();
	return {
		fixtures: new Map(data.fixtures.map((row) => [row.id, row])),
		rows: new Map(data.rows.map((row) => [`${row.table}:${row.id}`, row]))
	};
}

/** Everything that moved, with its own delta. */
function moved(before, after) {
	const list = [];
	for (const [id, was] of before.fixtures) {
		const now = after.fixtures.get(id);
		if (!now) continue;
		if (
			now.x !== was.x ||
			now.y !== was.y ||
			now.z !== was.z ||
			now.angle !== was.angle ||
			now.scale !== was.scale
		) {
			list.push({
				kind: 'fixture',
				id: `f${id}`,
				from: was,
				to: now,
				dx: now.x - was.x,
				dy: now.y - was.y,
				dz: now.z - was.z,
				da: now.angle - was.angle,
				ds: now.scale - was.scale
			});
		}
	}
	for (const [id, was] of before.rows) {
		const now = after.rows.get(id);
		if (!now) continue;
		if (now.x !== was.x || now.y !== was.y || now.z !== was.z || now.heading !== was.heading) {
			list.push({
				kind: 'row',
				id,
				from: was,
				to: now,
				dx: now.x - was.x,
				dy: now.y - was.y,
				dz: now.z - was.z,
				dh: (now.heading ?? 0) - (was.heading ?? 0)
			});
		}
	}
	return list;
}

function sameVector(list, pick) {
	const vectors = new Set(list.map((item) => `${pick(item)}`));
	return { ok: vectors.size <= 1, vectors: [...vectors] };
}

/** The client's own turn: degrees clockwise on the map, whose `y` grows south.
 * The fixture frame measures it (0 = north, +90 = east) and so does a carried
 * row — this is that same arithmetic, written out independently. */
function rotateAbout(x, y, pivot, degrees) {
	const radians = (degrees * Math.PI) / 180;
	const dx = x - pivot.x;
	const dy = y - pivot.y;
	const cos = Math.cos(radians);
	const sin = Math.sin(radians);
	return { x: pivot.x + dx * cos - dy * sin, y: pivot.y + dx * sin + dy * cos };
}

const journalCount = async () => {
	const zones = await journal();
	const rows = await journalRows();
	return {
		ops: zones.reduce((sum, zone) => sum + zone.ops.length, 0),
		patches: rows.reduce((sum, entry) => sum + entry.patches.length, 0)
	};
};

const patchedKeys = async () => {
	const rows = await journalRows();
	return rows.flatMap((entry) => entry.patches.map((patch) => `${patch.table}:${patch.key}`));
};

/** What a gesture added to the journal's patches, per key. */
function addedKeys(before, after) {
	const count = (keys) => {
		const map = new Map();
		for (const key of keys) map.set(key, (map.get(key) ?? 0) + 1);
		return map;
	};
	const was = count(before);
	const now = count(after);
	const added = new Map();
	for (const [key, seen] of now) {
		const delta = seen - (was.get(key) ?? 0);
		if (delta !== 0) added.set(key, delta);
	}
	return [...added];
}

/** The zone's world rectangle: the journal writes rows in world coordinates. */
const zoneRect = async () => {
	const rect = await page.evaluate(`(async () => {
		const zone = await fetch('/api/v1/world/zone/' + window.__world.zone, { cache: 'no-store' }).then((r) => r.json());
		return zone.info.world_rect;
	})()`);
	return { left: rect[0], top: rect[1] };
};

/** The journal's own values, as the scene holds them: a fixture op's X/Y/A/Scale
 * are zone-local already; a row patch is world, so the rectangle is taken off. */
const journalValues = async () => {
	const rect = await zoneRect();
	const zones = await journal();
	const rows = await journalRows();
	const fixtures = new Map();
	for (const zone of zones) {
		for (const op of zone.ops) {
			if (op.action !== 'set') continue;
			const entry = fixtures.get(op.id) ?? {};
			if (op.values.X !== undefined) entry.x = Number(op.values.X);
			if (op.values.Y !== undefined) entry.y = Number(op.values.Y);
			if (op.values.A !== undefined) entry.angle = Number(op.values.A);
			if (op.values.Scale !== undefined) entry.scale = Number(op.values.Scale);
			fixtures.set(op.id, entry);
		}
	}
	const patched = new Map();
	for (const entry of rows) {
		for (const patch of entry.patches) {
			const key = `${patch.table}:${patch.key}`;
			const held = patched.get(key) ?? {};
			if (patch.values.X !== undefined) held.x = Number(patch.values.X) - rect.left;
			if (patch.values.Y !== undefined) held.y = Number(patch.values.Y) - rect.top;
			if (patch.values.Z !== undefined) held.z = Number(patch.values.Z);
			if (patch.values.Heading !== undefined) held.heading = Number(patch.values.Heading);
			patched.set(key, held);
		}
	}
	return { fixtures, patched };
};

/** The invariant the whole editor rests on: what stands on the screen is what
 * the journal holds. A gesture whose write the daemon dropped shows up here. */
const sceneIsJournal = async (movedItems, label) => {
	const held = await journalValues();
	const problems = [];
	for (const item of movedItems) {
		if (item.kind === 'fixture') {
			const row = held.fixtures.get(Number(item.id.slice(1)));
			if (!row) {
				problems.push(`${item.id} has no op`);
				continue;
			}
			if ((item.dx !== 0 || item.dy !== 0) && (row.x !== item.to.x || row.y !== item.to.y)) {
				problems.push(
					`${item.id} at ${row.x},${row.y} (journal) vs ${item.to.x},${item.to.y} (scene)`
				);
			}
			if (item.da !== 0 && row.angle !== item.to.angle) {
				problems.push(`${item.id} A ${row.angle} vs ${item.to.angle}`);
			}
			if (item.ds !== 0 && row.scale !== item.to.scale) {
				problems.push(`${item.id} scale ${row.scale} vs ${item.to.scale}`);
			}
			continue;
		}
		const row = held.patched.get(item.id);
		if (!row) {
			problems.push(`${item.id} has no patch`);
			continue;
		}
		if ((item.dx !== 0 || item.dy !== 0) && (row.x !== item.to.x || row.y !== item.to.y)) {
			problems.push(
				`${item.id} at ${row.x},${row.y} (journal) vs ${item.to.x},${item.to.y} (scene)`
			);
		}
		if (item.dz !== 0 && row.z !== item.to.z) {
			problems.push(`${item.id} Z ${row.z} vs ${item.to.z}`);
		}
	}
	check(
		`the journal holds what the scene shows (${label})`,
		problems.length === 0,
		problems.join(' | ')
	);
};

await page.goto(`${base}/world`, { waitUntil: 'load' });
// the session is emptied *before* a zone is opened: a payload read while the
// journal still held an earlier run's ops would be stale from the first frame
await page.waitForFunction('document.querySelectorAll("aside select option").length > 0', {
	timeout: 120000
});
await page.evaluate('(async () => await fetch("/api/v1/world/mod/revert", { method: "POST" }))()');
await page.fill('input[type=search]', zoneName);
await page.locator('.zones button', { hasText: zoneName }).first().click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 180000 });
await page.waitForTimeout(4000);

const canvasBox = await page.locator('canvas').boundingBox();
const middle = { x: canvasBox.x + canvasBox.width / 2, y: canvasBox.y + canvasBox.height / 2 };
/** The editor's commits are a queue: wait for it to drain before measuring, or
 * a snapshot catches the scene mid-flight and blames the gesture. */
const settle = async () => {
	for (let i = 0; i < 60; i++) {
		const busy = await page.evaluate('window.__worldEditor().busy === true');
		if (!busy) {
			await page.waitForTimeout(250);
			if (!(await page.evaluate('window.__worldEditor().busy === true'))) return;
		}
		await page.waitForTimeout(150);
	}
	console.log('warning: the editor never settled');
};
const drag = async (button, from, to, steps = 10) => {
	await page.mouse.move(from.x, from.y);
	await page.mouse.down({ button });
	await page.mouse.move(to.x, to.y, { steps });
	await page.mouse.up({ button });
	await settle();
};

// 1. click selects one fixture -------------------------------------------------
const stone = await page.evaluate(`(async () => {
	const zone = await fetch('/api/v1/world/zone/' + window.__world.zone, { cache: 'no-store' }).then((r) => r.json());
	return zone.fixtures.find((f) => f.name.toLowerCase().includes('stone')) ?? zone.fixtures[0];
})()`);
await focus(stone.x, stone.y, 2400);
await page.waitForTimeout(1200);
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'left' });
await page.mouse.up({ button: 'left' });
await page.waitForTimeout(500);
const clicked = await selectedFixtures();
console.log('click selects:', JSON.stringify(clicked));
check('a left click selects something', clicked.length >= 1);

// 2. a box moves nothing -------------------------------------------------------
const beforeMode = await snapshot();
const quietOps = await journalCount();
await drag('left', { x: canvasBox.x + 60, y: canvasBox.y + 60 }, { x: middle.x, y: middle.y }, 8);
const afterMode = await snapshot();
check(
	'the box selects something',
	(await page.evaluate(
		'window.__worldViewer.selectionView.fixtures.length + window.__worldViewer.selectionView.rows.length'
	)) > 0
);
check('the box itself moves nothing', moved(beforeMode, afterMode).length === 0);
const quietAfter = await journalCount();
check(
	'the box journals nothing',
	quietAfter.ops === quietOps.ops && quietAfter.patches === quietOps.patches
);

// 3. the operator's own case: several items, one rigid move --------------------
await focus(stone.x, stone.y, 1400);
await page.waitForTimeout(1200);
for (const half of [120, 200, 280]) {
	await drag(
		'left',
		{ x: middle.x - half, y: middle.y - half },
		{ x: middle.x + half, y: middle.y + half },
		8
	);
	const count = await page.evaluate(
		'window.__worldViewer.selectionView.fixtures.length + window.__worldViewer.selectionView.rows.length'
	);
	if (count >= 3) break;
}
const group = { f: await selectedFixtures(), r: await selectedRows() };
console.log('group selected:', JSON.stringify(group));
check('the box caught several items', group.f.length + group.r.length >= 3);

const beforeMove = await snapshot();
const journalBeforeMove = await journalCount();
const keysBeforeMove = await patchedKeys();
const moveFrom = { x: middle.x, y: middle.y };
const moveTo = { x: middle.x + 200, y: middle.y + 120 };
const anchor = await groundAt(moveFrom.x, moveFrom.y);
await drag('middle', moveFrom, moveTo, 10);
const afterMove = await snapshot();
const movedItems = moved(beforeMove, afterMove);
console.log(
	'moved items:',
	movedItems.map((item) => `${item.id} (${item.dx}, ${item.dy})`).join(' ')
);
check('the move moved something', movedItems.length > 0);
const rigid = sameVector(movedItems, (item) => `${item.dx},${item.dy}`);
check('every item took the same vector', rigid.ok, rigid.vectors.join(' | '));
const end = await planeAt(moveTo.x, moveTo.y, anchor.z);
const expected = {
	dx: Math.round((end.x - anchor.x) / 256) * 256,
	dy: Math.round((end.y - anchor.y) / 256) * 256
};
check(
	'the vector is the snapped ground vector',
	movedItems.length > 0 &&
		movedItems.every((item) => item.dx === expected.dx && item.dy === expected.dy),
	`expected ${expected.dx},${expected.dy}`
);
const movedRowVectors = new Map(
	movedItems.filter((item) => item.kind === 'row').map((item) => [item.id, `${item.dx},${item.dy}`])
);
check(
	'every selected row moved with the group',
	group.r.length > 0 &&
		group.r.every((key) => movedRowVectors.get(key) === `${expected.dx},${expected.dy}`),
	`selected ${JSON.stringify(group.r)} moved ${JSON.stringify([...movedRowVectors])}`
);
const journalAfterMove = await journalCount();
check(
	'one op per changed fixture',
	journalAfterMove.ops - journalBeforeMove.ops === group.f.length,
	`${journalAfterMove.ops - journalBeforeMove.ops} ops for ${group.f.length} fixtures`
);
const selectedKeys = new Set([...group.f.map((id) => `f${id}`), ...group.r]);
const movedRowKeys = movedItems.filter((item) => item.kind === 'row').map((item) => item.id);
const added = addedKeys(keysBeforeMove, await patchedKeys());
const carriedKeys = new Set(added.map(([key]) => key));
check(
	'only the selection (and the rows it carries) moved',
	movedItems.every(
		(item) => selectedKeys.has(item.id) || (item.kind === 'row' && carriedKeys.has(item.id))
	),
	movedItems
		.filter((item) => !selectedKeys.has(item.id))
		.map((item) => item.id)
		.join(' ')
);
check(
	'every moved row is patched exactly once',
	movedRowKeys.length > 0 &&
		movedRowKeys.every((key) => added.filter(([patched]) => patched === key).length === 1),
	`${movedRowKeys.length} moved rows, added ${JSON.stringify(added.map(([key, seen]) => `${key.slice(0, 14)}×${seen}`))}`
);
check(
	'no row is patched twice by one gesture',
	added.every(([, seen]) => seen === 1),
	JSON.stringify(added.filter(([, seen]) => seen !== 1))
);
await sceneIsJournal(movedItems, 'move');

// 4. snap off: the raw ground vector, still one for all ------------------------
await page.getByTitle('256 units, 15°, 5%, 16 up').click();
const beforeFree = await snapshot();
const freeAnchor = await groundAt(moveFrom.x, moveFrom.y);
const freeTo = { x: moveFrom.x - 160, y: moveFrom.y + 90 };
await drag('middle', moveFrom, freeTo, 10);
const afterFree = await snapshot();
const freeMoved = moved(beforeFree, afterFree);
const freeEnd = await planeAt(freeTo.x, freeTo.y, freeAnchor.z);
const freeExpected = {
	dx: Math.round(freeEnd.x - freeAnchor.x),
	dy: Math.round(freeEnd.y - freeAnchor.y)
};
const freeRigid = sameVector(freeMoved, (item) => `${item.dx},${item.dy}`);
check(
	'snap off: every item still took the same vector',
	freeRigid.ok,
	freeRigid.vectors.join(' | ')
);
check(
	'snap off: the vector is the raw ground vector',
	freeMoved.length > 0 &&
		freeMoved.every((item) => item.dx === freeExpected.dx && item.dy === freeExpected.dy),
	`expected ${freeExpected.dx},${freeExpected.dy}`
);
await page.getByTitle('256 units, 15°, 5%, 16 up').click();

// 5. rotate: every item turns by the same angle about the same pivot -----------
await page.getByRole('button', { name: 'rotate', exact: true }).click();
const keysBeforeRotate = await patchedKeys();
const beforeRotate = await snapshot();
const selectedRowKeys = await selectedRows();
const pivotItems = [
	...group.f.map((id) => beforeRotate.fixtures.get(id)).filter(Boolean),
	...selectedRowKeys.map((key) => beforeRotate.rows.get(key)).filter(Boolean)
];
const pivot = pivotItems.reduce(
	(acc, row) => ({ x: acc.x + row.x / pivotItems.length, y: acc.y + row.y / pivotItems.length }),
	{ x: 0, y: 0 }
);
const pivotHeight = await page.evaluate(
	`window.__worldViewer.terrain.heightAt(${pivot.x}, ${pivot.y})`
);
const inside = (point) =>
	point.x > canvasBox.x + 30 &&
	point.x < canvasBox.x + canvasBox.width - 30 &&
	point.y > canvasBox.y + 30 &&
	point.y < canvasBox.y + canvasBox.height - 30;
let east = null;
let south = null;
for (const radius of [700, 500, 350, 240, 160]) {
	east = await screenAt(pivot.x + radius, pivot.y, pivotHeight);
	south = await screenAt(pivot.x, pivot.y + radius, pivotHeight);
	if (inside(east) && inside(south)) break;
}
check(
	'the rotate pivot is on screen',
	inside(east) && inside(south),
	JSON.stringify({ pivot, east, south })
);
await drag('middle', east, south, 12);
const afterRotate = await snapshot();
const turnedFixtures = group.f
	.map((id) => {
		const was = beforeRotate.fixtures.get(id);
		const now = afterRotate.fixtures.get(id);
		return was && now && now.angle !== was.angle ? { id, from: was, to: now } : null;
	})
	.filter(Boolean);
const angleDeltas = new Set(
	turnedFixtures.map(({ from, to }) => (((to.angle - from.angle) % 360) + 360) % 360)
);
check('every fixture turned', turnedFixtures.length === group.f.length);
check(
	'every fixture turned by the same angle',
	angleDeltas.size === 1,
	[...angleDeltas].join(' | ')
);
const degrees = [...angleDeltas][0] ?? 0;
check('the turn is a 15° step', degrees % 15 === 0 && degrees !== 0, `${degrees}°`);
const rotateFits = turnedFixtures.every(({ from, to }) => {
	const want = rotateAbout(from.x, from.y, pivot, degrees);
	return Math.abs(want.x - to.x) <= 1 && Math.abs(want.y - to.y) <= 1;
});
check('the fixtures turned about the pivot, the compass way', rotateFits);
// every row the gesture moved is either a directly-selected row (turned about
// the pivot) or one riding a fixture (turned in that fixture's own frame): the
// client's compass, checked against measured numbers either way
const rotateMoved = moved(beforeRotate, afterRotate).filter((item) => item.kind === 'row');
const rotateAdded = new Set(addedKeys(keysBeforeRotate, await patchedKeys()).map(([key]) => key));
const rideChecks = rotateMoved.map((row) => {
	const was = beforeRotate.rows.get(row.id);
	const now = afterRotate.rows.get(row.id);
	const pivotWant = rotateAbout(was.x, was.y, pivot, degrees);
	if (Math.abs(pivotWant.x - now.x) <= 1 && Math.abs(pivotWant.y - now.y) <= 1) {
		return { id: row.id, ok: true, how: 'about the pivot' };
	}
	for (const { from, to } of turnedFixtures) {
		const want = rotateAbout(to.x + (was.x - from.x), to.y + (was.y - from.y), to, degrees);
		if (Math.abs(want.x - now.x) <= 1 && Math.abs(want.y - now.y) <= 1) {
			return { id: row.id, ok: true, how: 'with its fixture' };
		}
	}
	return {
		id: row.id,
		ok: false,
		how: 'neither',
		was: { x: was.x, y: was.y },
		got: { x: now.x, y: now.y },
		pivotWant,
		candidates: turnedFixtures.map(({ id, from, to }) => ({
			id,
			from: { x: from.x, y: from.y, angle: from.angle },
			to: { x: to.x, y: to.y, angle: to.angle },
			want: rotateAbout(to.x + (was.x - from.x), to.y + (was.y - from.y), to, degrees)
		}))
	};
});
check(
	'every moved row turned the compass way',
	rotateMoved.length > 0 && rideChecks.every((ride) => ride.ok),
	JSON.stringify(rideChecks.filter((ride) => !ride.ok).slice(0, 3))
);
console.log(
	'rotate journal:',
	JSON.stringify(
		(await journalRows()).flatMap((entry) =>
			entry.patches
				.filter((patch) => rotateAdded.has(`${patch.table}:${patch.key}`))
				.map((patch) => `${patch.table}:${patch.key.slice(0, 8)} ${JSON.stringify(patch.values)}`)
		)
	)
);
check(
	'the rows the gesture turned are journaled',
	rotateMoved.every((row) => rotateAdded.has(row.id)),
	JSON.stringify([...rotateAdded].map((key) => key.slice(0, 14)))
);
await sceneIsJournal(moved(beforeRotate, afterRotate), 'rotate');

// 6. scale: one factor for all -------------------------------------------------
await page.getByRole('button', { name: 'scale', exact: true }).click();
const beforeScale = await snapshot();
await drag('middle', moveFrom, { x: moveFrom.x, y: moveFrom.y - 120 }, 8);
const afterScale = await snapshot();
const scaled = group.f
	.map((id) => {
		const was = beforeScale.fixtures.get(id);
		const now = afterScale.fixtures.get(id);
		return was && now ? { id, was: was.scale, now: now.scale } : null;
	})
	.filter(Boolean);
const grew = scaled.filter((row) => row.now !== row.was);
check('the scale gesture resized the fixtures', grew.length > 0);
const factor = grew.length ? grew[0].now / grew[0].was : 0;
check(
	'one factor for the whole selection, rounded per item',
	grew.length > 0 && grew.every((row) => Math.abs(row.now - Math.round(row.was * factor)) <= 1),
	`factor ${factor.toFixed(4)}: ${scaled.map((row) => `${row.was}->${row.now}`).join(' ')}`
);
await sceneIsJournal(moved(beforeScale, afterScale), 'scale');
await page.getByRole('button', { name: 'move', exact: true }).click();

// 7. height: one step for all --------------------------------------------------
const free = await page.evaluate(`(async () => {
	const zone = await fetch('/api/v1/world/zone/' + window.__world.zone, { cache: 'no-store' }).then((r) => r.json());
	return zone.fixtures.find((f) => !f.ground) ?? null;
})()`);
if (free) {
	await page.evaluate(
		`window.__worldViewer.select(Array.from(window.__worldViewer.picks.flatMap((g) => g.fixtures)).find((f) => f.id === ${free.id}))`
	);
	await page.waitForTimeout(300);
	const beforeLift = await snapshot();
	await page.keyboard.down('Alt');
	await drag('middle', moveFrom, { x: moveFrom.x, y: moveFrom.y - 80 }, 8);
	await page.keyboard.up('Alt');
	const afterLift = await snapshot();
	const lifted = moved(beforeLift, afterLift).filter((item) => item.dz !== 0);
	check('the lift raised the free fixture', lifted.length > 0, `${lifted.length} items`);
	const steps = new Set(lifted.map((item) => item.dz));
	check('one height step for all', steps.size <= 1, [...steps].join(' | '));
	check(
		'the step is a 16-unit step',
		[...steps].every((dz) => dz % 16 === 0),
		[...steps].join(' | ')
	);
	await sceneIsJournal(lifted, 'lift');
} else {
	console.log('no free-standing fixture in this zone: the lift check is skipped');
}

// 8. a click never journals ----------------------------------------------------
const quietNow = await journalCount();
await page.mouse.move(moveFrom.x, moveFrom.y);
await page.mouse.down({ button: 'middle' });
await page.mouse.up({ button: 'middle' });
await page.waitForTimeout(500);
const quietClick = await journalCount();
check(
	'a middle click journals nothing',
	quietClick.ops === quietNow.ops && quietClick.patches === quietNow.patches
);

// 8b. the panel's own fields are edits too -------------------------------------
await page.mouse.move(moveFrom.x, moveFrom.y);
await page.mouse.down({ button: 'left' });
await page.mouse.up({ button: 'left' });
await settle();
const beforeType = await snapshot();
const pickedForType = await selectedFixtures();
if (pickedForType.length) {
	const id = pickedForType[0];
	const was = beforeType.fixtures.get(id);
	const field = page.locator('section.context .fields input[type=number]').first();
	console.log(
		'typed check:',
		JSON.stringify({
			id,
			was: was ? { x: was.x, y: was.y } : null,
			field: await field.inputValue().catch((e) => `no field: ${e.message.slice(0, 60)}`),
			contexts: await page.locator('section.context').count()
		})
	);
	await field.click();
	await page.keyboard.press('Control+a');
	await page.keyboard.type(String(Math.round(was.x) + 1024));
	await page.keyboard.press('Tab');
	await settle();
	const afterType = await snapshot();
	const now = afterType.fixtures.get(id);
	const typedMoved = moved(beforeType, afterType).filter((item) => item.id === `f${id}`);
	console.log(
		'typed:',
		JSON.stringify({
			from: Math.round(was.x),
			now: now ? Math.round(now.x) : null,
			moved: typedMoved.length
		})
	);
	check(
		'typing x moves the fixture by the typed amount',
		typedMoved.length === 1 && typedMoved[0].dx === 1024,
		JSON.stringify(typedMoved.map((item) => `${item.dx},${item.dy}`))
	);
	await sceneIsJournal(typedMoved, 'typed x');
}

// 8c. Del removes the selection, and the journal records it --------------------
await page.evaluate('document.activeElement && document.activeElement.blur()');
const doomed = (await selectedFixtures())[0];
await page.keyboard.press('Delete');
await settle();
const afterDelete = await snapshot();
check(
	'Del removes the fixture from the scene',
	doomed !== undefined && !afterDelete.fixtures.has(doomed)
);
const removes = (await journal()).flatMap((zone) =>
	zone.ops.filter((op) => op.action === 'remove').map((op) => op.id)
);
check('the removal is journaled', removes.includes(doomed), JSON.stringify(removes));

// 9. shift+drag pans, right orbits, the wheel zooms ----------------------------
const target = () =>
	page.evaluate('JSON.stringify(window.__worldViewer.target.toArray().map(Math.round))');
const targetBefore = await target();
await page.keyboard.down('Shift');
await drag('left', moveFrom, { x: moveFrom.x + 120, y: moveFrom.y + 60 }, 8);
await page.keyboard.up('Shift');
check('shift+drag pans', targetBefore !== (await target()), `${targetBefore} -> ${await target()}`);

const azimuth = () => page.evaluate('Math.round(window.__worldViewer.orbit.azimuth * 1000) / 1000');
const azimuthBefore = await azimuth();
await drag('right', moveFrom, { x: moveFrom.x + 90, y: moveFrom.y }, 8);
check(
	'right drag orbits',
	azimuthBefore !== (await azimuth()),
	`${azimuthBefore} -> ${await azimuth()}`
);

const distance = () => page.evaluate('Math.round(window.__worldViewer.orbit.distance)');
const distanceBefore = await distance();
await page.mouse.move(moveFrom.x, moveFrom.y);
await page.mouse.wheel(0, -400);
await page.waitForTimeout(400);
check(
	'the wheel zooms',
	distanceBefore !== (await distance()),
	`${distanceBefore} -> ${await distance()}`
);

// 10. the browser's own menu never shows over the scene ------------------------
await page.evaluate(
	`window.addEventListener('contextmenu', (event) => { window.__ctx = event.defaultPrevented; })`
);
await drag('right', moveFrom, { x: moveFrom.x + 40, y: moveFrom.y + 20 }, 4);
check('the scene shuts the context menu', await page.evaluate('window.__ctx'));

// 11. revert: the journal empties ----------------------------------------------
await page.getByRole('button', { name: 'revert' }).click();
await page.waitForFunction(
	'window.__worldEditor().mops === 0 && window.__worldEditor().rows === 0',
	{
		timeout: 60000
	}
);
const done = await state();
check('revert empties the journal', done.mops === 0 && done.rows === 0, JSON.stringify(done));

await page.screenshot({ path: `${outDir}/panel.png`, ...SHOT });
console.log(
	'panel:',
	(await page.locator('section.panel').first().innerText()).replace(/\n+/g, ' | ').slice(0, 300)
);
console.log('console errors:', errors.length ? errors : 'none');
check('no console errors', errors.length === 0, errors.join(' / '));

console.log(failures ? `\n${failures} check(s) FAILED` : '\nall checks passed');
await browser.close();
process.exit(failures ? 1 : 0);
