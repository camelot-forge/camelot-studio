<script lang="ts">
	/**
	 * Home: what this daemon serves, the three
	 * ways in (open, create, import — plus clone), and the machine's projects
	 * with the answer to *can I work here*.
	 *
	 * The doors write inside a project's folder and nowhere else. A switch
	 * warns about what it leaves behind; it never refuses, because the journal
	 * *is* the save (§D3.1).
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { FolderOpen, GitBranch, HardDrive, LoaderCircle, Plus } from '@lucide/svelte';
	import {
		api,
		type HomeDoor,
		type HomeHealth,
		type HomePayload,
		type HomeRebase,
		type HomeRecent
	} from '$lib/api/client';
	import AppBar from '$lib/components/AppBar.svelte';
	import ProjectModal from '$lib/components/ProjectModal.svelte';
	import RebasePanel from '$lib/components/RebasePanel.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import { healthLabel, leftNotice, publishLabel, since } from '$lib/home';

	let payload = $state<HomePayload | null>(null);
	let error = $state<string | null>(null);
	/** What a door or a switch answered: one line, dismissed on the next act. */
	let notice = $state<{ tone: 'info' | 'warn'; text: string } | null>(null);
	let opener = $state('');
	/** The door's modal, when one is open. */
	let door = $state<'new' | 'import' | 'clone' | null>(null);
	/** The re-base panel's row, when one is open (`project.md` §D17). */
	let rebasing = $state<{ path: string; name: string } | null>(null);
	let cloneUrl = $state('');
	let busy = $state(false);
	/** Per row, filled one at a time: the page paints first, the health
	 * follows (`home.md` §D2 — never a `verify` in the first paint). */
	let health = $state<Record<string, HomeHealth>>({});

	const looksLikeUrl = (value: string) =>
		/^(https?:\/\/|git@|ssh:\/\/|file:\/\/)/.test(value.trim());

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

	/** D3's switch, from the opener or a row: the daemon serves another
	 * project, and the answer says what the one it left still holds. */
	async function open(path: string) {
		busy = true;
		notice = null;
		try {
			const answer = await api.homeOpen(path);
			show(answer);
			error = null;
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		} finally {
			busy = false;
			await load();
		}
	}

	function show(answer: HomeDoor) {
		const left = leftNotice(answer.left);
		if (answer.warning) notice = { tone: 'warn', text: answer.warning };
		else if (left) notice = { tone: 'info', text: `${left}.` };
	}

	async function fromOpener(event: Event) {
		event.preventDefault();
		const value = opener.trim();
		if (!value) return;
		if (looksLikeUrl(value)) {
			cloneUrl = value;
			door = 'clone';
			return;
		}
		await open(value);
	}

	/** A door ran (create / import / clone): say what happened, and go to the
	 * World editor when the modal asked for it and the daemon switched. */
	async function done(answer: HomeDoor, openIt: boolean) {
		door = null;
		cloneUrl = '';
		if (answer.warning && !answer.opened) {
			notice = { tone: 'warn', text: answer.warning };
			await load();
			return;
		}
		show(answer);
		await load();
		if (openIt && answer.opened) await goto(resolve('/world'));
	}

	/** The re-base panel closed: say which decision ran, and read the row back
	 * (the health chip has to stop saying what it said before). */
	async function rebaseDone(found: HomeRebase | null) {
		rebasing = null;
		if (!found) return;
		notice = {
			tone: found.conflicts > 0 ? 'warn' : 'info',
			text:
				found.decision === 'keep-ours'
					? `kept ours: ${found.written} resource(s) pinned — every deploy will say so`
					: `adopted theirs: ${found.written} resource(s) moved, ` +
						`${found.conflicts} op(s) no longer fit the base`
		};
		await load();
	}

	onMount(load);
</script>

<div class="shell">
	<AppBar section="home" subtitle={payload?.project ? payload.project.name : 'no project open'}>
		{#snippet actions()}
			{#if payload}
				{#if payload.daemon.client}
					<div class="chip" title="the client this daemon reads">
						<HardDrive size={15} />
						<span class="client">{payload.daemon.client.split('/').filter(Boolean).pop()}</span>
						<span class="zones">{payload.daemon.zones.toLocaleString()} zones</span>
					</div>
				{/if}
			{/if}
		{/snippet}
	</AppBar>

	<main>
		<h1>Your projects.</h1>
		<p class="sub">Open a project, or create one from a DAoC client.</p>

		<form class="opener" onsubmit={fromOpener}>
			<div class="field">
				<FolderOpen size={18} />
				<input
					class="input"
					type="text"
					placeholder="Path to a project folder, or a git URL"
					bind:value={opener}
				/>
				<button class="btn btn--primary" type="submit" disabled={!opener.trim() || busy}>
					{#if busy}<span class="spin"><LoaderCircle size={14} /></span>{/if}
					Open
				</button>
			</div>
			{#if notice}
				<p class="notice" class:warn={notice.tone === 'warn'}>{notice.text}</p>
			{:else if error}
				<p class="error">{error}</p>
			{:else}
				<p class="hint">The project's client is checked when it opens.</p>
			{/if}
		</form>

		<div class="doors">
			<button class="door" type="button" onclick={() => (door = 'new')}>
				<span class="tile"><Plus size={18} /></span>
				<span class="what">
					<span class="name">New project</span>
					<span class="line">Create a project folder and point it at a DAoC client.</span>
				</span>
			</button>
			<button class="door" type="button" onclick={() => (door = 'import')}>
				<span class="tile"><FolderOpen size={18} /></span>
				<span class="what">
					<span class="name">Import a folder</span>
					<span class="line">An existing edits folder becomes a project, without moving it.</span>
				</span>
			</button>
			<button class="door" type="button" onclick={() => (door = 'clone')}>
				<span class="tile"><GitBranch size={18} /></span>
				<span class="what">
					<span class="name">Clone a repository</span>
					<span class="line">Clone a project from a git repository.</span>
				</span>
			</button>
		</div>

		{#if payload}
			{#if payload.recent_error}
				<p class="error">{payload.recent_error}</p>
			{/if}
			<div class="section-head">
				<span class="label">RECENT</span>
				<span class="count">{payload.recents.length} on this machine</span>
			</div>

			{#if payload.recents.length === 0}
				<div class="empty">
					<div class="tile"><FolderOpen size={20} /></div>
					<div>
						<h2>No projects on this machine yet</h2>
						<p>Open a folder, or create a project — it will show up here.</p>
					</div>
				</div>
			{:else}
				<ul class="rows">
					{#each payload.recents as row (row.path)}
						{@const found = healthLabel(health[row.path])}
						{@const pending = health[row.path]
							? (health[row.path].pending?.fixtures ?? 0) + (health[row.path].pending?.rows ?? 0)
							: 0}
						<li class="row" class:missing={!row.exists}>
							<button
								class="open-row"
								type="button"
								disabled={!row.exists || !row.project || busy}
								title={row.exists && row.project ? `open ${row.name}` : 'not a project yet'}
								onclick={() => open(row.path)}
							>
								<span class="tile"><FolderOpen size={20} /></span>
								<span class="who">
									<span class="name">{row.name}</span>
									<span class="path" title={row.path}>{row.path}</span>
								</span>
							</button>
							<div class="chips">
								{#if !row.exists}
									<span class="chip warn"><span class="dot"></span>folder missing</span>
								{:else if !row.project}
									<span class="chip"><span class="dot none"></span>not a project</span>
								{:else if found}
									<span class="chip" class:warn={found.tone === 'warn'}>
										<span class="dot"></span>{found.text}
									</span>
									<span class="chip" class:warn={pending > 0}>{publishLabel(pending)}</span>
									{#if found.tone === 'warn' && health[row.path]?.state !== 'no-client'}
										<button
											class="chip action"
											type="button"
											title="the client moved under this project — see what it touches"
											onclick={() => (rebasing = { path: row.path, name: row.name })}
										>
											re-base…
										</button>
									{/if}
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
				<span
					>{payload.daemon.client ?? 'no client yet'} · {payload.daemon.zones.toLocaleString()} zones</span
				>
			{/if}
		{/snippet}
		{#snippet right()}
			{#if payload}
				<span>camelot {payload.daemon.version} · {payload.daemon.build}</span>
			{/if}
		{/snippet}
	</StatusBar>
</div>

{#if door && payload}
	<ProjectModal
		kind={door}
		defaultClient={payload.daemon.client ?? ''}
		defaultUrl={cloneUrl}
		onclose={() => {
			door = null;
			cloneUrl = '';
		}}
		ondone={done}
	/>
{/if}

{#if rebasing}
	<RebasePanel
		path={rebasing.path}
		name={rebasing.name}
		onclose={() => (rebasing = null)}
		ondone={rebaseDone}
	/>
{/if}

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
	.notice {
		margin-top: 10px;
		font-size: var(--fs-small);
		color: var(--text-3);
	}
	.notice.warn {
		color: var(--warn-text);
	}
	.error {
		margin-top: 12px;
		color: var(--danger-text);
		font-size: var(--fs-small);
	}
	.doors {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 12px;
		margin-top: 20px;
	}
	.door {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		padding: 16px;
		border: 1px solid var(--border);
		border-radius: var(--r-lg);
		background: var(--surface-dim);
		text-align: left;
	}
	.door:hover {
		border-color: var(--border-input);
		background: var(--surface);
	}
	.door .what {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}
	.door .name {
		font-weight: 600;
	}
	.door .line {
		font-size: var(--fs-dense);
		color: var(--text-3);
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
	.open-row {
		display: flex;
		align-items: center;
		gap: 16px;
		flex: 1;
		min-width: 0;
		height: 100%;
		border: 0;
		background: none;
		text-align: left;
		color: inherit;
	}
	.open-row:disabled {
		cursor: default;
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
	/* The re-base door (`project.md` §D17): the same chip, and it is a button. */
	.chip.action {
		cursor: pointer;
		color: var(--text-3);
	}
	.chip.action:hover {
		border-color: var(--border-hover);
		color: var(--text);
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
	.spin {
		animation: turn 1s linear infinite;
	}
	@keyframes turn {
		to {
			transform: rotate(360deg);
		}
	}
</style>
