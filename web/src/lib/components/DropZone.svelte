<script lang="ts">
	import { FolderOpen, Upload } from '@lucide/svelte';

	interface Props {
		onfiles: (files: File[]) => void;
	}
	let { onfiles }: Props = $props();

	let dragging = $state(false);
	let input = $state<HTMLInputElement | null>(null);

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		const files = Array.from(e.dataTransfer?.files ?? []);
		if (files.length) onfiles(files);
	}
	function onPick(e: Event) {
		const files = Array.from((e.target as HTMLInputElement).files ?? []);
		if (files.length) onfiles(files);
	}
	function openPicker() {
		input?.click();
	}
</script>

<div
	class="dropzone"
	class:active={dragging}
	ondragover={(e) => {
		e.preventDefault();
		dragging = true;
	}}
	ondragleave={() => (dragging = false)}
	ondrop={onDrop}
	onclick={openPicker}
	role="button"
	tabindex="0"
	onkeydown={(e) => e.key === 'Enter' && openPicker()}
>
	<input
		bind:this={input}
		class="sr-only"
		type="file"
		accept=".glb,.gltf,.bin,application/json"
		multiple
		onchange={onPick}
	/>
	<span class="icon" aria-hidden="true"><Upload size={26} /></span>
	<h2>Drop a figure here</h2>
	<p>Load a <code>.gltf</code> (+ its <code>.bin</code>) or a single <code>.glb</code></p>
	<span class="hint">…or click to browse. Optional <code>.skin.json</code> shows the bone rig.</span
	>
	<span class="open" aria-hidden="true"><FolderOpen size={14} /></span>
</div>

<style>
	.dropzone {
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 8px;
		width: min(480px, 90%);
		margin: auto;
		padding: 56px 32px;
		border: 1.5px dashed var(--border-hover);
		border-radius: var(--r-dropzone);
		background: var(--surface-dim);
		cursor: pointer;
		text-align: center;
		transition:
			border-color var(--dur) var(--ease),
			background-color var(--dur) var(--ease);
	}
	.dropzone:hover,
	.dropzone.active {
		border-color: var(--accent);
		background: var(--accent-tint-08);
	}
	.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 56px;
		height: 56px;
		border-radius: var(--r-card);
		background: var(--accent-tint-12);
		color: var(--accent);
	}
	h2 {
		font-size: 18px;
		font-weight: 650;
		margin-top: 8px;
	}
	p {
		color: var(--text-2);
	}
	.hint {
		font-size: 12px;
		color: var(--text-4);
	}
	code {
		font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
		font-size: 0.92em;
		background: var(--surface);
		border: 1px solid var(--border);
		padding: 0 5px;
		border-radius: 6px;
	}
	.open {
		position: absolute;
		top: 14px;
		right: 16px;
		color: var(--text-4);
	}
</style>
