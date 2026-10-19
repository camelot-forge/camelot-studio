<script lang="ts">
	/**
	 * The re-base panel (`notes/project.md` §D17, P8): the client moved
	 * under this project, and the panel says what it touched — then the two
	 * decisions. `keep ours` pins the recorded fingerprints; `adopt theirs`
	 * moves them to what the client holds now, and the ops that no longer fit
	 * are named either way. Nothing is written until one of the two is asked
	 * for, and the page says afterwards which one ran.
	 */
	import { onMount } from 'svelte';
	import { LoaderCircle, RefreshCw } from '@lucide/svelte';
	import { api, type HomeRebase } from '$lib/api/client';

	interface Props {
		path: string;
		name: string;
		onclose: () => void;
		/** The decision's own answer, or `null` when the panel was closed. */
		ondone: (found: HomeRebase | null) => void;
	}
	let { path, name, onclose, ondone }: Props = $props();

	let dialog: HTMLDialogElement;
	let view = $state<HomeRebase | null>(null);
	let busy = $state('');
	let problem = $state('');

	onMount(async () => {
		dialog.showModal();
		try {
			view = await api.homeRebase(path);
		} catch (error) {
			problem = error instanceof Error ? error.message : String(error);
		}
	});

	async function decide(decision: 'keep-ours' | 'adopt') {
		busy = decision;
		problem = '';
		try {
			const found = await api.homeRebaseDecide(path, decision);
			ondone(found);
			close();
		} catch (error) {
			problem = error instanceof Error ? error.message : String(error);
			busy = '';
		}
	}

	function close() {
		if (dialog?.open) dialog.close();
		else onclose();
	}
</script>

<dialog bind:this={dialog} onclose={() => ondone(null)} aria-label="re-base {name}">
	<div class="panel">
		<header>
			<span class="tile"><RefreshCw size={18} /></span>
			<div>
				<h2>Re-base {name}</h2>
				<p class="sub">
					The client moved under this project. Keep the base we recorded, or adopt what the client
					holds now.
				</p>
			</div>
		</header>

		{#if problem}
			<p class="problem">{problem}</p>
		{:else if !view}
			<p class="sub"><LoaderCircle size={14} /> reading the base back…</p>
		{:else if view.clean}
			<p class="sub">The base is the one this project recorded — nothing to decide.</p>
		{:else}
			<p class="count">
				{view.resources.length} resource(s) no longer match:
				{view.moved} moved, {view.missing} gone, {view.pinned} pinned
			</p>

			<ul class="resources">
				{#each view.resources as item (item.resource)}
					<li>
						<span class="state {item.state}">{item.state}</span>
						<span class="what" title={item.detail}>{item.resource}</span>
					</li>
				{/each}
			</ul>

			{#if view.journal.length}
				<h3>The journal, against the base as it stands</h3>
				<ul class="ops">
					{#each view.journal as entry (entry.resource)}
						<li class="resource">
							<span class="what">{entry.resource}</span>
							<ul>
								{#each entry.ops as op (op.key)}
									<li>
										<span class="verdict {op.verdict}">{op.verdict}</span>
										<span>{op.key}</span>
										<span class="where">{op.where} · row {op.row}</span>
										{#if op.detail}<span class="detail">{op.detail}</span>{/if}
									</li>
								{/each}
								{#if entry.unwalked}
									<li class="detail">
										{entry.unwalked} op(s) after the conflict were not judged
									</li>
								{/if}
								{#if entry.note}<li class="detail">{entry.note}</li>{/if}
							</ul>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}

		{#if view?.unwatched?.length}
			<ul class="unwatched">
				{#each view.unwatched as item (item.resource)}
					<li>
						zone {item.zone}: {item.ops} op(s), but <code>{item.resource}</code> is not
						fingerprinted — <code>camelot project populate --zones {item.zone}</code>
					</li>
				{/each}
			</ul>
		{/if}

		<footer>
			<button class="btn ghost" type="button" onclick={close} disabled={busy !== ''}>
				Close
			</button>
			{#if view && !view.clean}
				<button
					class="btn ghost"
					type="button"
					disabled={busy !== ''}
					onclick={() => decide('keep-ours')}
				>
					{busy === 'keep-ours' ? 'Pinning…' : 'Keep ours'}
				</button>
				<button
					class="btn primary"
					type="button"
					disabled={busy !== ''}
					onclick={() => decide('adopt')}
				>
					{busy === 'adopt' ? 'Adopting…' : 'Adopt theirs'}
				</button>
			{/if}
		</footer>
	</div>
</dialog>

<style>
	dialog {
		margin: auto;
		padding: 0;
		border: 1px solid var(--border-input);
		border-radius: var(--r-lg);
		background: var(--surface);
		color: var(--text);
		width: min(680px, 92vw);
		max-height: 90dvh;
		overflow-y: auto;
	}
	dialog::backdrop {
		background: rgb(0 0 0 / 0.55);
	}
	.panel {
		display: flex;
		flex-direction: column;
		gap: 14px;
		padding: 20px;
	}
	header {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	header h2 {
		margin: 0;
		font-size: var(--fs-title);
	}
	.tile {
		display: grid;
		place-items: center;
		width: var(--h-control-lg);
		height: var(--h-control-lg);
		border-radius: var(--r-md);
		background: var(--surface-input);
		color: var(--text-3);
	}
	.sub,
	.count {
		margin: 0;
		font-size: var(--fs-small);
		color: var(--text-3);
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.problem {
		margin: 0;
		font-size: var(--fs-small);
		color: var(--danger-text);
	}
	h3 {
		margin: 4px 0 0;
		font-size: var(--fs-small);
		color: var(--text-3);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}
	.resources > li {
		display: flex;
		align-items: baseline;
		gap: 10px;
		padding: 4px 0;
		border-bottom: 1px solid var(--border);
		font-size: var(--fs-dense);
	}
	.state,
	.verdict {
		flex: 0 0 72px;
		font-size: var(--fs-label);
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-4);
	}
	.state.missing,
	.state.moved,
	.verdict.conflict {
		color: var(--warn-text);
	}
	.state.pinned,
	.verdict.shows {
		color: var(--text-3);
	}
	.verdict.holds {
		color: var(--accent);
	}
	.what {
		font-family: var(--font-mono, monospace);
		color: var(--text-2);
		word-break: break-all;
	}
	.ops .resource > ul {
		padding-left: 82px;
	}
	.ops li li {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		font-size: var(--fs-small);
		color: var(--text-3);
	}
	.detail {
		color: var(--warn-text);
	}
	.unwatched {
		margin: 0;
		padding: 8px 10px;
		border: 1px solid var(--warn-border);
		border-radius: var(--r-md);
		background: var(--warn-tint);
		font-size: var(--fs-small);
		color: var(--warn-text);
		list-style: none;
	}
	.unwatched code {
		font-family: var(--font-mono, monospace);
	}
	footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding-top: 4px;
	}
</style>
