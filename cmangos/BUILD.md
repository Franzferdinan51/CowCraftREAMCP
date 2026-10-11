# CMaNGOS — Build Documentation for Minta

CowCraft runs a vanilla (1.12.1) WoW server. The compatible open-source
emulator is **CMaNGOS** (Continued MaNGOS), classic branch.

> **Not built yet.** Compiling CMaNGOS + extracting game data takes hours.
> This doc gives the exact steps so it can be done when ready. The MCP
> gateway in `../mcp-gateway/` already runs against its stub backend today.

## Which fork

| Project | Branch | Notes |
|---|---|---|
| [cmangos/mangos-classic](https://github.com/cmangos/mangos-classic) | `master` | Canonical CMaNGOS classic (1.12.1). Start here. |
| [vmangos/core](https://github.com/vmangos/core) | `master` | What benilla's dev runs against; Warden off by default. |

Either speaks the 1.12.1 protocol benilla expects. Pick one; don't mix DBs.

## Prerequisites (Linux build host)

```sh
sudo apt update && sudo apt install -y \
  build-essential cmake git openssl libssl-dev libmysqlclient-dev \
  libace-dev mariadb-server mariadb-client
```

macOS (Mac Studio) can build it, but the reliable path is a Linux VM or
Docker (see below). Apple Silicon needs no special flags — CMake handles it —
but all community testing is x86_64; expect minor friction on ARM.

## Build steps

```sh
# 1. Clone
git clone https://github.com/cmangos/mangos-classic.git
cd mangos-classic

# 2. Configure (adjust CMAKE_INSTALL_PREFIX to taste)
mkdir build && cd build
cmake .. \
  -DCMAKE_INSTALL_PREFIX=/opt/mangos-classic \
  -DBUILD_PLAYERBOTS=ON \
  -DCONF_DIR=/opt/mangos-classic/etc

# 3. Compile (takes 30–90 min depending on cores)
make -j$(nproc)

# 4. Install
sudo make install
```

`BUILD_PLAYERBOTS=ON` enables IKE3's playerbot AI (bots that populate the
world and can act as party members). Recommended even before the MCP bridge
exists — it gives you AI companions on day one via in-game chat commands.

## Database

```sh
# 5. Create databases and load base SQL (from the repo)
mysql -u root -p -e "CREATE DATABASE classicmangos; CREATE DATABASE classiccharacters; CREATE DATABASE classicrealmd;"
# Load order matters; see the repo's README/INSTALL for the exact files:
#   classicmangos:   world DB (mangos.sql + updates)
#   classiccharacters: characters.sql + updates
#   classicrealmd:   realmd.sql
```

## Game data extraction (needs a 1.12.1 client, build 5875)

The server needs maps/vmaps/mmaps/DBC extracted from a real client:

```sh
# From the install prefix:
cd /opt/mangos-classic/bin
./MoveMapGen          # takes a long time (hours for full mmaps)
```

Copy the client's `DBC` files and extracted `maps/`, `vmaps/`, `mmaps/`
into `/opt/mangos-classic/data/` (or wherever `mangosd.conf` points).

Shortcuts:
- Skip full mmaps initially (`vmaps` only) for a faster first boot; add
  mmaps later for correct LoS/pathing.
- Prebuilt data packs float around the community, but verify checksums —
  mismatched data causes silent weirdness.

## Config

```sh
cp /opt/mangos-classic/etc/mangosd.conf.dist /opt/mangos-classic/etc/mangosd.conf
cp /opt/mangos-classic/etc/realmd.conf.dist  /opt/mangos-classic/etc/realmd.conf
```

Edit:
- `mangosd.conf`: DB connection strings, `DataDir` path.
- `realmd.conf`: DB connection string, bind address.

## Run

```sh
/opt/mangos-classic/bin/realmd   # auth server, port 3724
/opt/mangos-classic/bin/mangosd  # world server, port 8085
```

Create a GM account via the mangosd console:

```
account create <user> <pass>
account set gmlevel <user> 3 -1
```

Then point benilla at it: `WOW_HOST=localhost WOW_DATA=/path/to/WoW/Data`.

## Docker alternative

[DadsMmoLab/dads-mmo-lab](https://github.com/dadsmmolab/dads-mmo-lab) has
Docker installers for CMaNGOS classic/TBC/WotLK with playerbots prebuilt.
Fastest path to a running server if you don't want to compile.

Instant repack (no build at all):
[sagrid-argus/vanilla-andy-repack](https://github.com/sagrid-argus/vanilla-andy-repack)
— prebuilt 1.12.1 + ~1,000 bots, log in as Andy/Andy.

## Wiring the MCP gateway to CMaNGOS (the hard part)

The gateway's `backend.py` defines the `WoWBackend` interface. Three
implementation strategies, easiest first:

1. **SOAP/RA console** (days): CMaNGOS exposes SOAP (port 7878) and the RA
   telnet console (3443). A `live_backend.py` can send GM commands
   (`.tele`, `.additem`, `.character level`, etc.) and parse output.
   Coarse, but works for movement/quests/items without C++.

2. **HermesProxy-style proxy** (weeks): [WowLegacyCore/HermesProxy](https://github.com/WowLegacyCore)
   sits between client and server as a C# proxy. Extend it to inject
   sessions and scrape state — this is the closest public analog to
   CowCraft's "direct-server native" bridge.

3. **Native mangosd module** (weeks): write a C++ module inside mangosd
   exposing session-inject + state-snapshot over a local socket. Most
   faithful to CowCraft's architecture; most work.

Whichever you pick, also add the **agent-bound flag**: a boolean column on
the character table, set on first MCP `select_character` (gate: level 1 +
no GM history), checked in the command path to disable GM/dev commands for
that character on every client.
