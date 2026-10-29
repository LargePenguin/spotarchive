# Authentication

Spotarchive logs in to Spotify through a real browser window, the same way you would log in to the web player. It never sees or stores your password.

## Logging in

```bash
spotarchive --login
```

1. A browser window opens at Spotify's login page.
2. Log in as usual (email, phone, Google, Facebook, Apple or a one-time code all work), solving a captcha if Spotify asks for one.
3. Once Spotify sends the window on to the web player (`open.spotify.com`), Spotarchive reads the login from it, closes the window by itself and saves the login. There's no need to close it yourself.

The login helper waits up to 10 minutes for you to log in. Closing the window before logging in cancels the login. If you open the web player without being logged in, the window stays open so you can still log in from there.

Running plain `spotarchive` checks the saved login first and only opens the browser when there is no working login.

The login window is an ordinary browser window with a profile of its own (it doesn't touch your normal browser profile or its logins). Spotarchive doesn't control it while you log in: automated browsers get stuck in endless captchas on Spotify's login page. It only asks the window, through a local DevTools port, which page it's on; it connects to the window to read the cookies once you've reached the web player. The profile is kept, so Spotify recognizes the device the next time you log in.

## Which browser is used

For the login window, Spotarchive uses the first of these it finds:

1. Google Chrome
2. Microsoft Edge (preinstalled on Windows)
3. Playwright's bundled Chromium (`python -m playwright install chromium`)

Getting the first access token after logging in and [refreshing tokens](#how-tokens-are-refreshed) happen in a hidden (headless) browser started through [Playwright](https://playwright.dev/python/). Token refreshes try Playwright's bundled Chromium, then Chrome, then Edge; if none of them can be started, the error message lists why each one failed.

## What is stored

The login is saved in `session.json` at the root of the download folder (default `data/session.json`), together with your [playlist selection](fetching.md#choosing-playlists):

| Item | Why |
| --- | --- |
| spotify.com cookies | `sp_dc` is Spotify's long-lived login cookie; it's what keeps you logged in. Cookies from other sites are never saved. |
| User agent | Later token refreshes present the same browser identity you logged in with. |
| Access token and its expiry | The short-lived token the web player uses to read your library; reused until it expires. |
| Your display name and user ID | Shown after logging in. |

On Linux the file is created readable only by your user (mode `600`).

The login window's browser profile is kept next to it, in `browser-profile/` (default `data/browser-profile/`, readable only by your user on Linux). It holds that window's cookies, including the Spotify login.

> [!WARNING]
> `session.json` and `browser-profile/` grant access to your Spotify account. Never share or commit them; the repository's `.gitignore` already excludes the whole `data/` folder. To log out, delete both.

## How tokens are refreshed

Spotify access tokens last about an hour. When the saved one expires, Spotarchive starts a hidden (headless) browser with the saved cookies, loads the web player, and keeps the fresh token the web player is issued. This takes a few seconds and needs no interaction.

Letting the real web player mint the token, instead of calling Spotify's token endpoint directly, keeps Spotarchive working when Spotify changes how that endpoint is signed.

If the cookies themselves have expired (Spotify only issues a logged-out token), you're asked to log in again. Plain `spotarchive` does this for you; `spotarchive --fetch` tells you to run `spotarchive --login`.

## Troubleshooting

- **"Couldn't start a browser"**: run `python -m playwright install chromium` (on Linux, `python -m playwright install --with-deps chromium` also installs the system libraries Chromium needs), or install Chrome or Edge.
- **Captchas keep coming back, or "Something went wrong"** on the login page: wait a few minutes before trying again, and if it persists install Google Chrome, which Spotarchive prefers over Playwright's Chromium. Spotarchive never drives the login window, so this is Spotify being cautious about the network or a brand-new profile, not about automation.
- **"The browser was closed before the login finished"**: the window was closed before Spotify logged you in; run `spotarchive --login` again. If a browser window from a previous attempt is still open, close it first.
- **The window doesn't close after logging in**: Spotarchive waits for the window to reach `open.spotify.com`. If Spotify leaves you on an account page instead, open <https://open.spotify.com/> in the same window.
- **"The saved Spotify login has expired"**: run `spotarchive --login`.
- **"Can't read the session file"**: the file is damaged; delete `data/session.json` and log in again. Your playlist selection will need to be made again too.

Back to the [documentation index](README.md).
