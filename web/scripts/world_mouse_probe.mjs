// The mouse grammar, driven like a hand: click to select, a box to select
// several, middle to move, shift+middle to rotate, alt+middle to lift — then
// the panel's publish state. Throwaway harness; nothing is published.
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8762';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
const zoneName = process.argv[4] ?? 'Camelot Hills';
mkdirSync(outDir, { recursive: true });
const SHOT = { timeout: 120000 };

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

await page.goto(`${base}/world`, { waitUntil: 'load' });
await page.waitForFunction('document.querySelectorAll("aside select option").length > 0', {
	timeout: 120000
});
await page.fill('input[type=search]', zoneName);
await page.locator('.zones button', { hasText: zoneName }).first().click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 180000 });
await page.waitForTimeout(4000);
await page.evaluate('(async () => await fetch("/api/v1/world/mod/revert", { method: "POST" }))()');

const box = await page.locator('canvas').boundingBox();
const middle = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
const pine = await page.evaluate(`(async () => {
	const zone = await fetch('/api/v1/world/zone/' + window.__world.zone, { cache: 'no-store' }).then((r) => r.json());
	const hit = zone.fixtures.find((f) => f.name.toLowerCase().includes('pine'));
	window.__worldFocus(hit.x, hit.y, 2200);
	return hit.id;
})()`);
console.log('standing over pine', pine);
await page.waitForTimeout(1200);

// 1. left click = selection (and nothing else)
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'left' });
await page.waitForTimeout(120);
await page.mouse.up({ button: 'left' });
await page.waitForTimeout(500);
console.log('click selects:', JSON.stringify((await state()).selection));

// 2. a left drag that starts *on the selection* grabs it (the middle button may
//    not exist); a left drag anywhere else is still a box
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'left' });
await page.mouse.move(middle.x + 100, middle.y, { steps: 8 });
await page.mouse.up({ button: 'left' });
await page.waitForTimeout(900);
console.log('left grab op:', JSON.stringify((await journal())[0]?.ops?.[0]?.values));

// 3. middle drag = the same gesture, for mice that have one
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'middle' });
await page.mouse.move(middle.x + 120, middle.y + 40, { steps: 8 });
await page.mouse.up({ button: 'middle' });
await page.waitForTimeout(900);
console.log('middle move op:', JSON.stringify((await journal())[0]?.ops?.[1]?.values));

// 4. the scene never shows the browser's own menu
await page.evaluate(
	`window.addEventListener('contextmenu', (event) => { window.__ctx = event.defaultPrevented; })`
);
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'right' });
await page.mouse.move(middle.x + 40, middle.y + 20, { steps: 4 });
await page.mouse.up({ button: 'right' });
await page.waitForTimeout(400);
console.log('context menu prevented:', await page.evaluate('window.__ctx'));

// 5. shift + middle = rotate (15° steps)
await page.keyboard.down('Shift');
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'middle' });
await page.mouse.move(middle.x, middle.y + 120, { steps: 8 });
await page.mouse.up({ button: 'middle' });
await page.keyboard.up('Shift');
await page.waitForTimeout(900);
console.log('rotate op:', JSON.stringify((await journal())[0]?.ops?.[2]?.values));

// 4. alt + middle = lift (only a free-standing row has a z to lift: the pine is
//    a ground row, so nothing may change — W-3)
await page.keyboard.down('Alt');
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'middle' });
await page.mouse.move(middle.x, middle.y - 80, { steps: 6 });
await page.mouse.up({ button: 'middle' });
await page.keyboard.up('Alt');
await page.waitForTimeout(700);
const lifted = await state();
console.log('after alt+middle: ops', lifted.mops, '(a ground row takes no z — W-3)');

// 5. ctrl + middle = scale
await page.keyboard.down('Control');
await page.mouse.move(middle.x, middle.y);
await page.mouse.down({ button: 'middle' });
await page.mouse.move(middle.x, middle.y + 100, { steps: 6 });
await page.mouse.up({ button: 'middle' });
await page.keyboard.up('Control');
await page.waitForTimeout(900);
console.log('scale op:', JSON.stringify((await journal())[0]?.ops?.[3]?.values));

// 6. a box over a corner of the scene = several fixtures
await page.mouse.move(box.x + 40, box.y + 40);
await page.mouse.down({ button: 'left' });
await page.mouse.move(box.x + box.width - 40, box.y + box.height - 40, { steps: 12 });
await page.mouse.up({ button: 'left' });
await page.waitForTimeout(800);
const boxed = await state();
console.log(
	'box selects:',
	boxed.selection.fixtures.length,
	'fixtures,',
	boxed.selection.rows.length,
	'rows'
);

// 7. the panel's own state, and the wheel still zooms
await page.mouse.move(middle.x, middle.y);
const before = await page.evaluate('Math.round(window.__worldViewer.orbit.distance)');
await page.mouse.wheel(0, -400);
await page.waitForTimeout(400);
const after = await page.evaluate('Math.round(window.__worldViewer.orbit.distance)');
console.log('wheel zoom:', before, '->', after);
await page.screenshot({ path: `${outDir}/panel.png`, ...SHOT });
console.log(
	'panel:',
	(await page.locator('section.panel').first().innerText()).replace(/\n+/g, ' | ').slice(0, 300)
);

// 8. tidy: the journal goes; nothing was published anywhere
const ops = await journal();
console.log(
	'ops:',
	JSON.stringify(
		ops.flatMap((zone) =>
			zone.ops.map((op) => `${op.action}#${op.id} ${JSON.stringify(op.values)}`)
		)
	)
);
await page.getByRole('button', { name: 'revert' }).click();
await page.waitForFunction(
	'window.__worldEditor().mops === 0 && window.__worldEditor().rows === 0',
	{
		timeout: 60000
	}
);
const done = await state();
console.log(
	'after revert:',
	JSON.stringify({ ops: done.mops, rows: done.rows, dirty: done.dirty })
);
console.log(
	'after revert:',
	JSON.stringify({ ops: done.mops, rows: done.rows, dirty: done.dirty })
);
console.log('console errors:', errors.length ? errors : 'none');
await browser.close();
