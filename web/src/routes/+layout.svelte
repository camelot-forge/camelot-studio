<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<title>Camelot Studio</title>
	<meta
		name="description"
		content="View and edit Dark Age of Camelot content: figures, characters, zones"
	/>
	<meta name="theme-color" content="#141414" />
	<link rel="icon" href={favicon} />
</svelte:head>

{@render children()}
