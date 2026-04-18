# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Postgres Atelier is a **source-build-first** PostgreSQL development environment. The PostgreSQL server and extensions are **not** pre-installed in the image; instead, the user builds them from source inside the workspace via `pg-configure` / `pg-make` / `pg-install` helpers (aimed at bug-reproduction and feature-development workflows).

- **4 OS flavours**: Rocky Linux 9/8, Ubuntu 24.04/22.04
- **One container per OS**: `rocky9`, `rocky8`, `ubuntu24`, `ubuntu22`
- The image carries only the dependencies needed to build PG, plus dev tools and a workspace skeleton.

## Repository Structure

```
postgres-atelier/
├── docker-build             # Image build script (retry-on-fail)
├── docker-compose.yml       # 4-container definition
├── tmux-session.py          # Python-based tmux session manager
├── tmux-docker / -atelier   # tmux session launcher wrappers
├── tmux-*.yml               # tmux layout configs
├── tmux-list / tmux-reload  # Utilities
└── dockerfiles/
    ├── rocky/{9,8}/Dockerfile
    ├── ubuntu/{24,22}/Dockerfile
    └── files/               # Shared assets (bashrc, profile, gitconfig, workspace tarball, VSCode JSONs, etc.)
```

## Build & Run

```bash
./docker-build                  # Build the 4 images (rocky9/rocky8/ubuntu24/ubuntu22)
docker compose up -d            # Bring up the 4 containers
docker compose exec -u postgres -w /var/lib/postgresql/workspace/postgres ubuntu24 bash --login
```

## Network

- Docker Compose default network. No fixed IPs.
- Containers reach each other by compose service name (`rocky9`, `rocky8`, `ubuntu24`, `ubuntu22`) via Compose's built-in DNS.
- `host.docker.internal` / `host` resolve to the Docker host via `extra_hosts: host-gateway`.

## Users & Paths

| Item | Rocky 9/8 | Ubuntu 24/22 |
|------|-----------|--------------|
| postgres home | `/var/lib/pgsql` | `/var/lib/postgresql` |
| workspace | `$HOME/workspace` (volume) | same |
| vscode-server | `$HOME/.vscode-server` (volume) | same |
| Claude config | `$HOME/.claude` (volume) | same |
| PG install prefix | `$HOME/.local` | same |
| `$PGDATA` (default) | `$HOME/workspace/pgdata` | same |

- The `postgres` user is created in the image via `useradd` (no PG package required).
- postgres has passwordless sudo.
- `gencov.py` is installed system-wide at `/usr/local/bin/gencov.py`.

## PostgreSQL Source Build (inside the container)

```bash
# First time: fetch sources. git-fetch hits all remotes at once.
cd ~/workspace/postgres
git remote -v                             # origin → $GIT_URL (if set at build), postgres → postgres/postgres, upstream → postgresql-cfbot/postgresql
git-fetch                                 # git fetch --all --tags --prune --force
git checkout -b cfNNNN upstream/cf/NNNN   # review/test a commitfest entry
git push -u origin cfNNNN                 # keep a copy in your fork

# Build / install (prefix is $HOME/.local — no sudo needed)
pg-configure debug               # release | debug | valgrind | coverage
pg-make -j$(nproc)               # = make world "$@"
pg-install                       # make install-world into $HOME/.local
initdb -D $PGDATA                # $PGDATA defaults to $HOME/workspace/pgdata
pg-start                         # pg_ctl -D $PGDATA -l $PGDATA/logfile start
```

`pg-configure` modes:
- `release` — standard build
- `debug` — `-Og -ggdb`, FORTIFY disabled
- `valgrind` — debug + `-DUSE_VALGRIND`
- `coverage` — debug + `--enable-coverage`

## Key Shell Helpers (pgsql_bashrc)

```
pg-configure [release|debug|valgrind|coverage] # always injects --prefix=$HOME/.local
pg-make [make-opts]                            # = make world "$@"
pg-install                                     # make install-world (no sudo; writes to $HOME/.local)
pg-clean                                       # make distclean
pg-check [make-opts]                           # make check-world "$@"
pg-regress [test...]                           # src/test/regress: `make check` or `make check-tests TESTS=...`
pg-regress-list                                # lists available tests from parallel_schedule
pg-init                                        # initdb -D $PGDATA + minimal postgresql.conf/pg_hba.conf
pg-start / pg-restart / pg-stop / pg-status    # pg_ctl wrappers against $PGDATA
pg-kill                                        # kills via $PGDATA/postmaster.pid
pg-valgrind                                    # runs postgres under Valgrind; auto-picks up PG source's src/tools/valgrind.supp
pg-core <core-file>                            # analyzes a core with GDB
pg-gcov [args...]                              # commit-scoped coverage report via gencov.py
pg-lcov [clear|report]                         # whole-tree coverage via lcov + genhtml (html.gcov/index.html)
git-fetch / git-clean / git-log                # workspace git helpers (git-fetch: `git fetch --all --tags --prune --force`)
```

Prompt format: `postgres@rocky9:~$`, `root@ubuntu24:~$`, etc.

## tmux

```bash
./tmux-docker          # session "docker": single "atelier" window, 4-pane tiled, one postgres shell per container at workspace/postgres
./tmux-atelier         # session "atelier": btop + shell + docker logs (4 containers)
./tmux-reload          # docker compose up -d --remove-orphans --wait, then ./tmux-docker
./tmux-list            # show current sessions/windows/panes
```

**Custom keybindings** (configured by tmux-session.py):
- `Ctrl+B, Ctrl+S` — toggle pane synchronization
- Click on status-right — toggle pane sync
- Click on status-left — switch to the next session

## Caveats

- `pg-start/restart/stop/status` drive `pg_ctl` against `$PGDATA`; they do not touch systemd. Override `$PGDATA` if you run multiple data dirs.
- `pg-configure` hardcodes the flag set (prefix `$HOME/.local`, PGDG-parity `--with-*` options); edit the function directly to deviate. `--with-liburing` is auto-skipped on Rocky 8 (EPEL 8 lacks `liburing-devel`).
- The Meson build (PG 18+) uses the already-installed `meson`/`ninja-build`. On Rocky 8, if the EPEL `meson` is too old, run `pip3 install --user meson`.
- Runtime settings such as `shared_preload_libraries` are not preconfigured; set them yourself after `initdb` (or let `pg-init` seed the defaults).
