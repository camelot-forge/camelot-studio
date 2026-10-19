<script lang="ts">
	/**
	 * The launcher's whole page: four facts, two buttons, and what the local process
	 * says. Everything privileged (writing in the client, starting the game) happens
	 * behind `/api/*` — a page in a browser cannot do either, which is why the process
	 * exists at all (`C §D14`).
	 */
	type Shard = { shard?: string; server?: string; version?: number; feed?: string };
	type Launcher = {
		installed: number;
		published: number | null;
		available: boolean;
		error: string | null;
	};
	type LauncherState = {
		client: string;
		client_there: boolean;
		account: string;
		installed: number | null;
		launcher: Launcher;
		shard: Shard;
		problems: string[];
		up_to_date?: boolean;
	};
	type Progress = {
		running: boolean;
		stage: string;
		what: string;
		done: number;
		total: number;
		error: string | null;
		version: number | null;
	};

	let state = $state<LauncherState | null>(null);
	let progress = $state<Progress>({
		running: false,
		stage: '',
		what: '',
		done: 0,
		total: 0,
		error: null,
		version: null
	});
	let account = $state('');
	let password = $state('');
	let client = $state('');
	let busy = $state(false);
	let restarting = $state(false);
	let said = $state('');

	async function call(path: string, body?: unknown) {
		const answer = await fetch(path, {
			method: body === undefined ? 'GET' : 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		const document = await answer.json();
		if (!answer.ok) throw new Error(document.error ?? `${answer.status}`);
		return document;
	}

	function read(next: { state?: LauncherState; progress?: Progress }) {
		if (next.state) {
			state = next.state;
			account = state.account || account;
			client = state.client || client;
		}
		if (next.progress) progress = next.progress;
	}

	async function refresh() {
		read(await call('/api/state'));
	}

	async function start_update() {
		busy = true;
		said = '';
		try {
			read(await call('/api/update', {}));
			while (progress.running) {
				await new Promise((done) => setTimeout(done, 400));
				read(await call('/api/progress'));
			}
		} catch (problem) {
			said = `${problem}`;
		}
		await refresh();
		busy = false;
	}

	async function start_play() {
		busy = true;
		said = '';
		try {
			await call('/api/play', { account, password });
			said = 'the client is starting …';
		} catch (problem) {
			said = `${problem}`;
		}
		busy = false;
	}

	async function update_launcher() {
		busy = true;
		said = '';
		try {
			await call('/api/launcher-update', {});
			restarting = true;
			void wait_for_restart();
		} catch (problem) {
			said = `${problem}`;
		}
		busy = false;
	}

	async function wait_for_restart() {
		// The swap script starts the new launcher with `--no-open`: this page is
		// already open, and reloads when the new process answers.
		const target = state?.launcher?.published ?? 0;
		for (let tries = 0; tries < 60; tries += 1) {
			await new Promise((done) => setTimeout(done, 1000));
			try {
				const answer = await fetch('/api/state', { cache: 'no-store' });
				if (!answer.ok) continue;
				const document = await answer.json();
				if ((document.state?.launcher?.installed ?? 0) >= target) {
					location.reload();
					return;
				}
			} catch {
				// the old process is gone; the new one has not bound the port yet
			}
		}
		restarting = false;
		said = 'the launcher did not come back — start it again';
	}

	async function save() {
		read(await call('/api/settings', { client, account, remember: true }));
	}

	$effect(() => {
		refresh();
	});

	const percent = $derived(
		progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0
	);
	// The walk of a client takes minutes and counts nothing: saying "0%" through it
	// would look stuck, so each stage says its own thing.
	const doing = $derived(
		progress.stage === 'checking'
			? 'checking your client …'
			: progress.stage === 'writing'
				? `${progress.done} / ${progress.total}`
				: ''
	);
</script>

<main>
	<h1>{state?.shard?.shard || 'Camelot Forge'}</h1>
	<p class="where">
		{state?.shard?.server ? state.shard.server : 'no game address yet'} · launcher{state
			?.launcher?.installed
			? ` ${state.launcher.installed}`
			: ''}
	</p>

	{#if state}
		{#if state.problems.length}
			<ul class="problems">
				{#each state.problems as problem (problem)}
					<li>{problem}</li>
				{/each}
			</ul>
		{/if}

		<div class="card">
			<dl class="facts">
				<dt>client</dt>
				<dd>{state.client || '—'}</dd>
				<dt>installed</dt>
				<dd>{state.installed ?? 'nothing yet'}</dd>
				<dt>published</dt>
				<dd>{state.shard.version ?? '—'}</dd>
			</dl>
			<p class="state" class:ok={state.up_to_date}>
				{#if state.up_to_date}
					up to date
				{:else if state.client_there}
					an update is waiting
				{:else}
					choose the game's folder
				{/if}
			</p>
			<div class="buttons">
				<button
					class="primary"
					disabled={busy || progress.running || !state.client_there}
					onclick={start_update}
				>
					{progress.running ? `${percent}%` : 'Update'}
				</button>
				{#if progress.running}
					<div class="bar"><span style={`width:${percent}%`}></span></div>
					<span class="state">{doing}</span>
				{/if}
			</div>
			{#if progress.error}
				<p class="problems">{progress.error}</p>
			{/if}
		</div>

		{#if state.launcher?.available || state.launcher?.error || restarting}
			<div class="card">
				<dl class="facts">
					<dt>launcher</dt>
					<dd>{state.launcher?.installed ?? 0}</dd>
					{#if state.launcher?.published}
						<dt>published</dt>
						<dd>{state.launcher.published}</dd>
					{/if}
				</dl>
				{#if restarting}
					<p class="state ok">restarting to apply version {state.launcher?.published} …</p>
				{:else if state.launcher?.available}
					<p class="state">a launcher update is ready</p>
					<div class="buttons">
						<button
							class="primary"
							disabled={busy || progress.running}
							onclick={update_launcher}
						>
							Restart and update
						</button>
					</div>
				{:else if state.launcher?.error}
					<p class="problems">{state.launcher.error}</p>
				{/if}
			</div>
		{/if}

		<div class="card">
			<div class="row">
				<div>
					<label for="account">account</label>
					<input id="account" bind:value={account} onblur={save} />
				</div>
				<div>
					<label for="password">password</label>
					<input id="password" type="password" bind:value={password} />
				</div>
			</div>
			<div class="buttons">
				<button
					class="primary"
					disabled={busy || !account || !password || !state.shard.server || !state.up_to_date}
					onclick={start_play}
				>
					Play
				</button>
				<button disabled={busy} onclick={save}>Remember</button>
			</div>
			{#if !state.up_to_date}
				<p class="state">an update is waiting — Update is what makes Play possible</p>
			{/if}
			{#if said}<p class="state">{said}</p>{/if}
		</div>

		<div class="card">
			<label for="client">client folder</label>
			<input id="client" bind:value={client} onblur={save} />
		</div>
	{/if}

	<p class="hint">
		The launcher reads your shard's feed, brings this client up to it, and starts the game.
		Nothing else on your machine is touched.
	</p>
</main>
