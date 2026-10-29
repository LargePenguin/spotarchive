# Authentication

Spotarchive logs in to Spotify through a real browser window, the same way you would log in to the web player. It never sees or stores your password.

## Logging in

```bash
spotarchive --login
```

1. A browser window opens at Spotify's login page.
2. Log in as usual (email, phone, Google, Facebook, Apple or a one-time code all work).
3. Once Spotify's web player loads, Spotarchive captures the session and closes the window.

The login helper waits up to 10 minutes. Closing the window cancels it.

Running plain `spotarchive` checks the saved login first and only opens the browser when there is no working login.

> [!TIP]
> If the browser lands somewhere other than the web player after logging in, just wait: Spotarchive opens `open.spotify.com` itself about 10 seconds after it sees you're logged in.

## Which browser is used

Spotarchive drives the browser through [Playwright](https://playwright.dev/python/) and tries, in order:

1. Playwright's bundled Chromium (`python -m playwright install chromium`)
2. Google Chrome
3. Microsoft Edge (preinstalled on Windows)

If none of them can be started, the error message lists why each one failed.

## What is stored

The login is saved in `session.json` at the root of the download folder (default `data/session.json`), together with your [playlist selection](fetching.md#choosing-playlists):

| Item | Why |
| --- | --- |
| spotify.com cookies | `sp_dc` is Spotify's long-lived login cookie; it's what keeps you logged in. Cookies from other sites are never saved. |
| User agent | Later token refreshes present the same browser identity you logged in with. |
| Access token and its expiry | The short-lived token the web player uses to read your library; reused until it expires. |
| Your display name and user ID | Shown after logging in. |

On Linux the file is created readable only by your user (mode `600`).

> [!WARNING]
> `session.json` grants access to your Spotify account. Never share it or commit it; the repository's `.gitignore` already excludes it and the whole `data/` folder. To log out, delete the file.

## How tokens are refreshed

Spotify access tokens last about an hour. When the saved one expires, Spotarchive starts a hidden (headless) browser with the saved cookies, loads the web player, and keeps the fresh token the web player is issued. This takes a few seconds and needs no interaction.

Letting the real web player mint the token, instead of calling Spotify's token endpoint directly, keeps Spotarchive working when Spotify changes how that endpoint is signed.

If the cookies themselves have expired (Spotify only issues a logged-out token), you're asked to log in again. Plain `spotarchive` does this for you; `spotarchive --fetch` tells you to run `spotarchive --login`.

## Troubleshooting

- **"Couldn't start a browser"**: run `python -m playwright install chromium` (on Linux, `python -m playwright install --with-deps chromium` also installs the system libraries Chromium needs), or install Chrome or Edge.
- **"The saved Spotify login has expired"**: run `spotarchive --login`.
- **"Can't read the session file"**: the file is damaged; delete `data/session.json` and log in again. Your playlist selection will need to be made again too.

Back to the [documentation index](README.md).
