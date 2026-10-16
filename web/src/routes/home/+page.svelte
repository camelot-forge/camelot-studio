<script lang="ts">
	import { onMount } from 'svelte';
	import { FolderOpen, HardDrive } from '@lucide/svelte';
	import { api, type HomeHealth, type HomePayload, type HomeRecent } from '$lib/api/client';
	import AppBar from '$lib/components/AppBar.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import { healthLabel, since } from '$lib/home';

	let payload = $state<HomePayload | null>(null);
	let error = $state<string | null>(null);
	let checkPath = $state('');
	let checked = $state<HomeHealth | null>(null);
	let checking = $state(false);
	/** Per row, filled one at a time: the page paints first, the health
	 * follows (`home.md` §D2 — never a `verify` in the first paint). */
	let health = $state<Record<string, HomeHealth>>({});

	async function checkRows(rows: HomeRecent[]) {
		for (const row of rows) {
			if (!row.exists || !row.project) continue;
			try {
				health[row.path] = await api.homeHealth(row.path);
			} catch {
				// the row says what it can; a failed check is not a page error
			}
		}
	}

	async function load() {
		try {
			payload = await api.home();
			void checkRows(payload.recents);
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		}
	}

	async function check() {
		checking = true;
		checked = null;
		try {
			checked = await api.homeHealth(checkPath);
		} catch (problem) {
			checked = {
				path: checkPath,
				name: '',
				state: 'refused',
				detail: problem instanceof Error ? problem.message : String(problem),
				findings: []
			};
		} finally {
			checking = false;
		}
	}

	onMount(load);
</script>

<div class="shell">
	<AppBar section="home" subtitle={payload?.project ? payload.project.name : 'no project open'}>
		{#snippet actions()}
			{#if payload}
				<div class="chip" title="the client this daemon reads">
					<HardDrive size={15} />
					<span class="client">{payload.daemon.client.split('/').filter(Boolean).pop()}</span>
					<span class="zones">{payload.daemon.zones.toLocaleString()} zones</span>
				</div>
			{/if}
		{/snippet}
	</AppBar>

	<main>
		<h1>Your projects.</h1>
		<p class="sub">This machine's projects.</p>

		<form
			class="opener"
			onsubmit={(event) => {
				event.preventDefault();
				void check();
			}}
		>
			<div class="field">
				<FolderOpen size={18} />
				<input
					class="input"
					type="text"
					placeholder="Path to a project folder"
					bind:value={checkPath}
				/>
				<button class="btn btn--primary" type="submit" disabled={!checkPath || checking}>
					{checking ? 'Checking…' : 'Check'}
				</button>
			</div>
			<p class="hint">
				{#if checked}
					{checked.name ? `${checked.name}: ` : ''}{checked.detail}
				{:else}
					Checks the folder, and the client it recorded.
				{/if}
			</p>
		</form>

		{#if error}
			<p class="error">{error}</p>
		{:else if payload}
			<div class="section-head">
				<span class="label">RECENT</span>
				<span class="count">{payload.recents.length} on this machine</span>
			</div>
			{#if payload.recent_error}
				<p class="error">{payload.recent_error}</p>
			{/if}

			{#if payload.recents.length === 0}
				<div class="empty">
					<div class="tile"><FolderOpen size={20} /></div>
					<div>
						<h2>No projects on this machine yet</h2>
						<p>A project shows up here the first time a daemon serves it.</p>
					</div>
				</div>
			{:else}
				<ul class="rows">
					{#each payload.recents as row (row.path)}
						{@const found = healthLabel(health[row.path])}
						<li class="row" class:missing={!row.exists}>
							<div class="tile"><FolderOpen size={20} /></div>
							<div class="who">
								<span class="name">{row.name}</span>
								<span class="path" title={row.path}>{row.path}</span>
							</div>
							<div class="chips">
								{#if !row.exists}
									<span class="chip warn"><span class="dot"></span>folder missing</span>
								{:else if !row.project}
									<span class="chip"><span class="dot none"></span>not a project</span>
								{:else if found}
									<span class="chip" class:warn={found.tone === 'warn'}>
										<span class="dot"></span>{found.text}
									</span>
								{/if}
							</div>
							<span class="when" title={row.opened}>{since(row.opened)}</span>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</main>

	<StatusBar>
		{#snippet left()}
			<span class="ok"><span class="dot"></span>daemon ok</span>
			<span>port {payload?.daemon.port ?? '…'}</span>
		{/snippet}
		{#snippet center()}
			{#if payload}
				<span>{payload.daemon.client} · {payload.daemon.zones.toLocaleString()} zones</span>
			{/if}
		{/snippet}
		{#snippet right()}
			{#if payload}
				<span>camelot {payload.daemon.version} · {payload.daemon.build}</span>
			{/if}
		{/snippet}
	</StatusBar>
</div>

<style>
	.shell {
		display: flex;
		flex-direction: column;
		height: 100dvh;
	}
	main {
		flex: 1;
		overflow-y: auto;
		width: min(1280px, 100% - 48px);
		margin: 0 auto;
		padding: 48px 0 24px;
	}
	h1 {
		font-size: var(--fs-section);
		font-weight: 600;
	}
	.sub {
		margin-top: 6px;
		color: var(--text-3);
	}
	.opener {
		margin-top: 28px;
	}
	.field {
		display: flex;
		align-items: center;
		gap: 10px;
		height: 52px;
		padding: 0 8px 0 16px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-lg);
		background: var(--surface-input);
		color: var(--text-4);
	}
	.field .input {
		flex: 1;
		border: 0;
		background: none;
		height: 100%;
	}
	.hint,
	.count,
	.when {
		color: var(--text-4);
	}
	.hint {
		margin-top: 10px;
		font-size: var(--fs-small);
	}
	.error {
		margin-top: 12px;
		color: var(--danger-text);
		font-size: var(--fs-small);
	}
	.section-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		margin: 36px 0 10px;
		padding-bottom: 10px;
		border-bottom: 1px solid var(--border);
	}
	.label {
		font-size: var(--fs-label);
		font-weight: 600;
		letter-spacing: 0.06em;
		color: var(--text-4);
	}
	.count {
		font-size: var(--fs-small);
	}
	.rows {
		display: flex;
		flex-direction: column;
		gap: 8px;
		list-style: none;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 16px;
		height: 80px;
		padding: 0 16px;
		border: 1px solid var(--border);
		border-radius: var(--r-lg);
		background: var(--surface-dim);
	}
	.row:hover {
		border-color: var(--border-input);
		background: var(--surface);
	}
	.row.missing {
		opacity: 0.7;
	}
	.tile {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 40px;
		height: 40px;
		border-radius: var(--r-lg);
		background: var(--surface-input);
		color: var(--text-2);
		flex: 0 0 auto;
	}
	.who {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
		flex: 1;
	}
	.name {
		font-weight: 600;
	}
	.path {
		font-size: var(--fs-small);
		color: var(--text-3);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chips {
		display: flex;
		gap: 8px;
		flex: 0 0 auto;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 12px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-input);
		font-size: var(--fs-dense);
		color: var(--text-2);
		white-space: nowrap;
		max-width: 420px;
	}
	.chip .zones {
		color: var(--text-4);
	}
	.chip .client {
		font-weight: 500;
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
		flex: 0 0 auto;
	}
	.dot.none {
		background: var(--text-4);
	}
	.chip.warn {
		color: var(--warn-text);
	}
	.chip.warn .dot {
		background: var(--warn);
	}
	.when {
		font-size: var(--fs-small);
		flex: 0 0 auto;
	}
	.empty {
		display: flex;
		align-items: center;
		gap: 16px;
		height: 120px;
		padding: 0 24px;
		border: 1px dashed var(--border-input);
		border-radius: var(--r-lg);
		background: var(--surface-dim);
	}
	.empty h2 {
		font-size: var(--fs-title);
		font-weight: 600;
	}
	.empty p {
		margin-top: 4px;
		color: var(--text-3);
		font-size: var(--fs-dense);
	}
	.ok {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
</style>
