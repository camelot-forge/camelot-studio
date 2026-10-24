<script lang="ts">
	// The Deploy page (`pipeline.md` §6): one question, one button. Publishing
	// cuts the release, builds the players' feed, signs it, commits the deploy
	// branch and pushes — the verbs `publish` already implements. The details
	// and the settings are folded away: an admin needs to know what is waiting,
	// what publishing does, and whether it worked.
	import { onMount } from 'svelte';
	import AppBar from '$lib/components/AppBar.svelte';
	import ProjectChip from '$lib/components/ProjectChip.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import {
		api,
		type DeployPage,
		type DeployPlan,
		type DeployPublish,
		type DeployTest
	} from '$lib/api/client';
	import { Eye, Package, Play, Send } from '@lucide/svelte';

	let facts = $state<DeployPage | null>(null);
	let publish = $state<DeployPublish | null>(null);
	let testing = $state(false);
	let tested = $state<DeployTest | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);

	let showing = $state(false);
	let plan = $state<DeployPlan | null>(null);
	let planEnv = $state<string | null>(null);
	let planning = $state(false);
	let planError = $state<string | null>(null);

	let shardDraft = $state({ feed_url: '', game_server: '' });

	const pending = $derived(facts ? (facts.pending?.fixtures ?? 0) + (facts.pending?.rows ?? 0) : 0);
	const objects = $derived(facts?.pending?.fixtures ?? 0);
	const rows = $derived(facts?.pending?.rows ?? 0);
	const newest = $derived(facts?.releases[0] ?? null);
	const feedRelease = $derived(facts?.feed.release ?? null);
	const stale = $derived(pending > 0 || (newest !== null && feedRelease !== newest.number));
	//: The release the next Publish carries: a fresh cut when the journal holds
	//: edits, the newest one when it is already cut (Test locally cuts too).
	const next = $derived(pending > 0 ? (newest?.number ?? 0) + 1 : (newest?.number ?? null));
	const localEnv = $derived(facts?.environments.find((item) => item.name === 'local') ?? null);
	const canPublish = $derived(
		!!facts && !facts.read_only && !publish?.running && (stale || (!!newest && !!publish?.error))
	);

	const writes = $derived(plan?.items.filter((item) => item.verdict === 'write') ?? []);
	const drifts = $derived(plan?.items.filter((item) => item.verdict === 'drift') ?? []);
	const byFamily = $derived.by(() => {
		const counted: Record<string, number> = {};
		for (const item of writes) counted[item.family] = (counted[item.family] ?? 0) + 1;
		return Object.entries(counted);
	});

	const lastLine = $derived(
		facts?.log?.find((line) => line.kind === 'deploy' || line.kind === 'promote')
	);

	function say(problem: unknown): void {
		error = problem instanceof Error ? problem.message : String(problem);
	}

	function sleep(ms: number): Promise<void> {
		return new Promise((done) => setTimeout(done, ms));
	}

	async function load(): Promise<void> {
		error = null;
		try {
			facts = await api.deploy();
			publish = facts.publish;
			shardDraft = {
				feed_url: facts.shard.feed_url ?? '',
				game_server: facts.shard.game_server ?? ''
			};
			if (publish.running) {
				publish = await followPublish();
				facts = await api.deploy();
				publish = facts.publish;
			}
			if (showing) await refreshPlan();
		} catch (problem) {
			say(problem);
		}
	}

	async function followPublish(): Promise<DeployPublish> {
		let state = (await api.deployPublishProgress()).progress;
		while (state.running) {
			await sleep(400);
			state = (await api.deployPublishProgress()).progress;
		}
		return state;
	}

	async function publishNow(): Promise<void> {
		busy = true;
		error = null;
		tested = null;
		try {
			publish = (await api.deployPublish()).progress;
			publish = await followPublish();
			if (publish.error) error = publish.error;
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			busy = false;
		}
	}

	async function testLocally(): Promise<void> {
		busy = true;
		testing = true;
		error = null;
		tested = null;
		try {
			tested = await api.deployTest();
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			testing = false;
			busy = false;
		}
	}

	async function toggleDetails(): Promise<void> {
		showing = !showing;
		if (showing) await refreshPlan();
	}

	async function refreshPlan(): Promise<void> {
		plan = null;
		planError = null;
		planEnv = null;
		if (newest === null || localEnv === null) return;
		planning = true;
		try {
			plan = await api.deployPlan({ release: newest.number, env: localEnv.name });
			planEnv = localEnv.name;
		} catch (problem) {
			planError = problem instanceof Error ? problem.message : String(problem);
		} finally {
			planning = false;
		}
	}

	async function saveShard(): Promise<void> {
		busy = true;
		error = null;
		try {
			const saved = await api.deployShard(shardDraft);
			shardDraft = {
				feed_url: saved.feed_url ?? '',
				game_server: saved.game_server ?? ''
			};
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			busy = false;
		}
	}

	async function commitLog(): Promise<void> {
		busy = true;
		error = null;
		try {
			await api.deployCommitLog();
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			busy = false;
		}
	}

	function stage(found: DeployPublish | null): string {
		if (!found) return '';
		if (found.stage === 'preparing') return 'Cutting the release…';
		if (found.stage === 'publishing') return 'Building the update and sending it…';
		if (found.stage === 'done') return 'Published';
		return found.stage;
	}

	/** The dry run's writes, grouped the way a release's record groups them. */
	const writesByFamily = $derived(describe(byFamily));

	/** What a release holds, in the operator's words: the record counts families
	 * of work (`client`, `server`, `world`, `rows`, `dropped`), not files only. */
	const FAMILY_NAMES: Record<string, [string, string]> = {
		client: ['client file', 'client files'],
		server: ['server file', 'server files'],
		world: ['world object', 'world objects'],
		rows: ['database row', 'database rows'],
		dropped: ['dropped edit', 'dropped edits']
	};

	function describe(counts: [string, number][]): string {
		const parts = counts.map(([name, count]) => {
			const [one, many] = FAMILY_NAMES[name] ?? [name, `${name}s`];
			return `${count.toLocaleString()} ${count === 1 ? one : many}`;
		});
		return parts.length ? parts.join(' · ') : 'no changes';
	}

	function families(found: Record<string, number>): string {
		return describe(Object.entries(found));
	}

	function when(stamp: string | null | undefined): string {
		if (!stamp) return '—';
		const date = new Date(stamp);
		if (Number.isNaN(date.getTime())) return stamp;
		const pad = (value: number) => String(value).padStart(2, '0');
		return `${pad(date.getDate())}/${pad(date.getMonth() + 1)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
	}

	function size(bytes: number): string {
		if (!bytes) return '0 B';
		const units = ['B', 'KB', 'MB', 'GB'];
		let value = bytes;
		let unit = 0;
		while (value >= 1024 && unit < units.length - 1) {
			value /= 1024;
			unit++;
		}
		return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
	}

	onMount(load);
</script>

<svelte:head><title>Deploy — Camelot Studio</title></svelte:head>

<div class="shell">
	<AppBar section="deploy" subtitle="deploy">
		{#snippet actions()}
			{#if facts}
				<ProjectChip name={facts.project} {pending} readOnly={facts.read_only} holder="" />
			{/if}
		{/snippet}
	</AppBar>

	<main class="page">
		<header class="head">
			<h1>Deploy</h1>
			<p>
				Your changes reach players only after they are published. Publishing builds the update,
				signs it and sends it to the server.
			</p>
		</header>

		{#if error}
			<p class="problem" role="alert">{error}</p>
		{/if}

		<section class="card hero" aria-label="publish">
			{#if facts === null}
				<p class="big">Reading the project…</p>
			{:else}
				<div class="state">
					{#if pending > 0}
						<p class="big">
							{pending}
							{pending === 1 ? 'change is' : 'changes are'} not published
						</p>
						<p class="sub">
							{objects}
							{objects === 1 ? 'object' : 'objects'} edited
							{#if rows > 0}
								· {rows} database {rows === 1 ? 'row' : 'rows'} to write
							{/if}
						</p>
						{#if newest}
							<p class="sub muted">
								Publishing will cut release {String(next ?? 0).padStart(4, '0')}.
							</p>
						{/if}
					{:else if newest && feedRelease !== newest.number}
						<p class="big">
							Release {String(newest.number).padStart(4, '0')} is ready to publish
						</p>
						<p class="sub">
							Cut on {when(newest.cut_at)} · {families(newest.families)} · not live for players yet
						</p>
					{:else if newest}
						<p class="big">Everything is published</p>
						<p class="sub">
							Release {String(newest.number).padStart(4, '0')} is live for players
							{#if facts.feed.version !== null}· update version {facts.feed.version}{/if}
						</p>
					{:else}
						<p class="big">Nothing to publish yet</p>
						<p class="sub">Edit the world, then come back here to publish your changes.</p>
					{/if}
				</div>

				{#if publish?.running}
					<p class="working" aria-live="polite">
						<span class="dot" aria-hidden="true"></span>
						{stage(publish)}
					</p>
				{:else if publish?.stage === 'done' && publish.release !== null && !stale}
					<p class="done" aria-live="polite">
						Release {String(publish.release).padStart(4, '0')} published. Players get it the next time
						they launch the game.
					</p>
				{:else if publish?.error}
					<p class="problem inline" role="alert">The last publish failed: {publish.error}</p>
				{/if}

				{#if tested}
					<p class="done" aria-live="polite">
						{#if tested.unchanged}
							Release {String(tested.release).padStart(4, '0')} is already installed on this computer.
						{:else}
							Release {String(tested.release).padStart(4, '0')} installed on this computer ({tested.applied}
							{tested.applied === 1 ? 'file' : 'files'}). Start the game to check it.
						{/if}
					</p>
				{/if}

				<div class="acts">
					<button class="btn btn--primary" disabled={busy || !canPublish} onclick={publishNow}>
						<Send size={15} />
						{publish?.running
							? 'Publishing…'
							: publish?.error && !stale
								? 'Try publishing again'
								: 'Publish'}
					</button>
					<button
						class="btn btn--secondary"
						aria-expanded={showing}
						disabled={facts.read_only || (!newest && pending === 0)}
						onclick={toggleDetails}
					>
						<Eye size={15} />
						{showing ? 'Hide details' : 'See details'}
					</button>
					<button
						class="btn btn--secondary"
						disabled={busy || facts.read_only || !localEnv || (pending === 0 && !newest)}
						onclick={testLocally}
					>
						<Play size={15} />
						{testing ? 'Installing…' : 'Test locally'}
					</button>
				</div>

				{#if facts.read_only}
					<p class="note">Another window holds this project: close it to publish or test.</p>
				{:else if pending > 0 && next !== null}
					<p class="note">
						Publishing will create release {String(next).padStart(4, '0')} with
						{#if objects > 0}
							{objects} edited {objects === 1 ? 'object' : 'objects'}
						{/if}
						{#if objects > 0 && rows > 0}and
						{/if}
						{#if rows > 0}
							{rows} database {rows === 1 ? 'row' : 'rows'}
						{/if}.
					</p>
				{:else if newest && feedRelease !== newest.number}
					<p class="note">
						Publishing sends release {String(newest.number).padStart(4, '0')} to the update address.
					</p>
				{/if}

				{#if showing}
					<div class="details">
						<h3>What installing it here would change</h3>
						{#if pending > 0 && newest}
							<p class="note">
								{pending} unpublished
								{pending === 1 ? 'change is' : 'changes are'} not in a release yet. Publish, or Test locally,
								to cut them into a release.
							</p>
						{/if}
						{#if newest === null}
							<p class="note">No release exists yet: there is nothing to inspect.</p>
						{:else if localEnv === null}
							<p class="note">
								This project names no <code>local</code> environment, so there is no test install to inspect.
							</p>
						{:else if planning}
							<p class="note">Reading release {String(newest.number).padStart(4, '0')}…</p>
						{:else if planError}
							<p class="problem inline">{planError}</p>
						{:else if plan}
							<p class="note">
								Release {String(plan.release).padStart(4, '0')} → {planEnv} · read-only, nothing was written.
							</p>
							{#if writes.length === 0 && drifts.length === 0 && plan.refusals.length === 0}
								<p class="line ok">Already installed here: nothing would change.</p>
							{:else}
								<p class="line">Would write {writesByFamily}.</p>
								{#if drifts.length}
									<p class="line warn">
										{drifts.length}
										{drifts.length === 1 ? 'file was' : 'files were'} changed outside the tool since the
										install: publishing would replace them.
									</p>
								{/if}
								{#each plan.refusals as refusal (refusal)}
									<p class="problem inline">{refusal}</p>
								{/each}
							{/if}
							{#if plan.needs.restart}
								<p class="note">The game server restarts when this is installed.</p>
							{:else if plan.needs.reload?.length}
								<p class="note">
									After installing, the server reloads: {plan.needs.reload.join(', ')}.
								</p>
							{/if}
						{/if}
					</div>
				{/if}
			{/if}
		</section>

		{#if facts}
			<div class="grid">
				<section class="card" aria-label="releases">
					<h3>
						Releases
						<span class="card-meta">{facts.releases.length}</span>
					</h3>
					{#if facts.releases.length}
						<ul class="rows">
							{#each facts.releases as item (item.number)}
								<li>
									<span class="num">{String(item.number).padStart(4, '0')}</span>
									<span class="what">{families(item.families)}</span>
									<span class="where" class:live={item.live_in.length > 0}>
										{item.live_in.length
											? `installed on ${item.live_in.join(', ')}`
											: 'not installed'}
									</span>
									<span class="when">{when(item.cut_at)}</span>
								</li>
							{/each}
						</ul>
					{:else}
						<p class="hint">No release yet. Your first Publish will create one.</p>
					{/if}
				</section>

				<details class="card settings">
					<summary>
						<span>Update address and settings</span>
						<span class="card-meta">{facts.shard.feed_url || 'no update address'}</span>
					</summary>
					<div class="fold">
						<label class="field">
							<span>Update address (players' launcher)</span>
							<input
								bind:value={shardDraft.feed_url}
								placeholder="https://feed.example.com/"
								disabled={facts.read_only}
							/>
						</label>
						<label class="field">
							<span>Game address</span>
							<input
								bind:value={shardDraft.game_server}
								placeholder="play.example.com:10300"
								disabled={facts.read_only}
							/>
						</label>
						<button
							class="btn btn--secondary"
							disabled={busy || facts.read_only}
							onclick={saveShard}>Save addresses</button
						>
						<p class="hint">
							They live in <code>project.json</code>, so every machine agrees. Publish again after
							changing them.
						</p>

						<h3>Update feed</h3>
						{#if facts.feed.present}
							<p class="line">
								Version {facts.feed.version ?? '—'} · {facts.feed.files.toLocaleString()} files ·
								{size(facts.feed.bytes)}
								{facts.feed.release
									? ` · release ${String(facts.feed.release).padStart(4, '0')}`
									: ''}
							</p>
							{#if facts.feed.base_url}
								<p class="path">{facts.feed.base_url}</p>
							{/if}
							{#if facts.feed.release && newest && facts.feed.release > newest.number}
								<p class="line warn">
									The feed carries release {facts.feed.release}, newer than the newest release here.
								</p>
							{:else if facts.feed.release === newest?.number}
								<p class="line ok">The feed carries the newest release.</p>
							{:else if facts.feed.release}
								<p class="line warn">
									The feed carries release {facts.feed.release}; the newest here is
									{newest ? String(newest.number).padStart(4, '0') : 'none'}.
								</p>
							{/if}
						{:else}
							<p class="hint">No update feed yet. Publishing builds one.</p>
						{/if}

						<h3>Signature</h3>
						<p class="line" class:warn={!facts.signing.key}>
							{#if facts.signing.key}
								Signed: the launcher refuses an update that this key did not sign.
							{:else}
								Not signed. Name a <code>release_key</code> in <code>machine.json</code>.
							{/if}
						</p>

						<h3>Environments</h3>
						<ul class="rows">
							{#each facts.environments as item (item.name)}
								<li>
									<span class="num-em">{item.name}</span>
									<span class="what">
										{item.live
											? `holds release ${String(item.live.release).padStart(4, '0')}`
											: 'nothing installed'}
									</span>
									<span class="where">{item.problem ?? item.root ?? 'no folder'}</span>
								</li>
							{/each}
						</ul>

						{#if facts.log_dirty}
							<p class="line warn">The deploy history is not saved in git yet.</p>
							<button class="btn btn--secondary" disabled={busy} onclick={commitLog}>
								Save the deploy history
							</button>
						{/if}
					</div>
				</details>
			</div>
		{/if}
	</main>

	<StatusBar>
		{#snippet left()}
			{#if facts}
				<span class="sb-strong">
					{facts.feed.present ? `update version ${facts.feed.version ?? '—'}` : 'no update feed'}
				</span>
				{#if newest}
					<span>· release {String(newest.number).padStart(4, '0')}</span>
				{/if}
			{/if}
		{/snippet}
		{#snippet center()}
			{#if lastLine}
				<span>
					last install: release {String(lastLine.release ?? 0).padStart(4, '0')}
					on {lastLine.env} · {lastLine.wrote?.length ?? 0} file(s) · {when(lastLine.at)}
				</span>
			{:else if facts}
				<span>no install recorded yet</span>
			{/if}
		{/snippet}
		{#snippet right()}
			{#if facts?.shard.game_server}
				<Package size={12} />
				<span>{facts.shard.game_server}</span>
			{/if}
		{/snippet}
	</StatusBar>
</div>

<style>
	.shell {
		display: grid;
		grid-template-rows: var(--h-topbar) 1fr var(--h-statusbar);
		height: 100vh;
	}
	.page {
		overflow-y: auto;
		padding: 28px 32px 48px;
		display: flex;
		flex-direction: column;
		gap: 20px;
		max-width: 1080px;
		width: 100%;
		margin: 0 auto;
		min-height: 0;
	}
	.head h1 {
		font-size: var(--fs-section);
		font-weight: 650;
		letter-spacing: -0.02em;
	}
	.head p {
		margin-top: 4px;
		color: var(--text-3);
		font-size: var(--fs-body);
		max-width: 62ch;
	}
	.problem {
		border: 1px solid var(--danger-border);
		background: var(--danger-tint);
		color: var(--danger-text);
		border-radius: var(--r-md);
		padding: 10px 12px;
		font-size: var(--fs-dense);
	}
	.problem.inline {
		margin-top: 10px;
	}
	.hero {
		padding: 26px 28px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.big {
		font-size: 26px;
		font-weight: 650;
		letter-spacing: -0.02em;
	}
	.sub {
		color: var(--text-3);
		font-size: var(--fs-body);
	}
	.sub.muted {
		color: var(--text-4);
	}
	.acts {
		display: flex;
		flex-wrap: wrap;
		gap: 10px;
		margin-top: 18px;
	}
	.note {
		margin-top: 10px;
		font-size: 12px;
		line-height: 1.4;
		color: var(--text-4);
	}
	.working {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 12px;
		font-size: var(--fs-dense);
		color: var(--accent);
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--accent);
		animation: pulse 1s ease-in-out infinite alternate;
	}
	@keyframes pulse {
		from {
			opacity: 0.3;
		}
		to {
			opacity: 1;
		}
	}
	.done {
		margin-top: 12px;
		font-size: var(--fs-dense);
		color: var(--accent);
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 16px;
	}
	.rows {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 1px;
	}
	.rows li {
		display: flex;
		align-items: baseline;
		gap: 12px;
		padding: 7px 8px;
		border-radius: var(--r-sm);
		font-size: var(--fs-dense);
		color: var(--text-2);
	}
	.rows li:hover {
		background: var(--surface-input);
	}
	.num {
		font-variant-numeric: tabular-nums;
		color: var(--text-3);
		min-width: 34px;
	}
	.num-em {
		min-width: 64px;
		font-weight: 600;
	}
	.what {
		flex: 1;
		min-width: 0;
	}
	.where {
		color: var(--text-4);
		font-size: var(--fs-small);
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.where.live {
		color: var(--accent);
	}
	.when {
		color: var(--text-4);
		font-size: var(--fs-small);
		white-space: nowrap;
	}
	details.settings {
		padding: 0;
	}
	details.settings > summary {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 14px;
		cursor: pointer;
		font-size: 12px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-3);
		list-style: none;
		user-select: none;
	}
	details.settings > summary::-webkit-details-marker {
		display: none;
	}
	details.settings > summary::after {
		content: '▾';
		margin-left: auto;
		color: var(--text-4);
		transition: transform var(--dur) var(--ease);
	}
	details.settings[open] > summary::after {
		transform: rotate(180deg);
	}
	details.settings > summary .card-meta {
		margin-left: 0;
		font-size: 11px;
		font-weight: 400;
		text-transform: none;
		letter-spacing: 0;
		color: var(--text-4);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.fold {
		display: flex;
		flex-direction: column;
		gap: 10px;
		padding: 0 14px 16px;
	}
	.fold > h3 {
		margin-top: 8px;
		font-size: 12px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-3);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 5px;
		font-size: 12px;
		color: var(--text-3);
	}
	.field input {
		width: 100%;
		background: var(--surface-input);
		border: 1px solid var(--border-input);
		border-radius: var(--r-input);
		padding: 9px 12px;
		font-size: 13px;
		color: var(--text);
	}
	.field input:hover {
		border-color: var(--border-hover);
	}
	.field input:focus-visible {
		outline: none;
		border-color: var(--accent);
	}
	.fold .btn {
		align-self: flex-start;
	}
	.details {
		margin-top: 16px;
		padding-top: 14px;
		border-top: 1px solid var(--border);
	}
	.details h3 {
		font-size: 12px;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-3);
		margin-bottom: 8px;
	}
	.line {
		font-size: var(--fs-dense);
		color: var(--text-2);
		margin-top: 4px;
	}
	.line.ok {
		color: var(--accent);
	}
	.line.warn {
		color: var(--warn-text);
	}
	.path {
		font-size: 11.5px;
		color: var(--text-4);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.hint {
		font-size: 12px;
		line-height: 1.4;
		color: var(--text-4);
	}
</style>
