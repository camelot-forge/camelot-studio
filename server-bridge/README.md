# server-bridge — (M2) OpenDAoC integration

The suite produces **artifacts** (files + `gamedata` table diffs). This
folder will hold the scripts/tooling to inject a model into the OpenDAoC
emulator (C#) and to test (spawn + animation cycle, e.g. via daochook).

The server does not need to know the formats: it references model IDs;
the client resolves the rest through its tables.