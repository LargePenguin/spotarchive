# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Spotarchive is a cross-platform (Linux and Windows) Python CLI that caches metadata about a user's Spotify playlists into per-playlist `setlist.csv` files, then legally sources the songs through an abstracted download backend (default: [SomeDL](https://github.com/ChemistryGull/SomeDL), which pulls from YouTube/Deezer/etc.). YT Music support is planned. The full product spec is in `.tmp/spec.md`; the user-facing documentation lives in `docs/` (see **Documentation** below) — keep it in sync when behavior changes; `README.md` stays a short overview.

> [!IMPORTANT]
> **Privacy boundary — never cross it.** Never include personally identifying or sensitive information in source code, tests, documents (README, `docs/`, comments, fixtures, sample data), commit messages, or PR/changelist descriptions. This covers the computer hostname, usernames and home-directory paths, email addresses, IP/MAC addresses, secrets, API keys, passwords, session tokens, cookies, `x-bc` values, user agents captured from a real session, Spotify account/user IDs and real playlist contents. Use obvious placeholders (`<your-username>`, `example.com`, `dummy-token`) and fake data in tests and examples. Session/credential files must be gitignored and never committed. Before committing or opening a PR, review the diff and descriptions for such leaks.

## Status

Steps 1–4 of the development plan are done: docs scaffolding, the Spotify features (browser login, playlist selection, `setlist.csv` caching) and downloading through the backend interface (SomeDL), plus the install scripts from step 5. Metadata cleanup, updates and the config file don't exist yet. Update this file (especially **Commands** and **Architecture**) as code lands — don't leave it describing things that don't exist, and don't invent commands before they work.

Development plan order: (1) `CLAUDE.md`, (2) `docs/`, minimal `README.md` (ASCII-art name + status badges) and quickstart, (3) Spotify features, (4) download-backend features, (5) metadata cleanup, setup script and config file.

## Commands

User install: `./install.sh` (Linux) or `install.ps1` (Windows), also usable as `curl ... | bash` / `irm ... | iex`. **Don't run either from a worktree** (they `pip install -e` into `.venv`; both refuse a worktree). Development setup, from the repo root with the virtualenv active:

```bash
pip install -e ".[dev,somedl]"          # package + pytest + SomeDL (default backend; also needs ffmpeg)
python -m playwright install chromium   # browser for the login (Chrome/Edge also work)
python -m pytest                        # all tests; browser tests skip if no browser is installed
python -m pytest tests/test_browser_auth.py   # real headless Chromium against a fake Spotify
```

Working CLI:

```bash
spotarchive                  # login (if the saved one doesn't work) -> fetch with selection -> download
spotarchive -l | --login     # interactive browser login helper
spotarchive -f | --fetch     # choose playlists and rebuild setlist.csv files; -fn / --fetch --noselect reuses previous selection
spotarchive -d | --download  # download missing songs of the selected, fetched setlists via the backend (-fd, -fnd combine)
spotarchive -V | --version
spotarchive -h | --help
```

Still planned (see `.tmp/spec.md`): `-m | --meta` (metadata cleanup, combinable as `-dm`), `-u | --update` (compare against GitHub and update in place). Plain `spotarchive` will then also check for updates first (prompting before installing) and fix metadata after downloading.

## Architecture

Package in `src/spotarchive/` (setuptools, `pyproject.toml`; console script `spotarchive = spotarchive.cli:main`). Dependencies: `playwright` (browser login), `requests`, `rich` (colors, progress bars), `questionary` (checkboxes); optional extra `somedl` (default backend, run as a separate program).

- **`cli.py`** — argparse flags and orchestration (`run_login`, `ensure_login`, `run_fetch`, `run_download`); maps errors to exit codes (1 error, 130 cancelled). `main(argv, data_dir)` takes the data folder so tests can point it at a temp dir; `make_client`/`make_backend` are the seams tests replace with fakes.
- **`spotify/auth.py`** — browser login. `interactive_login` starts installed Chrome, then Edge, then Playwright's bundled Chromium as a **plain process** (not Playwright-driven: automated browsers get endless captchas on Spotify's login) on its own profile in `data/browser-profile/`, with `--remote-debugging-port=0` (port read from `DevToolsActivePort`). While the user logs in it only polls the DevTools HTTP `/json/list` for tab URLs; once a tab reaches `open.spotify.com` it connects over CDP, reads the `sp_dc` cookie and user agent, and closes the window with `Browser.close`, then gets the first token through `refresh_session`. It keeps only spotify.com cookies and the user agent. A DevTools port turns `navigator.webdriver` on, so the login window gets `--disable-blink-features=AutomationControlled`; headless launches also drop `--enable-automation`. `refresh_session` replays the cookies in headless Chromium to get a fresh token, so Spotify's token-endpoint signing (TOTP) is never reimplemented; from the same page load it reads the persisted-query hashes out of the main `web-player.*.js` script. `SessionTokenProvider` caches the token (and hashes) and refreshes it when it expires. (The spec mentions `x-bc`; that's not a Spotify header and isn't used.)
- **`spotify/client.py`** — read-only client for the web player's GraphQL API (`POST api-partner.spotify.com/pathfinder/v2/query`, persisted queries `profileAttributes`, `libraryV3` flattened with the `Playlists` filter, `fetchPlaylist`, `fetchLibraryTracks` for Liked Songs). The public Web API (`api.spotify.com`) is **not** used: it answers web-player tokens with 429. Only the bearer token is needed (no `client-token`). Offset pagination against `totalCount`, one forced token refresh on 401, one hash re-read (via token refresh) on 412 then `QueryOutdated`, `Retry-After` backoff on 429/5xx, `RateLimited` without waiting when Spotify asks for more than a minute. `DEFAULT_QUERY_HASHES` is the fallback when no hashes were captured.
- **`spotify/models.py`** — `Playlist`/`Track` parsed from pathfinder JSON. No ISRC in that API (the column stays, empty); Liked Songs have no album date. Liked Songs uses the pseudo ID `liked-songs` (`spotify:collection:tracks`).
- **`session.py`** — `data/session.json`: login (cookies, user agent, token + expiry, display name, query hashes) and per-playlist state (`selected`, `folder`). Written atomically, mode 600 on POSIX.
- **`setlist.py`** — folder naming (Windows-safe, case-insensitive dedup by appending the playlist ID, previous owner keeps its folder, renamed playlists move their folder) and `setlist.csv` I/O. Columns: `name, artist, album, release_date, date_added, duration_ms, isrc, spotify_url`; artists joined with `; `.
- **`fetch.py`** — records the selection and writes setlists; one failing playlist doesn't stop the others.
- **`backends/`** — the download-backend interface. `base.py`: `DownloadBackend` (class attrs `name`, `shows_own_output`, `audio_extensions`; `check()` raises `BackendUnavailable`, `download(song, folder) -> list[Path]` raises `DownloadFailed`) and `Song` (a parsed setlist row; `key` = Spotify URL, else name/artists/album). `__init__.py`: `BACKENDS` registry + `get_backend(name, **options)`. Keep all helper-specific code in its own module; nothing outside `backends/` may know about SomeDL.
- **`backends/somedl.py`** — runs `somedl --here --skip-file-check [--format F] -- "First Artist - Title"` once per song with `cwd` = playlist folder and inherited stdio (its output is the progress display). `--here` forces flat `{artist} - {song}` names regardless of the user's SomeDL config. SomeDL exits 0 even on failure, so success = a new/modified audio file (mtime diff of the folder). Queries equal to SomeDL subcommands (`import`, `web`, ...) are refused. Executable looked up next to `sys.executable`, then on PATH; `check()` also requires `ffmpeg`.
- **`download.py`** — smart downloading. Per playlist folder, `.downloads.json` maps song key -> files (helpers name files after what *they* found, so names can't be matched reliably). A song is present if a recorded file exists or an audio file's stem matches `Artist - Song` (normalized; first/all artists); name matches are written back to the record. Pending songs go to the backend one at a time; the record is saved after each song; failures are collected, not recorded, and don't stop the rest. Downloading only reads `session.json` + setlists, never Spotify.
- **`install.sh` / `install.ps1`** (repo root) — single-command install. Use the checkout they sit in, otherwise clone into `~/spotarchive` (or `git pull --ff-only` an existing clone). They create `.venv`, `pip install -e ".[somedl]"`, run `playwright install chromium`, and install FFmpeg if it's missing, asking first (apt/dnf/pacman/zypper/apk with sudo; winget `Gyan.FFmpeg`). Then they expose the command: a symlink in `~/.local/bin` on Linux, or a `spotarchive.cmd` launcher in `%LOCALAPPDATA%\spotarchive\bin` added to the user PATH on Windows (not `.venv\Scripts`, which would shadow python/pip). Idempotent; the options are documented in `docs/quickstart.md`. `install.ps1` must not call `exit`, since that closes the window under `iex`. Keep the two scripts' behavior and options in sync.
- **`ui.py`** — rich console output (ASCII markers so legacy Windows consoles work), questionary checkbox, progress bars.

Planned:
- **Metadata correction** — use the helper's own metadata when it supports it (e.g. `somedl import`), otherwise fall back to `setlist.csv`.
- **Configuration** — a config file in the project root: download folder, which backend to use, and per-backend settings (output format, metadata source of truth, folder structure, ...). Backend settings should reach backends as `get_backend(name, **options)` keyword args (SomeDL already takes `executable`, `audio_format`, `extra_args`).

Everything must work on both Linux and Windows: use `pathlib`, avoid shell-specific assumptions, and don't hardcode path separators.

## Documentation

- **`README.md` stays minimal**: an ASCII-art rendering of the tool's name, the usual badges (Python version, downloads, stars, last commit, ...), what the project is, a short feature list, a quick start and links to the docs. Don't add detailed behavior, settings, how-tos or reference tables to it — those always go in `docs/`.
- **`docs/`** is plain GitHub Markdown: it must read correctly on github.com, so no MkDocs/Sphinx-only syntax — use GitHub alerts (`> [!WARNING]`) for callouts. `docs/README.md` is the index; link every new page from it. Link between pages with relative `.md` links and GitHub heading anchors, and check anchors still exist when renaming headings.
- `docs/quickstart.md` covers project setup (install script, first login, first fetch/download). Add pages per topic as features land (e.g. authentication, configuration, download backends, writing a new backend).
- When a CLI flag, config option or behavior changes, update the relevant docs page and the usage list in this file in the same PR.

## Tests

pytest, in `tests/` (commands under **Commands**). Tests must never hit Spotify or download real audio — fake the Spotify layer and the download backend. `tests/conftest.py` has `FakeHttp` (queued responses per pathfinder operation name; an unknown operation gets 412), `FakeTokens` (with swappable query hashes) and builders for pathfinder JSON; `tests/test_cli.py` swaps `cli.make_client`, `cli.interactive_login` and `ui.choose_playlists`. `tests/test_browser_auth.py` drives real headless Chromium but routes every request to fake Spotify pages (and aborts anything else) through the `configure_context` hook of `interactive_login`/`refresh_session`; the login window is replaced via `open_browser` by a fake user in a headless browser with a DevTools port, run from a separate thread, who "logs in" and gets redirected to the web player. Use only obviously fake data (`dummy-token`, `Fake Artist`).

## Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/) for commits, branch names and PR titles. Work is done on branches and merged via PR.

- **Types:** `feat` (new user-facing behavior), `fix` (bug fix), `docs` (README/`docs/`/CLAUDE.md/comments only), `refactor` (no behavior change), `perf`, `test`, `build` (dependencies, install scripts), `chore` (anything else). Pick the type for the change's main effect; split unrelated changes into separate commits.
- **Scope:** the feature area, lowercase, e.g. `auth`, `fetch`, `setlist`, `download`, `backend`, `somedl`, `meta`, `config`, `cli`, `ui`, `update`, `setup`. Reuse scopes already in `git log` rather than inventing near-duplicates.
- **Commit messages:** `type(scope): summary` — imperative mood, lowercase after the colon, no trailing period, subject ≤ 72 characters, e.g. `feat(fetch): add --noselect flag`. Use the body to explain *why* when it isn't obvious. Breaking changes (config format, setlist/session file layout, CLI flags) get `!` after the scope (`feat(config)!: ...`) and a `BREAKING CHANGE:` footer.
- **Branch names:** `type/scope-short-description`, lowercase kebab-case, e.g. `feat/fetch-noselect-flag`, `fix/setlist-date-parsing`, `docs/readme-badges`. `EnterWorktree` creates branches named `worktree-<name>`; right after entering, rename it with `git branch -m <conventional-name>` before the first push.
- **PR titles:** the same `type(scope): summary` form as a commit; for a single-commit PR, reuse its subject. The title is what lands in the merge history, so it should describe the whole PR.
- **PR bodies:** a short summary of what changed and why, then how it was tested (tests run, manual checks). Call out breaking changes, format migrations and documentation updates.

**Worktrees:** in a git worktree (e.g. under `.claude/worktrees/`), symlink the project root's virtualenv instead of creating a new one — from the worktree root: `ln -s "$(git rev-parse --path-format=absolute --git-common-dir)/../.venv" .venv`. The symlink must never be checked in; `.gitignore` should list `.venv` without a trailing slash so it matches the symlink as well as a directory. **Never run `pip install -e .` (or `pip install .`) from a worktree:** it repoints the shared virtualenv's `spotarchive` at the worktree, and once the worktree is removed the user's `spotarchive` command fails with `ModuleNotFoundError`. Tests don't need it (`pythonpath = ["src"]` in `pyproject.toml` makes pytest import the worktree's code); run the CLI from a worktree with `PYTHONPATH=src python -m spotarchive`. If a dependency is missing, install just that package (`pip install <name>`), not the project.

**Finishing a task:** a task isn't done until it's in a PR. Commit on your branch, push it, and open a PR against `main` with `gh pr create` (title and body as above). Never push to `main` directly or merge the PR yourself.

**Merge conflicts:** before opening the PR (and again if `main` moves while it's open), `git fetch origin` and merge `origin/main` into your branch. Resolve any conflicts so both your change and the incoming work are kept — don't just take one side — re-run the tests, commit the merge and push. Confirm with `gh pr view --json mergeable` that the PR reports `MERGEABLE`; if it's `CONFLICTING`, repeat. Other agents may work in parallel worktrees, so expect `main` to have moved, especially in shared files like `CLAUDE.md` and `README.md`.
