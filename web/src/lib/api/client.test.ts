import { describe, expect, it } from 'vitest';
import { readJson } from './client';

const answer = (body: string, type: string, status = 200) =>
	new Response(body, { status, headers: { 'content-type': type } });

describe('readJson', () => {
	it('reads a JSON answer', async () => {
		const res = answer('{"ok":1}', 'application/json');
		expect(await readJson<{ ok: number }>(res)).toEqual({ ok: 1 });
	});

	it('says when the answer is a page instead of JSON, and which URL it was', async () => {
		// what an older daemon (or a dev server without its proxy) answers:
		// its own index.html, 200 — the browser's own parse error said nothing
		// about why (2026-10-17). The URL is the fact that says which port the
		// page is really talking to.
		const res = answer('<!doctype html><html></html>', 'text/html; charset=utf-8');
		Object.defineProperty(res, 'url', { value: 'http://127.0.0.1:8760/api/v1/home' });
		await expect(readJson(res)).rejects.toThrow(
			'the answer from http://127.0.0.1:8760/api/v1/home is not JSON (text/html; charset=utf-8)'
		);
		await expect(readJson(res)).rejects.toThrow(/is this the daemon's own port\?/);
	});

	it('and says so even when the answer carries no URL of its own', async () => {
		const res = answer('<!doctype html>', 'text/html');
		await expect(readJson(res)).rejects.toThrow(/the answer from the daemon is not JSON/);
	});

	it('keeps the daemon’s own refusal when the call failed', async () => {
		const res = answer(
			'{"detail":"not a project: no project.json in /x"}',
			'application/json',
			400
		);
		await expect(readJson(res)).rejects.toThrow('not a project: no project.json in /x');
	});

	it('and the status line when the refusal is not JSON either', async () => {
		const res = answer('boom', 'text/plain', 500);
		await expect(readJson(res)).rejects.toThrow('500');
	});
});
