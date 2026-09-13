<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Database, FileBox, RefreshCw, Search, WifiOff } from '@lucide/svelte';
	import { api, type AssetSummary, type Health, type RootInfo } from '$lib/api/client';

	interface Props {
		onselect: (asset: AssetSummary) => void;
		selectedId?: string | null;
	}
	let { onselect, selectedId = null }: Props = $props();

	type Status = 'connecting' | 'online' | 'offline';
	let status = $state<Status>('connecting');
	let info = $state<RootInfo | null>(null);
	let health = $state<Health | null>(null);
	let items = $state<AssetSummary[]>([]);
	let total = $state(0);
	let page = $state(1);
	let limit = 100;
	let type = $state('nif');
	let folder = $state('');
	let showOther = $state(false);
	let q = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);

	let events: EventSource | null = null;

	// `other` (unrecognised archive entries) is noise: hidden unless asked for.
	const types = $derived(
		info
			? Object.keys(info.counts)
					.filter((t) => t !== 'other' || showOther)
					.sort()
			: []
	);
	const dirs = $derived(info ? Object.entries(info.dirs) : []);

	async function refresh() {
		loading = true;
		error = null;
		try {
			const res = await api.assets({ type, dir: folder, q, page, limit, sort: 'path' });
			items = res.items;
			total = res.total;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function connect() {
		try {
			health = await api.health();
			info = await api.root();
			status = 'online';
			await refresh();
			events = api.events();
			events.addEventListener('index.changed', () => {
				void api.root().then((r) => (info = r));
				void refresh();
			});
		} catch {
			status = 'offline';
		}
	}

	let debounce: ReturnType<typeof setTimeout>;
	function onSearch() {
		clearTimeout(debounce);
		debounce = setTimeout(() => {
			page = 1;
			void refresh();
		}, 200);
	}

	function onFilter() {
		page = 1;
		void refresh();
	}

	function onShowOther() {
		if (!showOther && type === 'other') type = 'nif';
		page = 1;
		void refresh();
	}

	function go(delta: number) {
		page += delta;
		void refresh();
	}

	function pick(asset: AssetSummary) {
		if (asset.type === 'nif') onselect(asset);
	}

	onMount(connect);
	onDestroy(() => events?.close());

	const totalPages = $derived(Math.max(1, Math.ceil(total / limit)));
</script>

<aside class="library">
	<header>
		<span class="logo" aria-hidden="true"><Database size={15} /></span>
		<div class="titles">
			<span class="name">Library</span>
			<span class="sub">
				{#if status === 'online' && info}
					{info.total.toLocaleString()} assets · {info.archives.toLocaleString()} archives
				{:else if status === 'offline'}
					daemon offline
				{:else}
					connecting…
				{/if}
			</span>
		</div>
	</header>

	{#if status === 'offline'}
		<div class="offline">
			<WifiOff size={20} />
			<p>No <code>camelot serve</code> daemon reachable.</p>
			<p class="hint">
				Start it with <code>camelot serve</code>, or point this page at one with
				<code>?api=http://localhost:8760</code>.
			</p>
		</div>
	{:else}
		<div class="controls">
			<div class="search">
				<Search size={14} />
				<input
					class="input"
					type="search"
					placeholder="Search figures…"
					bind:value={q}
					oninput={onSearch}
				/>
			</div>
			<div class="filters">
				<select class="input" bind:value={type} onchange={onFilter} aria-label="Asset type">
					<option value="">all types</option>
					{#each types as t (t)}
						<option value={t}>{t} · {(info?.counts[t] ?? 0).toLocaleString()}</option>
					{/each}
				</select>
				<select class="input" bind:value={folder} onchange={onFilter} aria-label="Folder">
					<option value="">all folders</option>
					{#each dirs as [name] (name)}
						<option value={name}>{name}/</option>
					{/each}
				</select>
			</div>
			<label class="other">
				<input type="checkbox" bind:checked={showOther} onchange={onShowOther} />
				include unclassified entries
			</label>
		</div>

		<div class="list" class:loading>
			{#if error}
				<p class="err">{error}</p>
			{:else if items.length === 0 && !loading}
				<p class="empty">No assets.</p>
			{/if}
			{#each items as asset (asset.id)}
				<button
					class="row"
					class:selected={asset.id === selectedId}
					class:dim={asset.type !== 'nif'}
					onclick={() => pick(asset)}
					disabled={asset.type !== 'nif'}
					title={asset.path}
				>
					<FileBox size={13} />
					<span class="label">{asset.name}</span>
					<span class="type">{asset.type}</span>
				</button>
			{/each}
		</div>

		<footer>
			<button class="btn btn--ghost" disabled={page <= 1} onclick={() => go(-1)}>‹</button>
			<span class="nums">{page} / {totalPages}</span>
			<button class="btn btn--ghost" disabled={page >= totalPages} onclick={() => go(1)}>›</button>
			<button class="btn btn--ghost" onclick={refresh} aria-label="Refresh">
				<RefreshCw size={14} class={loading ? 'spin' : ''} />
			</button>
			{#if health}<span class="build" title="daemon build">{health.version}@{health.build}</span
				>{/if}
		</footer>
	{/if}
</aside>

<style>
	.library {
		width: 320px;
		min-width: 320px;
		display: flex;
		flex-direction: column;
		border-right: 1px solid var(--border);
		background: var(--surface-2);
		min-height: 0;
	}
	header {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-bottom: 1px solid var(--border);
	}
	.logo {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 30px;
		height: 30px;
		border-radius: var(--r-input);
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	.titles {
		display: flex;
		flex-direction: column;
		line-height: 1.2;
		overflow: hidden;
	}
	.name {
		font-weight: 650;
		font-size: 14px;
	}
	.sub {
		font-size: 11.5px;
		color: var(--text-3);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.controls {
		display: flex;
		flex-direction: column;
		gap: 8px;
		padding: 12px 14px;
		border-bottom: 1px solid var(--border);
	}
	.filters {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}
	.other {
		display: flex;
		align-items: center;
		gap: 7px;
		font-size: 11.5px;
		color: var(--text-4);
		cursor: pointer;
	}
	.other input {
		accent-color: var(--accent);
	}
	.search {
		position: relative;
		display: flex;
		align-items: center;
	}
	.search :global(svg) {
		position: absolute;
		left: 10px;
		color: var(--text-4);
	}
	.search .input {
		padding-left: 30px;
	}
	.list {
		flex: 1;
		overflow-y: auto;
		padding: 6px;
		display: flex;
		flex-direction: column;
		gap: 1px;
		min-height: 0;
		transition: opacity var(--dur) var(--ease);
	}
	.list.loading {
		opacity: 0.55;
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 7px 9px;
		border-radius: 8px;
		text-align: left;
		color: var(--text-2);
		width: 100%;
	}
	.row:hover:not(:disabled) {
		background: var(--surface-input);
		color: var(--text);
	}
	.row.selected {
		background: var(--accent-tint-12);
		color: var(--text);
	}
	.row.dim {
		opacity: 0.45;
		cursor: default;
	}
	.label {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 12.5px;
	}
	.type {
		font-size: 10px;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-4);
	}
	footer {
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 8px 10px;
		border-top: 1px solid var(--border);
		font-size: 12px;
		color: var(--text-3);
	}
	footer .nums {
		margin: 0 6px;
	}
	footer .build {
		margin-left: auto;
		font-size: 10px;
		font-variant-numeric: tabular-nums;
		color: var(--text-3);
	}
	.offline {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		padding: 40px 24px;
		text-align: center;
		color: var(--text-3);
	}
	.offline p {
		font-size: 12.5px;
	}
	.hint {
		color: var(--text-4);
		font-size: 11.5px;
	}
	code {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.9em;
		color: var(--text-2);
	}
	.empty,
	.err {
		padding: 16px;
		font-size: 12px;
		color: var(--text-4);
	}
	.err {
		color: var(--danger-text);
	}
	:global(.spin) {
		animation: spin 0.9s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
