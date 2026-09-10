<script lang="ts">
	import { Bone, ChevronDown, FileBox } from '@lucide/svelte';
	import type { LoadedModel } from '$lib/gltf/load';
	import { BONE_INDEX, MAX_VERIFIED } from '$lib/skeleton';

	interface Props {
		model: LoadedModel;
	}
	let { model }: Props = $props();

	let showAll = $state(false);

	const joints = $derived.by(() => {
		// transient dedup set within a derived computation — not reactive state
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const seen = new Set<string>();
		const out: Array<{ index: number | null; name: string }> = [];
		for (const mesh of model.skinJson?.meshes ?? []) {
			for (const name of mesh.joints) {
				if (seen.has(name)) continue;
				seen.add(name);
				const idx = Object.entries(BONE_INDEX).find(([, n]) => n === name)?.[0];
				out.push({ index: idx !== undefined ? Number(idx) : null, name });
			}
		}
		return out;
	});

	const allBones = $derived(
		Object.entries(BONE_INDEX).map(([i, n]) => ({ index: Number(i), name: n }))
	);
</script>

<aside class="sidebar">
	<section class="card">
		<h3><FileBox size={14} /> Model</h3>
		<dl class="meta">
			<div>
				<dt>File</dt>
				<dd class="nums" title={model.name}>{model.name}</dd>
			</div>
			<div>
				<dt>Meshes</dt>
				<dd class="nums">{model.meshCount}</dd>
			</div>
			<div>
				<dt>Triangles</dt>
				<dd class="nums">{model.triangleCount.toLocaleString()}</dd>
			</div>
		</dl>
	</section>

	<section class="card">
		<h3><Bone size={14} /> Bones</h3>
		{#if joints.length > 0}
			<ul class="bones">
				{#each joints as { index, name } (name)}
					<li class="bone">
						<span class="idx nums">{index ?? '–'}</span>
						<span class="nm">{name}</span>
					</li>
				{/each}
			</ul>
			<button class="link" onclick={() => (showAll = !showAll)}>
				{showAll ? 'Hide' : 'Show'} canonical table (0–{MAX_VERIFIED})
				<ChevronDown size={14} class={showAll ? 'flip' : ''} />
			</button>
			{#if showAll}
				<ul class="bones">
					{#each allBones as { index, name } (index)}
						<li class="bone">
							<span class="idx nums">{index}</span><span class="nm">{name}</span>
						</li>
					{/each}
				</ul>
			{/if}
		{:else}
			<p class="note">No <code>.skin.json</code> sidecar loaded — drop one to see the rig.</p>
		{/if}
	</section>
</aside>

<style>
	.sidebar {
		width: 300px;
		min-width: 300px;
		display: flex;
		flex-direction: column;
		gap: 14px;
		padding: 16px;
		border-left: 1px solid var(--border);
		background: var(--surface-2);
		overflow-y: auto;
	}
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--r-card);
		padding: 14px;
	}
	h3 {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-3);
		margin-bottom: 12px;
	}
	.meta {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.meta div {
		display: flex;
		justify-content: space-between;
		gap: 12px;
	}
	dt {
		color: var(--text-3);
	}
	dd {
		text-align: right;
		color: var(--text);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.bones {
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: 2px;
		max-height: 340px;
		overflow-y: auto;
	}
	.bone {
		display: flex;
		gap: 10px;
		align-items: baseline;
		padding: 3px 6px;
		border-radius: 6px;
	}
	.bone:hover {
		background: var(--surface-input);
	}
	.idx {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 11px;
		color: var(--accent);
		width: 28px;
	}
	.nm {
		font-size: 12.5px;
		color: var(--text-2);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.link {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		margin-top: 10px;
		font-size: 12px;
		color: var(--text-3);
	}
	.link:hover {
		color: var(--accent);
	}
	.link :global(svg) {
		transition: transform var(--dur) var(--ease);
	}
	.link :global(svg.flip) {
		transform: rotate(180deg);
	}
	.note {
		font-size: 12px;
		color: var(--text-4);
	}
</style>
