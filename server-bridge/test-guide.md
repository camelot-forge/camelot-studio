# Setting up a machine to test the client MPAK ladder

> Written for a fresh Linux x86-64 machine with no DAoC anything installed.
> Windows and macOS notes are at the end; the server half is the same, the
> client half differs.
>
> Time: ~40 min, most of it downloads and a 69 MB SQL import.

## What the test is for

Three questions, in order, each answered by one client run. Stop at the
first failure and report it.

| | what it asks | what it needs |
|---|---|---|
| **A** | does the client accept an MPAK **container** we wrote? | the client |
| **B** | does it read **modified table data**? | the client |
| **C** | can a mod introduce a **new id**? | the client + the server |

**A needs no server**: `gamedata.mpk` is read at startup, so launching the
client to the login screen already answers it. B and C want a running server
to see a creature.

## Part 1 — the server

### 1.1 Prerequisites

```sh
sudo apt install -y git docker.io curl          # or your distro's equivalents
curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0
export PATH="$HOME/.dotnet:$PATH"               # add to ~/.bashrc
dotnet --version                                 # expect 10.x
```

Docker must be usable without sudo, or prefix the docker commands below.

### 1.2 Three checkouts

```sh
mkdir -p ~/daoc && cd ~/daoc
# the pinned server commit this repository was measured against
git clone https://github.com/OpenDAoC/OpenDAoC-Core.git
git -C OpenDAoC-Core checkout 8cf2f1aea
git clone https://github.com/OpenDAoC/OpenDAoC-Database.git
```

### 1.3 The database

The repository's own `docker-compose.yml` does **not publish a port for the
database**, so a server running on the host cannot reach it. Run MariaDB
directly instead, with the port published and the same settings the compose
file uses:

```sh
docker network create opendaoc-net 2>/dev/null

docker run -d --name opendaoc-db -p 3307:3306 --network opendaoc-net \
  -v opendaoc-db-data:/var/lib/mysql \
  -e MYSQL_DATABASE=opendaoc -e MYSQL_ROOT_PASSWORD=my-secret-pw \
  mariadb:10.6 \
  --default-authentication-plugin=mysql_native_password \
  --lower_case_table_names=1 \
  --character-set-server=utf8mb3 --collation-server=utf8mb3_general_ci

sleep 25
docker exec opendaoc-db mariadb -uroot -pmy-secret-pw -e "SELECT VERSION();"
```

Import the 110 dumps. They are ~69 MB combined and take a couple of minutes:

```sh
cd ~/daoc/OpenDAoC-Database/opendaoc-db-core
cat *.sql > /tmp/opendaoc-full.sql
docker cp /tmp/opendaoc-full.sql opendaoc-db:/tmp/full.sql
docker exec opendaoc-db sh -c \
  'mariadb -uroot -pmy-secret-pw --default-character-set=utf8mb3 opendaoc < /tmp/full.sql'

# expect 110 tables, 34545 items, 41 races
docker exec opendaoc-db mariadb -uroot -pmy-secret-pw opendaoc -e "
  SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='opendaoc';
  SELECT COUNT(*) FROM itemtemplate;
  SELECT COUNT(*) FROM race;"
```

### 1.4 Build and start

`CoreServer/config/serverconfig.xml` is **absent from the checkout** (it is
gitignored, by design) and the build fails without it:

```sh
cd ~/daoc/OpenDAoC-Core
cp CoreServer/config/serverconfig.example.xml CoreServer/config/serverconfig.xml
```

Point it at the database you just created — the example says `localhost:3306`,
which is the port the compose file *would* have used, not the one you have:

```sh
sed -i 's|Server=localhost;Port=3306;Database=opendaoc;UserId=root;Password=root;|Server=localhost;Port=3307;Database=opendaoc;UserId=root;Password=my-secret-pw;|' \
  CoreServer/config/serverconfig.xml
grep -o 'Server=[^;]*;Port=[0-9]*' CoreServer/config/serverconfig.xml   # expect Port=3307
```

Build and run:

```sh
dotnet build "Dawn of Light.sln" -c Release        # expect 0 errors, ~30 s
cp CoreServer/config/serverconfig.xml Release/config/serverconfig.xml
cd Release && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=False ./CoreServer
```

Expect `Script compilation: True` and, shortly after, the server listening on
`10300`. The startup prints ~400 non-fatal errors (missing spells, pathfinding
library absent); they are the database dump and the code being at different
points in time, not a broken install.

Leave it running in its own terminal.

## Part 2 — the client

The client is 32-bit Windows. On Linux it runs under Wine; on Windows it runs
natively; on macOS it is the fiddliest and the least recommended for this.

### 2.1 Get a client

Use the **OpenDAoC client installer** (a modified retail client, 1.127, built
for this server): <https://www.opendaoc.com/docs/client>. Any client whose
`game.dll` is 1.127 works; a `1.130` retail client is **refused** by the
server (the newest packet library is 1129, and the dispatch is an exact match).

Install it somewhere writable, e.g. `~/daoc-client`.

### 2.2 Run it correctly — this matters

**Never launch `camelot.exe` directly.** The client's `patch.cfg` points at
Broadsword's patch servers, and a direct launch lets the official patcher
re-download files over your modifications. The supported entry points are:

```sh
# Linux (Wine)
cd ~/daoc-client
wine OpenDAoCLauncher.exe                 # the launcher, if it runs under Wine
# or, if the launcher fails under Wine (it is WPF), call the component it calls:
wine connect.exe game1127.dll 127.0.0.1:10300 <account> <password>
```

`connect.exe` resolves the server, launches the game and patches its memory to
point the connection at it. The port defaults to 10300. The account is created
on first login (`AutoAccountCreation` is on) — use any name and password.

On Windows, run `OpenDAoCLauncher.exe` from the client directory.

## Part 3 — the MPAK ladder

Clone this repository on the test machine if it is not already there:

```sh
git clone git@github.com:camelot-forge/camelot-studio.git
cd camelot-studio && ./bootstrap.sh
export CAMELOT=~/daoc/camelot-studio
export DAOC_CLIENT=~/daoc-client
```

**Back the archive up before anything.**

```sh
cp "$DAOC_CLIENT/gamedata.mpk" "$DAOC_CLIENT/gamedata.mpk.orig"
```

### Test A — is our container accepted?

Content is byte-identical to the original; only the container is ours.

```sh
cd $CAMELOT
.venv/bin/camelot mpak repack "$DAOC_CLIENT/gamedata.mpk" /tmp/testA.mpk
cp /tmp/testA.mpk "$DAOC_CLIENT/gamedata.mpk"

# launch the client and get to the login screen (a server is not required)
wine "$DAOC_CLIENT/camelot.exe"     # only to reach the login screen; see 2.2
```

**Observe**: monsters and NPCs render as usual. Because the content is
identical, *any* visible difference means the container was rejected.

```sh
cp "$DAOC_CLIENT/gamedata.mpk.orig" "$DAOC_CLIENT/gamedata.mpk"   # roll back
```

### Test B — is modified data read?

Change one table and look for the change. `monnifs.csv` names creature models,
so renaming a creature you can see is the clearest signal.

```sh
cd $CAMELOT
python3 - <<'PY'
import os, sys
sys.path.insert(0, "core")
from camelot.mpak import MpakArchive
d = os.environ["DAOC_CLIENT"]
a = MpakArchive(f"{d}/gamedata.mpk")
try:
    rows = a.read("monnifs.csv").decode("latin1").split("\n")
    for i, r in enumerate(rows):
        if ",Albion Stone Minotaur," in r:
            rows[i] = r.replace("Albion Stone Minotaur", "PATCHED MINOTAUR")
            break
    else:
        raise SystemExit("the reference row was not found; adjust the search")
    open("/tmp/monnifs.csv", "w", encoding="latin1").write("\n".join(rows))
finally:
    a.close()
PY
.venv/bin/camelot mpak repack "$DAOC_CLIENT/gamedata.mpk" /tmp/testB.mpk \
  --replace "monnifs.csv=/tmp/monnifs.csv"
cp /tmp/testB.mpk "$DAOC_CLIENT/gamedata.mpk"
```

**Observe**: launch, log in, find the Stone Minotaur (Midgard stone giant, or
spawn one with a GM command: `&spawn mino`). The name shown changes if the
client re-read the table. Roll back with the `.orig` copy.

### Test C — can a mod introduce a new id?

Add a `monnifs` row mapping an unused id to an existing model. No asset writer
is involved: the new id points at a NIF that already ships.

```sh
python3 - <<'PY'
import os, sys
sys.path.insert(0, "core")
from camelot.mpak import MpakArchive
d = os.environ["DAOC_CLIENT"]
a = MpakArchive(f"{d}/gamedata.mpk")
try:
    text = a.read("monnifs.csv").decode("latin1")
finally:
    a.close()
rows = text.split("\n")
ids = [int(r.split(",")[0]) for r in rows[2:] if r.split(",")[0].isdigit()]
new_id = max(ids) + 1          # just above the table's own range: the
                               # client's parsers cap their ids, so a wild
                               # number like 9001 risks rejection for a
                               # reason unrelated to what is being tested
print("existing ids go up to", max(ids), "-> using", new_id)
# duplicate an existing row rather than craft one: the table has ~30 columns
# and a parser that expects them all, so copying the shape is the only safe
# way to add a row
for r in rows:
    if ",Albion Stone Minotaur," in r:
        fields = r.split(",")
        fields[0] = str(new_id)
        fields[1] = "Probe Minotaur"
        rows.append(",".join(fields))
        break
else:
    raise SystemExit("the reference row was not found; adjust the search")
open("/tmp/monnifs_c.csv", "w", encoding="latin1").write("\n".join(rows))
PY
.venv/bin/camelot mpak repack "$DAOC_CLIENT/gamedata.mpk" /tmp/testC.mpk \
  --replace "monnifs.csv=/tmp/monnifs_c.csv"
cp /tmp/testC.mpk "$DAOC_CLIENT/gamedata.mpk"
```

Then place a creature with that model id (printed by the script) on the server — the mod loader does
exactly this, and needs one config line:

```sh
# in OpenDAoC-Core/Release/config/serverconfig.xml
#   <ScriptAssemblies>ICSharpCode.SharpZipLib,Newtonsoft.Json</ScriptAssemblies>
# without it script compilation fails and the server will not start
mkdir -p "$DAOC_CLIENT/../OpenDAoC-Core/Release/scripts/camelot-mods"
cp $CAMELOT/server-bridge/camelot-mod-loader/*.cs \
   ~/daoc/OpenDAoC-Core/Release/scripts/camelot-mods/
```

Generate a pack whose rows use model 9001 and let the loader write them, then
spawn one in game. **Observe**: a minotaur-looking creature appears where the
id has no right to work. That is the whole question of the phase.

## What to report back

For each test: which one, pass or fail, and the exact symptom. The client's
own log is in `logs/` inside the client directory — `error.log` and
`warn.log` mention archive and table problems by name.

If **A** fails, stop: the client validates the header's undecoded six bytes,
and reversing that comes before anything else.

## Platform notes

- **Linux**: Wine, as above. The best of the three for this test — no
  emulation, and the client is 32-bit which Wine handles.
- **Windows**: native; run `OpenDAoCLauncher.exe` from the client directory.
- **macOS**: the client is 32-bit Windows and needs Wine. Expect the fights
  recorded in `workspace/notes`:
  a WPF launcher that renders blank, `dotnet20`/`dotnet35`, `cabextract`.
  Not recommended for this test if a Linux or Windows machine is available.

## Traps already paid for

- the compose file publishes no database port, so a host-run server fails
  every query while the config looks correct;
- `serverconfig.xml` is absent from the checkout and gitignored;
- the client must not be launched directly, or the official patcher
  overwrites modifications;
- the server refuses a 1.130 client (packet libs stop at 1129) — use 1.127;
- the server's ~400 startup errors are content skew, not a broken install.
