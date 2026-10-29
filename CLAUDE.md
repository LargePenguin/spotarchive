# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Spotarchive is a cross-platform (Linux and Windows) Python CLI that caches metadata about a user's Spotify playlists into per-playlist `setlist.csv` files, then legally sources the songs through an abstracted download backend (default: [SomeDL](https://github.com/ChemistryGull/SomeDL), which pulls from YouTube/Deezer/etc.). YT Music support is planned. The full product spec is in `.tmp/spec.md`; the user-facing documentation lives in `docs/` (see **Documentation** below) — keep it in sync when behavior changes; `README.md` stays a short overview.

> [!IMPORTANT]
> **Privacy boundary — never cross it.** Never include personally identifying or sensitive information in source code, tests, documents (README, `docs/`, comments, fixtures, sample data), commit messages, or PR/changelist descriptions. This covers the computer hostname, usernames and home-directory paths, email addresses, IP/MAC addresses, secrets, API keys, passwords, session tokens, cookies, `x-bc` values, user agents captured from a real session, Spotify account/user IDs and real playlist contents. Use obvious placeholders (`<your-username>`, `example.com`, `dummy-token`) and fake data in tests and examples. Session/credential files must be gitignored and never committed. Before committing or opening a PR, review the diff and descriptions for such leaks.

## Status

The repo is at the start of the development plan: only `LICENSE`, `README.md`, `.gitignore`, `CLAUDE.md` and the `docs/` stubs (`docs/README.md`, `docs/quickstart.md`) exist; no code yet. Update this file (especially **Commands** and **Architecture**) as code lands — don't leave it describing things that don't exist, and don't invent commands before they work.

Development plan order: (1) `CLAUDE.md`, (2) `docs/`, minimal `README.md` (ASCII-art name + status badges) and quickstart, (3) Spotify features, (4) download-backend features, (5) metadata cleanup, setup script and config file.

## Commands

To be filled in as they are created. Planned interface (see `.tmp/spec.md` for details):

```bash
spotarchive                  # full flow: update check -> login (if needed) -> fetch -> download -> meta
spotarchive -l | --login     # interactive browser login helper
spotarchive -f | --fetch     # choose playlists and rebuild setlist.csv files; -fn / --fetch --noselect reuses previous selection
spotarchive -d | --download  # download songs listed in the setlists via the backend
spotarchive -m | --meta      # metadata cleanup (combinable: -dm)
spotarchive -u | --update    # compare against the GitHub version and update in place
spotarchive -h | --help
```

Plain `spotarchive` checks for updates first (prompting before installing) and runs the login helper first if there is no working login. A single-command install script will set up dependencies and the project.

## Architecture (planned)

- **Spotify layer** — browser-based auth (launch a browser at Spotify's login, capture the session via cookie, user agent and `x-bc`) and playlist/track metadata fetching. Session info and playlist include/exclude choices are cached in a file at the root of the data folder.
- **Setlist** — each selected playlist gets a subfolder in the configurable download folder (default `data/` under the current working directory, e.g. `data/MyPlaylistName/`) containing `setlist.csv` (name, artist, album, release date, date added).
- **Download backend** — an interface that the rest of the code talks to; SomeDL (CLI) is the default implementation. Keep all SomeDL-specific code behind that interface so contributors can swap in another helper. Smart downloading: before requesting downloads, skip songs already present in the folder.
- **Metadata correction** — use the helper's own metadata when it supports it (e.g. `somedl import`), otherwise fall back to `setlist.csv`.
- **Configuration** — a config file in the project root: download folder, which backend to use, and per-backend settings (output format, metadata source of truth, folder structure, ...).
- **UI** — colored CLI output, checkbox selection for playlists, progress bars. When SomeDL is the backend its own output can serve as the download progress display.

Everything must work on both Linux and Windows: use `pathlib`, avoid shell-specific assumptions, and don't hardcode path separators.

## Documentation

- **`README.md` stays minimal**: an ASCII-art rendering of the tool's name, the usual badges (Python version, downloads, stars, last commit, ...), what the project is, a short feature list, a quick start and links to the docs. Don't add detailed behavior, settings, how-tos or reference tables to it — those always go in `docs/`.
- **`docs/`** is plain GitHub Markdown: it must read correctly on github.com, so no MkDocs/Sphinx-only syntax — use GitHub alerts (`> [!WARNING]`) for callouts. `docs/README.md` is the index; link every new page from it. Link between pages with relative `.md` links and GitHub heading anchors, and check anchors still exist when renaming headings.
- `docs/quickstart.md` covers project setup (install script, first login, first fetch/download). Add pages per topic as features land (e.g. authentication, configuration, download backends, writing a new backend).
- When a CLI flag, config option or behavior changes, update the relevant docs page and the usage list in this file in the same PR.

## Tests

None yet. When tests are added, they must never hit Spotify or download real audio — fake the Spotify layer and the download backend. Document the test commands under **Commands**.

## Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/) for commits, branch names and PR titles. Work is done on branches and merged via PR.

- **Types:** `feat` (new user-facing behavior), `fix` (bug fix), `docs` (README/`docs/`/CLAUDE.md/comments only), `refactor` (no behavior change), `perf`, `test`, `build` (dependencies, install scripts), `chore` (anything else). Pick the type for the change's main effect; split unrelated changes into separate commits.
- **Scope:** the feature area, lowercase, e.g. `auth`, `fetch`, `setlist`, `download`, `backend`, `somedl`, `meta`, `config`, `cli`, `ui`, `update`, `setup`. Reuse scopes already in `git log` rather than inventing near-duplicates.
- **Commit messages:** `type(scope): summary` — imperative mood, lowercase after the colon, no trailing period, subject ≤ 72 characters, e.g. `feat(fetch): add --noselect flag`. Use the body to explain *why* when it isn't obvious. Breaking changes (config format, setlist/session file layout, CLI flags) get `!` after the scope (`feat(config)!: ...`) and a `BREAKING CHANGE:` footer.
- **Branch names:** `type/scope-short-description`, lowercase kebab-case, e.g. `feat/fetch-noselect-flag`, `fix/setlist-date-parsing`, `docs/readme-badges`. `EnterWorktree` creates branches named `worktree-<name>`; right after entering, rename it with `git branch -m <conventional-name>` before the first push.
- **PR titles:** the same `type(scope): summary` form as a commit; for a single-commit PR, reuse its subject. The title is what lands in the merge history, so it should describe the whole PR.
- **PR bodies:** a short summary of what changed and why, then how it was tested (tests run, manual checks). Call out breaking changes, format migrations and documentation updates.

**Worktrees:** in a git worktree (e.g. under `.claude/worktrees/`), symlink the project root's virtualenv instead of creating a new one — from the worktree root: `ln -s "$(git rev-parse --path-format=absolute --git-common-dir)/../.venv" .venv`. The symlink must never be checked in; `.gitignore` should list `.venv` without a trailing slash so it matches the symlink as well as a directory.

**Finishing a task:** a task isn't done until it's in a PR. Commit on your branch, push it, and open a PR against `main` with `gh pr create` (title and body as above). Never push to `main` directly or merge the PR yourself.

**Merge conflicts:** before opening the PR (and again if `main` moves while it's open), `git fetch origin` and merge `origin/main` into your branch. Resolve any conflicts so both your change and the incoming work are kept — don't just take one side — re-run the tests, commit the merge and push. Confirm with `gh pr view --json mergeable` that the PR reports `MERGEABLE`; if it's `CONFLICTING`, repeat. Other agents may work in parallel worktrees, so expect `main` to have moved, especially in shared files like `CLAUDE.md` and `README.md`.
