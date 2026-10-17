<script lang="ts">
	/**
	 * The project chip and its menu — the selector (`notes/home.md` §D5,
	 * draft 4's `chip menu` board): the project being served, then the machine's
	 * own list with the work waiting, then the three doors.
	 *
	 * The page may hand the chip its *live* state (the world editor passes the
	 * session's own pending, so the count moves as you edit); without props the
	 * chip asks the daemon (`GET /home`) — same answer, same words.
	 *
	 * A switch reloads the page: everything on it — the zone, the figure, the
	 * client's index — belongs to the project that was served. Creating or
	 * cloning without opening stays here, and the list fills in.
	 */
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { ChevronDown, FolderOpen, GitBranch, LoaderCircle, Lock, Plus, X } from '@lucide/svelte';
	import { api, type HomeDoor, type HomePayload, type HomeRecent } from '$lib/api/client';
	import { menuState } from '$lib/home';
	import ProjectModal from './ProjectModal.svelte';

	interface Props {
		/** The live state when the page has one. */
		name?: string;
		pending?: number;
		readOnly?: boolean;
		holder?: string;
	}
	let { name = '', pending = 0, readOnly = false, holder = '' }: Props = $props();

	let open = $state(false);
	let asked = $state<HomePayload | null>(null);
	let recents = $state<HomeRecent[]>([]);
	let door = $state<'new' | 'clone' | null>(null);
	let busy = $state(false);
	let error = $state('');

	const total = (counts: Record<string, number>) => (counts?.fixtures ?? 0) + (counts?.rows ?? 0);
	/** The state the chip shows: the page's live one, else what the daemon said. */
	const facts = $derived(
		name || readOnly || holder
			? { name, pending, readOnly, holder }
			: asked?.project
				? {
						name: asked.project.name,
						pending: total(asked.project.pending),
						readOnly: asked.project.read_only,
						holder: asked.project.holder
					}
				: asked
					? { name: '', pending: 0, readOnly: false, holder: '' }
					: null
	);

	const chipState = $derived(
		facts === null
			? ''
			: facts.readOnly
				? facts.holder
					? `held by ${facts.holder}`
					: 'read-only'
				: facts.pending > 0
					? `${facts.pending} ${facts.pending === 1 ? 'edit' : 'edits'} to publish`
					: 'all published'
	);
	const warn = $derived(facts !== null && (facts.readOnly || facts.pending > 0));
	const title = $derived(
		facts?.readOnly
			? `this project is held by ${facts.holder || 'another process'}: writes are refused`
			: 'the projects this machine has opened'
	);

	async function load() {
		try {
			asked = await api.home();
			recents = asked.recents;
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		}
	}

	async function toggle() {
		error = '';
		open = !open;
		if (open) await load();
	}

	async function serve(row: HomeRecent) {
		busy = true;
		error = '';
		try {
			await api.homeOpen(row.path);
			// everything on this page belongs to the project that was served
			location.reload();
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		} finally {
			busy = false;
		}
	}

	async function forget(row: HomeRecent) {
		error = '';
		try {
			recents = (await api.homeForget(row.path)).recents;
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		}
	}

	async function done(answer: HomeDoor, openIt: boolean) {
		door = null;
		if (answer.opened && openIt) {
			// asked for the editor: the World page mounts on the new project
			location.assign(resolve('/world'));
			return;
		}
		if (answer.warning) error = answer.warning;
		open = true;
		await load();
	}

	function onWindowClick(event: MouseEvent) {
		if (!open) return;
		const inside = (event.target as HTMLElement | null)?.closest?.('.chip-wrap');
		if (!inside) open = false;
	}

	function onKey(event: KeyboardEvent) {
		if (event.key === 'Escape') open = false;
	}

	onMount(() => {
		if (!name && !readOnly) void load();
	});
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKey} />

<div class="chip-wrap">
	<button
		class="chip"
		class:warn
		aria-haspopup="menu"
		aria-expanded={open}
		{title}
		onclick={toggle}
	>
		{#if facts?.readOnly}
			<Lock size={13} />
		{:else}
			<span class="dot" aria-hidden="true"></span>
		{/if}
		<span class="name">{facts === null ? '…' : facts.name || 'No project'}</span>
		<span class="state">{facts === null ? '' : `· ${facts.name ? chipState : 'Open…'}`}</span>
		<ChevronDown size={13} />
	</button>

	{#if open}
		<div class="menu" role="menu">
			<div class="menu-head">
				<span class="name">{facts?.name || 'No project'}</span>
				<span class="state">{facts?.name ? `· ${chipState}` : '· Open…'}</span>
			</div>
			<div class="menu-label">ON THIS MACHINE</div>
			{#each recents as row (row.path)}
				<div class="menu-row">
					<button
						class="pick"
						role="menuitem"
						disabled={busy || !row.exists || !row.project}
						title={row.exists && row.project ? `serve ${row.name}` : 'not a project yet'}
						onclick={() => serve(row)}
					>
						<span class="name">{row.name}</span>
						<span class="meta">{menuState(row.pending, row.opened)}</span>
					</button>
					<button
						class="forget"
						title="forget it in this list — the folder stays"
						onclick={() => forget(row)}
					>
						<X size={13} />
					</button>
				</div>
			{:else}
				<p class="menu-empty">No projects on this machine yet.</p>
			{/each}
			<div class="menu-sep"></div>
			<button class="action" role="menuitem" onclick={() => (location.href = resolve('/home'))}>
				<FolderOpen size={14} /> Open a project…
			</button>
			<button class="action" role="menuitem" onclick={() => (door = 'new')}>
				<Plus size={14} /> New project…
			</button>
			<button class="action" role="menuitem" onclick={() => (door = 'clone')}>
				<GitBranch size={14} /> Clone a repository…
			</button>
			{#if busy || error}
				<p class="menu-error">
					{#if busy}<span class="spin"><LoaderCircle size={13} /></span>{/if}{error}
				</p>
			{/if}
		</div>
	{/if}
</div>

{#if door && asked}
	<ProjectModal
		kind={door}
		defaultClient={asked.daemon.client}
		onclose={() => (door = null)}
		ondone={done}
	/>
{/if}

<style>
	.chip-wrap {
		position: relative;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 10px 0 12px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-input);
		font-size: var(--fs-dense);
		color: var(--text-4);
		min-width: 0;
	}
	.chip:hover {
		border-color: var(--border-hover, var(--border-input));
	}
	.dot {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--accent);
	}
	.warn .dot {
		background: var(--warn);
	}
	.warn {
		color: var(--warn-text);
	}
	.name {
		color: var(--text-2);
		font-weight: 500;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.warn .name {
		color: var(--text);
	}
	.state {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.menu {
		position: absolute;
		top: calc(100% + 6px);
		right: 0;
		z-index: 40;
		width: 320px;
		padding: 6px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface);
		box-shadow: 0 12px 32px rgb(0 0 0 / 0.35);
	}
	.menu-head {
		display: flex;
		align-items: baseline;
		gap: 6px;
		padding: 6px 8px 8px;
		font-size: var(--fs-dense);
		color: var(--text-4);
	}
	.menu-head .name {
		font-weight: 600;
	}
	.menu-label {
		padding: 4px 8px;
		font-size: var(--fs-label);
		font-weight: 600;
		letter-spacing: 0.06em;
		color: var(--text-4);
	}
	.menu-row {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.menu-row .pick {
		display: flex;
		align-items: baseline;
		gap: 8px;
		flex: 1;
		min-width: 0;
		padding: 6px 8px;
		border: 0;
		border-radius: var(--r-sm, 6px);
		background: none;
		text-align: left;
		color: var(--text-2);
		font-size: var(--fs-dense);
	}
	.menu-row .pick:hover:not(:disabled),
	.action:hover {
		background: var(--surface-input);
	}
	.menu-row .pick:disabled {
		opacity: 0.55;
	}
	.menu-row .meta {
		color: var(--text-4);
		white-space: nowrap;
	}
	.forget,
	.action {
		display: flex;
		align-items: center;
		gap: 8px;
		border: 0;
		background: none;
		color: var(--text-3);
	}
	.forget {
		padding: 6px;
		border-radius: var(--r-sm, 6px);
	}
	.forget:hover {
		color: var(--danger-text);
		background: var(--surface-input);
	}
	.action {
		width: 100%;
		padding: 7px 8px;
		border-radius: var(--r-sm, 6px);
		font-size: var(--fs-dense);
		text-align: left;
	}
	.menu-sep {
		height: 1px;
		margin: 6px 4px;
		background: var(--border);
	}
	.menu-empty,
	.menu-error {
		padding: 6px 8px;
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.menu-error {
		display: flex;
		align-items: center;
		gap: 6px;
		color: var(--danger-text);
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
