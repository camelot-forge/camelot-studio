<script lang="ts">
	/**
	 * The three doors' modal: one folder on this machine, a DAoC client
	 * checked *before* anything is written, git, staging — and "Open it in the
	 * World editor", which chains the project switch.
	 *
	 * The modal does the writing and reports what happened: `ondone` carries the
	 * daemon's own answer, warning included, so the page says it in one line
	 * instead of guessing.
	 */
	import { onMount, untrack } from 'svelte';
	import { ChevronUp, Folder, GitBranch, LoaderCircle, Plus } from '@lucide/svelte';
	import { api, type HomeBrowse, type HomeClient, type HomeDoor } from '$lib/api/client';
	import { clientLabel, slug } from '$lib/home';

	interface Props {
		kind: 'new' | 'import' | 'clone';
		/** The client this daemon serves — the field's own default. */
		defaultClient: string;
		/** A URL Home's opener accepted: the clone door starts with it filled. */
		defaultUrl?: string;
		onclose: () => void;
		ondone: (door: HomeDoor, openIt: boolean) => void;
	}
	let { kind, defaultClient, defaultUrl = '', onclose, ondone }: Props = $props();

	const titles: Record<string, string> = {
		new: 'New project',
		import: 'Import a folder',
		clone: 'Clone a repository'
	};
	const subs: Record<string, string> = {
		new: 'A project is a folder on this machine.',
		import: 'An existing edits folder becomes a project, without moving it.',
		clone: 'Clone a project from a git repository.'
	};
	const primaries: Record<string, string> = {
		new: 'Create project',
		import: 'Import folder',
		clone: 'Clone'
	};

	let dialog = $state<HTMLDialogElement | null>(null);
	let name = $state('');
	let dir = $state('');
	// the props are the modal's *initial* values: the operator takes it from there
	let url = $state(untrack(() => defaultUrl));
	let client = $state(untrack(() => defaultClient));
	let serverconfig = $state('');
	let git = $state(false);
	let staging = $state(false);
	let openIt = $state(true);
	let busy = $state(false);
	let error = $state('');
	/** The folder is derived from the name until the operator edits it. */
	let folderEdited = $state(false);
	let home = $state('');
	let browse = $state<HomeBrowse | null>(null);
	let check = $state<HomeClient | undefined>(undefined);
	const label = $derived(clientLabel(check));
	const Icon = $derived(kind === 'clone' ? GitBranch : kind === 'new' ? Plus : Folder);
	const ready = $derived(
		dir.trim().length > 0 && client.trim().length > 0 && (kind !== 'clone' || url.trim().length > 0)
	);

	/** `~/servers/<slug>` — the draft's own default, refreshed until the
	 * operator names a folder himself. */
	function suggest() {
		if (folderEdited || !home) return;
		const leaf = kind === 'clone' ? slug(repoName(url)) : slug(name);
		dir = `${home}/servers/${leaf}`;
	}

	function repoName(value: string): string {
		const tail =
			value
				.trim()
				.replace(/\.git$/, '')
				.split(/[/:]/)
				.filter(Boolean)
				.pop() ?? '';
		return tail || 'project';
	}

	async function openBrowse() {
		try {
			browse = await api.homeBrowse(browse?.path ?? dir);
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		}
	}

	async function checkClient() {
		check = undefined;
		if (!client.trim()) return;
		try {
			check = await api.homeClient(client);
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		}
	}

	async function submit(event: Event) {
		event.preventDefault();
		if (!ready || busy) return;
		busy = true;
		error = '';
		try {
			const door =
				kind === 'new'
					? await api.homeNew({ name, dir, client, db_config: serverconfig, staging, open: openIt })
					: kind === 'import'
						? await api.homeImport({ dir, name, client, git, open: openIt })
						: await api.homeClone({ url, dir, client, open: openIt });
			ondone(door, openIt);
		} catch (problem) {
			error = problem instanceof Error ? problem.message : String(problem);
		} finally {
			busy = false;
		}
	}

	onMount(async () => {
		dialog?.showModal();
		try {
			home = (await api.homeBrowse('')).path;
		} catch {
			// no home directory to guess from: the field stays empty and the
			// operator types one — Browse… still works
		}
		suggest();
		void checkClient();
	});

	$effect(() => {
		// the name (or the URL) drives the folder until the folder is edited
		if (!folderEdited) {
			void name;
			void url;
			void home;
			suggest();
		}
	});
</script>

<dialog bind:this={dialog} {onclose} aria-label={titles[kind]}>
	<form class="modal" onsubmit={submit}>
		<header>
			<span class="tile"><Icon size={18} /></span>
			<div>
				<h2>{titles[kind]}</h2>
				<p class="sub">{subs[kind]}</p>
			</div>
		</header>

		{#if kind === 'clone'}
			<label class="field" for="door-url">
				<span>REPOSITORY</span>
				<input
					id="door-url"
					class="input"
					type="text"
					bind:value={url}
					oninput={() => (folderEdited = false)}
					placeholder="https://… or git@…"
				/>
			</label>
		{:else}
			<label class="field" for="door-name">
				<span>NAME</span>
				<input id="door-name" class="input" type="text" bind:value={name} placeholder="avalon" />
				<span class="hint">folder · {slug(name)}</span>
			</label>
		{/if}

		<label class="field" for="door-folder">
			<span>FOLDER</span>
			<div class="row">
				<input
					id="door-folder"
					class="input"
					type="text"
					bind:value={dir}
					oninput={() => (folderEdited = true)}
					placeholder="/home/you/servers/avalon"
				/>
				<button class="btn btn--secondary" type="button" onclick={openBrowse}>Browse…</button>
			</div>
			<span class="hint">
				{kind === 'new'
					? 'Where the project is created.'
					: kind === 'import'
						? 'The folder to import.'
						: 'Where the clone lands.'}
			</span>
		</label>

		{#if browse}
			<div class="browse">
				<div class="browse-head">
					<button
						class="btn btn--secondary tiny"
						type="button"
						disabled={!browse.parent}
						onclick={async () => {
							browse = await api.homeBrowse(browse?.parent ?? '');
						}}
					>
						<ChevronUp size={14} /> up
					</button>
					<span class="browse-path" title={browse.path}>{browse.path}</span>
					<button
						class="btn btn--secondary tiny"
						type="button"
						onclick={() => {
							dir = browse?.path ?? dir;
							browse = null;
						}}
					>
						Use this folder
					</button>
				</div>
				<ul>
					{#each browse.dirs as child (child.path)}
						<li>
							<button
								type="button"
								onclick={async () => {
									browse = await api.homeBrowse(child.path);
								}}
							>
								<Folder size={14} />{child.name}
							</button>
						</li>
					{:else}
						<li class="none">no sub-folder here</li>
					{/each}
				</ul>
			</div>
		{/if}

		<label class="field" for="door-client">
			<span>CLIENT</span>
			<div class="row">
				<input
					id="door-client"
					class="input"
					type="text"
					bind:value={client}
					onblur={checkClient}
				/>
				<span class="check" class:ok={label?.tone === 'ok'} class:warn={label?.tone === 'warn'}>
					{label?.text ?? (client.trim() ? 'checking…' : '')}
				</span>
			</div>
			<span class="hint">The DAoC client this project uses.</span>
		</label>

		{#if kind === 'new'}
			<label class="field" for="door-server">
				<span>SERVERCONFIG (OPTIONAL)</span>
				<input
					id="door-server"
					class="input"
					type="text"
					bind:value={serverconfig}
					placeholder="/home/you/daoc/env-local/server/config/serverconfig.xml"
				/>
				<span class="hint"
					>The environment's serverconfig.xml: the viewer reads its mobs from the database it names.</span
				>
			</label>
		{/if}

		<div class="options">
			{#if kind === 'import'}
				<label class="option">
					<input type="checkbox" bind:checked={git} />
					<span>Initialise a git repository</span>
				</label>
			{/if}
			{#if kind === 'new'}
				<label class="option">
					<input type="checkbox" bind:checked={staging} />
					<span>Add a staging environment</span>
					<span class="hint">you can add it later</span>
				</label>
			{/if}
			<label class="option">
				<input type="checkbox" bind:checked={openIt} />
				<span>Open it in the World editor</span>
			</label>
		</div>

		{#if error}
			<p class="error">{error}</p>
		{/if}

		<footer>
			<span class="rule">
				{kind === 'clone'
					? 'Run by this daemon, with your own git credentials.'
					: 'The client is not modified.'}
			</span>
			<button class="btn btn--secondary" type="button" onclick={onclose} disabled={busy}>
				cancel
			</button>
			<button class="btn btn--primary" type="submit" disabled={!ready || busy}>
				{#if busy}<span class="spin"><LoaderCircle size={14} /></span>{/if}
				{primaries[kind]}
			</button>
		</footer>
	</form>
</dialog>

<style>
	dialog {
		margin: auto;
		padding: 0;
		border: 1px solid var(--border-input);
		border-radius: var(--r-lg);
		background: var(--surface);
		color: var(--text);
		width: min(560px, 92vw);
		max-height: 90dvh;
		overflow-y: auto;
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.55);
	}
	.modal {
		display: flex;
		flex-direction: column;
		gap: 16px;
		padding: 20px;
	}
	header {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.tile {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 36px;
		height: 36px;
		border-radius: var(--r-md);
		background: var(--surface-input);
		color: var(--text-2);
		flex: 0 0 auto;
	}
	h2 {
		font-size: var(--fs-title);
		font-weight: 600;
	}
	.sub {
		margin-top: 2px;
		font-size: var(--fs-dense);
		color: var(--text-3);
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.field > span:first-child {
		font-size: var(--fs-label);
		font-weight: 600;
		letter-spacing: 0.06em;
		color: var(--text-4);
	}
	.row {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.input {
		flex: 1;
		height: 36px;
		padding: 0 10px;
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-input);
		color: var(--text);
		min-width: 0;
	}
	.hint {
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.check {
		font-size: var(--fs-small);
		color: var(--text-4);
		white-space: nowrap;
	}
	.check.ok {
		color: var(--ok-text, var(--text-3));
	}
	.check.warn {
		color: var(--warn-text);
	}
	.browse {
		border: 1px solid var(--border-input);
		border-radius: var(--r-md);
		background: var(--surface-dim);
		padding: 8px;
		max-height: 200px;
		overflow-y: auto;
	}
	.browse-head {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 6px;
	}
	.browse-path {
		flex: 1;
		font-size: var(--fs-small);
		color: var(--text-3);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.browse ul {
		list-style: none;
		display: flex;
		flex-direction: column;
	}
	.browse button {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 6px 8px;
		border: 0;
		border-radius: var(--r-sm, 6px);
		background: none;
		color: var(--text-2);
		font-size: var(--fs-dense);
		text-align: left;
	}
	.browse ul button:hover {
		background: var(--surface-input);
	}
	.browse li.none {
		padding: 6px 8px;
		font-size: var(--fs-small);
		color: var(--text-4);
	}
	.options {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.option {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: var(--fs-dense);
		color: var(--text-2);
	}
	.option .hint {
		margin-left: -4px;
	}
	.error {
		color: var(--danger-text);
		font-size: var(--fs-small);
	}
	footer {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 4px;
	}
	.rule {
		flex: 1;
		font-size: var(--fs-small);
		color: var(--text-4);
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
