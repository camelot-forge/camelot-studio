<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { Pin, PinOff } from '@lucide/svelte';

	interface Props {
		/** Names the dock's remembered width, pin and spot (`camelot.dock.<id>`). */
		id: string;
		title: string;
		/** The line under the title: what this dock is showing right now. */
		sub?: string;
		icon: Component<{ size?: number }>;
		/** Which edge the dock is attached to (the side its border is on). */
		side?: 'left' | 'right';
		/** A dock may own its own scrolling (`scroll={false}`), the way the
		 * library does: its list scrolls, the controls above stay put. */
		scroll?: boolean;
		/** Tabs, when the dock has them: the page renders the strip. */
		tabs?: Snippet;
		/** Extra controls for the head, right-aligned. */
		head?: Snippet;
		/** A dock's own foot: actions that belong to the whole dock, not to its
		 * scrolled body (the builder's Randomize / Reset). */
		foot?: Snippet;
		children: Snippet;
	}
	let {
		id,
		title,
		sub = '',
		icon: Icon,
		side = 'left',
		scroll = true,
		tabs,
		head,
		foot,
		children
	}: Props = $props();

	const DEFAULT_WIDTH = 320;
	const MIN_WIDTH = 260;
	const MAX_WIDTH = 640;

	interface Saved {
		w?: number;
		pinned?: boolean;
		x?: number;
		y?: number;
	}

	function read(): Saved {
		if (typeof localStorage === 'undefined') return {};
		try {
			return JSON.parse(localStorage.getItem(`camelot.dock.${id}`) ?? '{}') as Saved;
		} catch {
			return {};
		}
	}

	// "My UI, not ours": a dock's width, its pin and — when it floats — its spot
	// are the operator's, remembered per dock and per browser.
	const saved = read();
	let width = $state(saved.w ?? DEFAULT_WIDTH);
	let pinned = $state(saved.pinned ?? true);
	let floating = $state({ x: saved.x ?? 16, y: saved.y ?? 16 });

	function remember() {
		try {
			localStorage.setItem(
				`camelot.dock.${id}`,
				JSON.stringify({ w: width, pinned, x: floating.x, y: floating.y })
			);
		} catch {
			/* a full or blocked storage is not worth a broken dock */
		}
	}

	/** The grip sits on the dock's inner edge: its drag moves that edge. */
	function resizeDown(event: PointerEvent) {
		event.preventDefault(); // no text selection, no native drag
		const startX = event.clientX;
		const startWidth = width;
		const dir = side === 'right' ? -1 : 1;
		// Window listeners, not pointer capture: a pointer the browser no longer
		// tracks must not abort the gesture.
		const move = (e: PointerEvent) => {
			width = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, startWidth + dir * (e.clientX - startX)));
		};
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
			remember();
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	/** A pinned dock is part of the layout; a floating one is dragged by its head. */
	function headDown(event: PointerEvent) {
		if (pinned) return;
		if ((event.target as HTMLElement).closest('button')) return;
		// A head is chrome: pressing it must not start a text selection, whose
		// native drag cancels the pointer after one move (measured).
		event.preventDefault();
		const from = { x: event.clientX, y: event.clientY, dx: floating.x, dy: floating.y };
		const move = (e: PointerEvent) => {
			floating = {
				x: Math.max(0, from.dx + (e.clientX - from.x)),
				y: Math.max(0, from.dy + (e.clientY - from.y))
			};
		};
		const up = () => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
			remember();
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
	}

	function togglePin() {
		pinned = !pinned;
		remember();
	}
</script>

<aside
	class="dock {side}"
	class:floating={!pinned}
	style="width: {width}px; min-width: {width}px;
	{!pinned ? `left: ${floating.x}px; top: ${floating.y}px;` : ''}"
>
	<header
		role="toolbar"
		tabindex="-1"
		class="dock-head"
		onpointerdown={headDown}
		title={pinned ? `${title} — unpin to let it float` : `${title} — drag to move it`}
	>
		<span class="logo" aria-hidden="true"><Icon size={15} /></span>
		<div class="titles">
			<span class="name">{title}</span>
			{#if sub}<span class="sub">{sub}</span>{/if}
		</div>
		{@render head?.()}
		<button
			type="button"
			class="pin"
			class:on={!pinned}
			aria-pressed={!pinned}
			onclick={togglePin}
			title={pinned ? 'unpin — the dock floats over the scene' : 'pin — back into the layout'}
		>
			{#if pinned}<Pin size={14} />{:else}<PinOff size={14} />{/if}
		</button>
	</header>

	{#if tabs}<div class="dock-tabs">{@render tabs()}</div>{/if}

	<div class="dock-body" class:no-scroll={!scroll}>{@render children()}</div>

	{#if foot}<footer class="dock-foot">{@render foot()}</footer>{/if}

	<button
		type="button"
		class="dock-grip"
		aria-label={`resize ${title}`}
		title="drag to resize — double-click to reset"
		onpointerdown={resizeDown}
		ondblclick={() => {
			width = DEFAULT_WIDTH;
			remember();
		}}
	></button>
</aside>
