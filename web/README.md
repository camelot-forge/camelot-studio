# web — (M1) viewer

Svelte + three.js viewer: loads the figures exported to glTF by `core`
(bind pose first, animated `.kfa` reading later). Shippable as a static
site — no Python dependency for end users.

Contract: `.gltf` + `.bin` files produced by `camelot gltf`.