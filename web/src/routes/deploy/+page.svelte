<script lang="ts">
	// The Deploy page (`pipeline.md` §6, draft 5): the releases the project cut,
	// the environments this machine names, the dry run of one release against
	// one environment — the table opened before any push — and the feed the
	// players' launcher reads. Every write goes through `pipeline`, the same
	// verbs the CLI runs: this page is a face on them, never a second
	// implementation.
	import { onMount } from 'svelte';
	import AppBar from '$lib/components/AppBar.svelte';
	import ProjectChip from '$lib/components/ProjectChip.svelte';
	import StatusBar from '$lib/components/StatusBar.svelte';
	import {
		api,
		type DeployOutcome,
		type DeployPage,
		type DeployPlan,
		type DeployVerify
	} from '$lib/api/client';
	import { ArrowUpRight, Eye, Package, Rocket, Scissors, Send, ShieldCheck } from '@lucide/svelte';

	let facts = $state<DeployPage | null>(null);
	let release = $state<number | null>(null);
	let env = $state<string | null>(null);
	let plan = $state<DeployPlan | null>(null);
	let outcome = $state<DeployOutcome | null>(null);
	let verified = $state<DeployVerify | null>(null);
	let reading = $state<string | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);

	const pending = $derived(facts ? (facts.pending?.fixtures ?? 0) + (facts.pending?.rows ?? 0) : 0);
	const chosen = $derived(facts?.releases.find((item) => item.number === release) ?? null);
	const chosenEnv = $derived(facts?.environments.find((item) => item.name === env) ?? null);
	const newest = $derived(facts?.releases[0]?.number ?? null);
	const chunk = $derived(plan?.items.filter((item) => item.verdict !== 'unchanged') ?? []);
	const drifts = $derived(plan?.items.filter((item) => item.verdict === 'drift') ?? []);
	const lastLine = $derived(
		facts?.log?.find((line) => line.kind === 'deploy' || line.kind === 'promote')
	);

	function say(problem: unknown): void {
		error = problem instanceof Error ? problem.message : String(problem);
	}

	async function load(): Promise<void> {
		error = null;
		try {
			facts = await api.deploy();
			if (release === null) release = facts.releases[0]?.number ?? null;
			if (env === null) env = facts.environments[0]?.name ?? null;
			await planOnly();
		} catch (problem) {
			say(problem);
		}
	}

	/** The table, without touching the outcome line: a push refreshes the facts
	 * and the table, and the operator keeps reading what it just wrote. */
	async function planOnly(): Promise<void> {
		plan = null;
		if (release === null || env === null) return;
		try {
			plan = await api.deployPlan({ release, env });
		} catch (problem) {
			say(problem);
		}
	}

	/** The operator asking for it again: the previous outcome no longer stands. */
	async function dry(): Promise<void> {
		outcome = null;
		await planOnly();
	}

	async function select(ask: { release?: number; env?: string }): Promise<void> {
		if (ask.release !== undefined) release = ask.release;
		if (ask.env !== undefined) env = ask.env;
		verified = null;
		await dry();
	}

	async function cut(): Promise<void> {
		busy = true;
		error = null;
		try {
			const record = await api.deployCut();
			outcome = null;
			verified = null;
			release = record.number;
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			busy = false;
		}
	}

	/** Promote asks the other environment to vouch for the release: the bytes
	 * travel from where they are live, never from a rebuild (§D4). */
	async function promote(): Promise<void> {
		if (release === null || env === null || !facts) return;
		const source = facts.environments.find(
			(item) => item.name !== env && item.live?.release === release
		);
		if (!source) {
			say(`no other environment holds release ${release}: deploy it there first`);
			return;
		}
		await push(false, source.name);
	}

	async function push(adopt: boolean, source?: string): Promise<void> {
		if (release === null || env === null) return;
		busy = true;
		error = null;
		try {
			outcome = await api.deployPush({ release, env, adopt, source });
			await load();
		} catch (problem) {
			say(problem);
		} finally {
			busy = false;
		}
	}

	async function readBack(name: string): Promise<void> {
		reading = name;
		verified = null;
		error = null;
		try {
			verified = await api.deployVerify(name);
		} catch (problem) {
			say(problem);
		} finally {
			reading = null;
		}
	}

	function families(found: Record<string, number>): string {
		const parts = Object.entries(found).map(([name, count]) => `${name} ${count}`);
		return parts.length ? parts.join(' · ') : '—';
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

	function envState(name: string): { label: string; tone: string } {
		const found = facts?.environments.find((item) => item.name === name);
		if (found?.problem) return { label: 'no folder', tone: 'muted' };
		if (!found?.live) return { label: 'nothing deployed', tone: 'muted' };
		if (found.live.release === newest) return { label: 'live', tone: 'ok' };
		return { label: `behind (${found.live.release})`, tone: 'warn' };
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

	<div class="body">
		<nav class="rail" aria-label="the pipeline">
			<button
				class="tool"
				title="cut a release (the range since the last one)"
				aria-label="Cut a release"
				disabled={busy}
				onclick={cut}
			>
				<Scissors size={17} />
			</button>
			<button
				class="tool"
				title="dry run: what would be written, and why"
				aria-label="Dry run"
				onclick={dry}
			>
				<Eye size={17} />
			</button>
			<button
				class="tool"
				title={`deploy ${String(release ?? 0).padStart(4, '0')} to ${env ?? 'an environment'}`}
				aria-label="Deploy"
				disabled={busy || !plan || plan.refusals.length > 0 || chunk.length === 0}
				onclick={() => push(false)}
			>
				<Rocket size={17} />
			</button>
			<button
				class="tool"
				title="promote: the same bytes, from the environment that already holds it"
				aria-label="Promote"
				disabled={busy || release === null}
				onclick={promote}
			>
				<ArrowUpRight size={17} />
			</button>
			<button
				class="tool"
				title="read the environment back"
				aria-label="Read it back"
				onclick={() => env && readBack(env)}
			>
				<ShieldCheck size={17} />
			</button>
			<button
				class="tool"
				title="publish the players' update — it lands with P6"
				aria-label="Publish"
				disabled
			>
				<Send size={17} />
			</button>
		</nav>

		<main class="deploy">
			<header class="head">
				<h1>Deploy</h1>
				<p>
					{#if facts}
						{facts.project} · {facts.environments.length} environment(s) ·
						{facts.releases.length} release(s){chosenEnv?.live
							? ` · ${chosenEnv.name} holds ${chosenEnv.live.release}`
							: ''}
					{:else}
						reading the project…
					{/if}
				</p>
			</header>

			{#if error}
				<p class="problem" role="alert">{error}</p>
			{/if}

			<div class="grid">
				<section class="card" aria-label="releases">
					<h3>
						Releases
						<span class="card-meta"
							>{facts?.releases.length ?? 0} cut(s) · newest {newest ?? '—'}</span
						>
					</h3>
					{#if facts && facts.releases.length}
						<ul class="rows">
							{#each facts.releases as item (item.number)}
								<li>
									<button
										class="row"
										class:on={item.number === release}
										aria-pressed={item.number === release}
										onclick={() => select({ release: item.number })}
									>
										<span class="num">{String(item.number).padStart(4, '0')}</span>
										<span class="what">{families(item.families)}</span>
										<span class="where" class:live={item.live_in.length > 0}>
											{item.live_in.length ? item.live_in.join(' · ') : 'not deployed'}
										</span>
										<span class="when">{when(item.cut_at)}</span>
									</button>
								</li>
							{/each}
						</ul>
						<p class="hint">
							A cut is rebuilt from the journal, never stored twice:
							<code>camelot project verify --rebuild {newest ?? 'N'}</code>
						</p>
					{:else}
						<p class="hint">
							No release yet. Cut one with <code>camelot project release</code> once edits are recorded.
						</p>
					{/if}
				</section>

				<section class="card" aria-label="environments">
					<h3>
						Environments
						<span class="card-meta">{facts?.environments.length ?? 0} declared</span>
					</h3>
					<div class="envs">
						{#each facts?.environments ?? [] as item (item.name)}
							<article class="env" class:on={item.name === env}>
								<header>
									<strong>{item.name}</strong>
									<span
										class="pill"
										class:ok={envState(item.name).tone === 'ok'}
										class:warn={envState(item.name).tone === 'warn'}
									>
										{envState(item.name).label}
									</span>
								</header>
								<p class="path">{item.problem ?? item.root ?? 'no folder on this machine'}</p>
								{#if item.live}
									<p class="line">
										release {item.live.release} · since {when(item.live.at)}{item.live.from
											? ` · promoted from ${item.live.from}`
											: ''}
									</p>
								{:else}
									<p class="line muted">nothing was deployed there yet</p>
								{/if}
								<p class="line muted">
									client {item.client?.exists ? 'ok' : 'missing'}
									{#if item.client}· {item.client.path}{/if}
								</p>
								<div class="acts">
									<button class="btn btn--secondary" onclick={() => select({ env: item.name })}>
										Dry run here
									</button>
									<button
										class="btn btn--ghost"
										disabled={reading === item.name}
										onclick={() => readBack(item.name)}
									>
										{reading === item.name ? 'reading…' : 'Read it back'}
									</button>
								</div>
								{#if verified?.env === item.name}
									<p class="line" class:ok={verified.ok}>
										{verified.ok
											? `read back: release ${verified.release ?? '—'} and its client are what the project says`
											: verified.problems.slice(0, 3).join(' · ')}
									</p>
								{/if}
							</article>
						{/each}
					</div>
				</section>

				<section class="card" aria-label="dry run">
					<h3>
						Dry run
						<span class="card-meta">
							{#if plan}{plan.items.length} item(s): {Object.entries(plan.counts)
									.filter(([, count]) => count > 0)
									.map(([verdict, count]) => `${count} ${verdict}`)
									.join(' · ')}{/if}
						</span>
					</h3>
					{#if release !== null && env !== null}
						<p class="select-line">
							<span class="lead">
								<Rocket size={14} />
								{String(release).padStart(4, '0')} → {env}
							</span>
							<span class="muted">{chosenEnv?.root ?? ''}</span>
						</p>
					{/if}

					{#if plan}
						{#each chunk as item (item.family + item.what)}
							<div class="item">
								<span class="verdict" data-v={item.verdict}>{item.verdict}</span>
								<span class="what">{item.what}</span>
								<span class="why">{item.why}</span>
								<span class="bytes">
									{#if item.theirs}theirs {item.theirs.replace('sha256:', '').slice(0, 4)}… ·
									{/if}ours
									{item.ours?.replace('sha256:', '').slice(0, 4) ?? '—'}…
								</span>
							</div>
						{/each}
						{#if chunk.length === 0}
							<p class="hint">
								Every destination already holds this release: the deploy would write nothing.
							</p>
						{/if}
						<ul class="release-lines">
							<li>order {plan.order}</li>
							{#if plan.needs.reload?.length}
								<li>
									after the deploy:
									{plan.needs.reload.map((name) => `/reload ${name}`).join(', ')}
								</li>
							{/if}
							<li>
								{plan.ids.checked} id(s) checked against {plan.env} · {plan.ids.collisions.length}
								already held
							</li>
							<li>
								evidence {plan.evidence.present ? 'is there' : 'is not there yet'} ·
								{plan.evidence.path}
							</li>
						</ul>
						{#each plan.refusals as refusal (refusal)}
							<p class="problem">{refusal}</p>
						{/each}
						<div class="acts end">
							<button class="btn btn--secondary" onclick={dry}>Dry run again</button>
							{#if drifts.length}
								<button class="btn btn--ghost" disabled={busy} onclick={() => push(true)}>
									Adopt what is there now ({drifts.length})
								</button>
							{/if}
							<button
								class="btn btn--primary"
								disabled={busy || plan.refusals.length > 0 || chunk.length === 0}
								onclick={() => push(false)}
							>
								{busy
									? 'deploying…'
									: `Deploy ${String(release ?? 0).padStart(4, '0')} → ${env ?? ''}`}
							</button>
						</div>
					{:else}
						<p class="hint">
							Pick a release and an environment: the table of what would be written appears here,
							and the deploy follows it.
						</p>
					{/if}
					{#if outcome}
						<p class="line" class:ok={outcome.outcome !== 'unchanged'}>
							{outcome.outcome === 'unchanged'
								? `nothing written: ${outcome.plan.env} already holds release ${String(outcome.plan.release).padStart(4, '0')}`
								: `${outcome.wrote.length} file(s) written — read it back from the environment's card`}
						</p>
					{/if}
				</section>

				<section class="card" aria-label="feed">
					<h3>
						Feed
						<span class="card-meta">the players' update</span>
					</h3>
					{#if facts?.feed.present}
						<p class="version">version {facts.feed.version ?? '—'}</p>
						<p class="line">
							{facts.feed.files} file(s) · {size(facts.feed.bytes)}
							{facts.feed.release ? ` · release ${facts.feed.release}` : ''}
						</p>
						<p class="path">{facts.feed.path}</p>
						{#if facts.feed.release && newest && facts.feed.release > newest}
							<p class="line warn">
								the feed carries {facts.feed.release}, newer than the newest release here
							</p>
						{:else if chosenEnv?.live && facts.feed.release && facts.feed.release > chosenEnv.live.release}
							<p class="line warn">
								the feed carries {facts.feed.release}; {chosenEnv.name} holds {chosenEnv.live
									.release}
								— players would fetch content the server does not have yet
							</p>
						{:else}
							<p class="line ok">the feed matches what is live</p>
						{/if}
						<p class="hint">
							the launcher reads <code>version-new.txt</code>, then
							<code>patchlist-new.txt</code> (path, md5, size)
						</p>
					{:else}
						<p class="hint">
							No feed yet. The players' update is built by <code>camelot project publish</code>,
							which lands with P6 — until then the panel shows what the launcher would read.
						</p>
						<p class="path">{facts?.feed.path ?? ''}</p>
					{/if}
				</section>
			</div>
		</main>
	</div>

	<StatusBar>
		{#snippet left()}
			{#if chosenEnv}
				<span class="sb-strong">{chosenEnv.name}</span>
				<span>{chosenEnv.live ? `release ${chosenEnv.live.release}` : 'nothing deployed'}</span>
			{/if}
			{#if chosen}
				<span>· order {chosen.order}</span>
			{/if}
		{/snippet}
		{#snippet center()}
			{#if lastLine}
				<span>
					{lastLine.kind === 'promote' ? 'promoted' : 'deployed'}
					{String(lastLine.release ?? 0).padStart(4, '0')}
					{lastLine.env}
					{lastLine.outcome ?? ''} · {lastLine.wrote?.length ?? 0} file(s) ·
					{when(lastLine.at)}
				</span>
			{:else}
				<span>no attempt recorded yet</span>
			{/if}
		{/snippet}
		{#snippet right()}
			{#if facts}
				<Package size={12} />
				<span>{facts.root}</span>
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
	.body {
		flex: 1;
		min-height: 0;
		display: flex;
	}
	.rail {
		width: 64px;
		flex: 0 0 auto;
		padding: 12px 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 8px;
		background: var(--surface);
		border-right: 1px solid var(--border);
	}
	.tool {
		width: 40px;
		height: 40px;
		display: grid;
		place-items: center;
		border: none;
		border-radius: var(--r-lg);
		background: var(--surface-input);
		color: var(--text-2);
		cursor: pointer;
		transition: color 120ms var(--ease);
	}
	.tool:hover:not(:disabled) {
		color: var(--accent);
	}
	.tool:disabled {
		color: var(--text-4);
		cursor: not-allowed;
	}
	.deploy {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: 24px 32px 32px;
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.head h1 {
		font-size: var(--fs-section);
		font-weight: 600;
		color: var(--text);
	}
	.head p {
		margin-top: 6px;
		font-size: var(--fs-dense);
		color: var(--text-3);
	}
	.problem {
		border: 1px solid var(--danger);
		border-radius: var(--r-md);
		padding: 8px 12px;
		font-size: var(--fs-dense);
		color: var(--danger);
	}
	.grid {
		display: grid;
		grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
		gap: 20px;
		align-items: start;
	}
	@media (max-width: 1100px) {
		.grid {
			grid-template-columns: minmax(0, 1fr);
		}
	}
	.rows {
		display: flex;
		flex-direction: column;
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.row {
		display: grid;
		grid-template-columns: 56px minmax(0, 1fr) 180px 96px;
		align-items: center;
		gap: 12px;
		width: 100%;
		padding: 9px 10px;
		border: 1px solid transparent;
		border-radius: var(--r-md);
		background: transparent;
		font: inherit;
		text-align: left;
		color: var(--text-2);
		cursor: pointer;
	}
	.row:hover {
		background: var(--surface-2);
	}
	.row.on {
		background: var(--surface-2);
		border-color: var(--border-input);
	}
	.num {
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		color: var(--text);
	}
	.what {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.where {
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.where.live {
		color: var(--text-3);
	}
	.where.live::before {
		content: '●';
		margin-right: 6px;
		color: var(--accent);
		font-size: 8px;
	}
	.when {
		font-size: var(--fs-small);
		text-align: right;
		color: var(--text-4);
		font-variant-numeric: tabular-nums;
	}
	.envs {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.env {
		border: 1px solid var(--border);
		border-radius: var(--r-lg);
		padding: 12px 14px;
		background: var(--surface-2);
	}
	.env.on {
		border-color: var(--border-input);
	}
	.env header {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.env strong {
		font-size: var(--fs-title);
		font-weight: 600;
		color: var(--text);
	}
	.pill {
		margin-left: auto;
		padding: 2px 8px;
		border-radius: var(--r-sm);
		background: var(--surface);
		font-size: var(--fs-label);
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-4);
	}
	.pill.ok {
		background: var(--accent-tint-14);
		color: var(--accent);
	}
	.pill.warn {
		background: var(--warn-tint);
		color: var(--warn-text);
	}
	.path,
	.line {
		margin-top: 6px;
		font-size: var(--fs-small);
		color: var(--text-3);
		overflow-wrap: anywhere;
	}
	.path {
		color: var(--text-4);
	}
	.line.muted,
	.muted {
		color: var(--text-4);
	}
	.line.ok {
		color: var(--accent);
	}
	.line.warn {
		color: var(--warn-text);
	}
	.acts {
		display: flex;
		gap: 8px;
		margin-top: 10px;
	}
	.acts.end {
		justify-content: flex-end;
		margin-top: 14px;
	}
	.select-line {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 10px;
		font-size: var(--fs-dense);
		color: var(--text-2);
	}
	.select-line .lead {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		font-variant-numeric: tabular-nums;
	}
	.item {
		display: grid;
		grid-template-columns: 92px minmax(0, 1fr);
		gap: 4px 12px;
		padding: 8px 0;
		border-top: 1px solid var(--border);
	}
	.verdict {
		font-size: var(--fs-label);
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--accent);
	}
	.verdict[data-v='unchanged'] {
		color: var(--text-4);
	}
	.verdict[data-v='drift'] {
		color: var(--warn-text);
	}
	.verdict[data-v='refused'] {
		color: var(--danger);
	}
	.item .what {
		font-size: var(--fs-dense);
		color: var(--text-2);
		overflow-wrap: anywhere;
	}
	.item .why {
		grid-column: 2;
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.item .bytes {
		grid-column: 2;
		font-size: var(--fs-small);
		color: var(--text-4);
		font-variant-numeric: tabular-nums;
	}
	.release-lines {
		margin: 12px 0 0;
		padding: 10px 12px;
		list-style: none;
		border-radius: var(--r-md);
		background: var(--surface-2);
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.version {
		font-size: 26px;
		font-weight: 600;
		color: var(--text);
	}
	code {
		font-family: inherit;
		color: var(--text-3);
		background: var(--surface-2);
		border-radius: var(--r-xs);
		padding: 1px 4px;
	}
</style>
