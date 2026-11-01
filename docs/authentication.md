# Authentication

Spotarchive logs in to Spotify through a real browser window, the same way you would log in to the web player. It never sees or stores your password. A [YouTube login](#logging-in-to-youtube-optional) works the same way and is optional: it lets age-restricted songs download.

## Logging in

```bash
spotarchive --login spotify    # or: spotarchive -l spotify
spotarchive --login youtube    # optional, see below
spotarchive --login            # asks which site
```

Without a site, `--login` shows a list to pick Spotify or YouTube from (arrow keys, enter). When there's no terminal to ask in (e.g. in a script), name the site.

1. A browser window opens at Spotify's login page.
2. Log in as usual (email, phone, Google, Facebook, Apple or a one-time code all work), solving a captcha if Spotify asks for one.
3. Once Spotify sends the window on to the web player (`open.spotify.com`), Spotarchive reads the login from it, closes the window by itself and saves the login. There's no need to close it yourself.

For Spotify, the login helper waits up to 10 minutes for you to log in. Closing the window before logging in cancels the login. If you open the web player without being logged in, the window stays open so you can still log in from there.

Running plain `spotarchive` checks the saved login first and only opens the browser when there is no working login.

The login window is an ordinary browser window with a profile of its own (it doesn't touch your normal browser profile or its logins). Spotarchive doesn't control it while you log in: automated browsers get stuck in endless captchas on Spotify's login page. It only asks the window, through a local DevTools port, which page it's on; it connects to the window to read the cookies once you've reached the web player. The profile is kept, so Spotify recognizes the device the next time you log in.

## Logging in to YouTube (optional)

The download helper finds songs on YouTube. YouTube only plays some of them (mostly ones marked age-restricted) to a logged-in, age-verified account, so without a login those songs fail. Logging in fixes that:

```bash
spotarchive --login youtube    # or: spotarchive -l youtube
```

1. A browser window opens at Google's sign-in page for YouTube.
2. Sign in with the Google account you use on YouTube.
3. Once Google sends the window on to YouTube, Spotarchive reads the youtube.com cookies from it, closes the window by itself and saves the login.

It uses the same kind of window as the Spotify login (see [which browser is used](#which-browser-is-used)), with a separate profile, and waits up to 10 minutes as well.

Plain `spotarchive` offers the YouTube login after the Spotify one whenever there's no working YouTube login (none saved, or the saved one has expired):

```text
? Log in to YouTube too? Some age-restricted songs only download with a login.
 » Yes, log in now
   No, not now
   No, and don't ask again
```

- **Yes, log in now** opens the login window. If the login fails or you close the window, the run goes on without it.
- **No, not now** asks again on the next run.
- **No, and don't ask again** stops asking. Run `spotarchive --login youtube` whenever you want to log in; after that, Spotarchive asks again if that login ever expires.

The question is only asked in a terminal, and only by plain `spotarchive`, never by `--fetch`, `--download` or `--meta`.

Spotarchive itself never talks to YouTube with the login. It hands the cookies to the [download backend](backends.md#youtube-login), which passes them to its helper (SomeDL passes them on to yt-dlp). YouTube doesn't say when a login stops working, so Spotarchive only notices a login has expired from the cookies' expiry dates. If age-restricted songs keep failing while you're logged in, [log in again](#troubleshooting).

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
| Query hashes | Identifiers of the web player's API requests, read from its JavaScript; see [where the data comes from](fetching.md#where-the-data-comes-from). Not secret. |
| Your display name and user ID | Shown after logging in. |
| youtube.com cookies | Only with the [YouTube login](#logging-in-to-youtube-optional). The cookies that keep you logged in to YouTube, handed to the download helper. Google's and other sites' cookies are never saved. |
| YouTube user agent | The browser identity the YouTube cookies belong to. |
| Whether to offer the YouTube login | `false` once you've picked "don't ask again". |

On Linux the file is created readable only by your user (mode `600`).

The login windows' browser profiles are kept next to it, in `browser-profile/` for Spotify and `browser-profile-youtube/` for YouTube (default `data/browser-profile/` and `data/browser-profile-youtube/`, readable only by your user on Linux). They hold those windows' cookies, including the logins.

While downloading with a YouTube login, SomeDL gets the cookies in a temporary `cookies.txt` file, readable only by your user, that's deleted as soon as each song is done.

> [!WARNING]
> `session.json` and the `browser-profile*/` folders grant access to your Spotify and Google accounts. Never share or commit them; the repository's `.gitignore` already excludes the whole `data/` folder. To log out of both, delete them all. To log out of YouTube only, delete `browser-profile-youtube/` and set `"youtube"` to `null` in `session.json`.

## How tokens are refreshed

Spotify access tokens last about an hour. When the saved one expires, Spotarchive starts a hidden (headless) browser with the saved cookies, loads the web player, and keeps the fresh token the web player is issued. It also reads the current [query hashes](fetching.md#where-the-data-comes-from) from the web player's script. This takes a few seconds and needs no interaction.

Letting the real web player mint the token, instead of calling Spotify's token endpoint directly, keeps Spotarchive working when Spotify changes how that endpoint is signed.

If the cookies themselves have expired (Spotify only issues a logged-out token), you're asked to log in again. Plain `spotarchive` does this for you; `spotarchive --fetch` tells you to run `spotarchive --login spotify`.

## Troubleshooting

- **"Couldn't start a browser"**: run `python -m playwright install chromium` (on Linux, `python -m playwright install --with-deps chromium` also installs the system libraries Chromium needs), or install Chrome or Edge.
- **Captchas keep coming back, or "Something went wrong"** on the login page: wait a few minutes before trying again, and if it persists install Google Chrome, which Spotarchive prefers over Playwright's Chromium. Spotarchive never drives the login window, so this is Spotify being cautious about the network or a brand-new profile, not about automation.
- **"The browser was closed before the login finished"**: the window was closed before you were logged in; run `spotarchive --login spotify` (or `youtube`) again. If a browser window from a previous attempt is still open, close it first.
- **The window doesn't close after logging in**: Spotarchive waits for the window to reach `open.spotify.com`. If Spotify leaves you on an account page instead, open <https://open.spotify.com/> in the same window.
- **"The saved Spotify login has expired"**: run `spotarchive --login spotify`.
- **"unknown login site"**: `--login` takes `spotify` or `youtube`. Since it takes a site, it can't be combined with other short options in one group: write `spotarchive -l spotify -f`, not `-lf`.
- **Age-restricted songs fail although you're logged in to YouTube** (yt-dlp says the video is age-restricted, or asks you to sign in): the login may have been invalidated, for example because you logged out of that Google account on all devices or changed its password. Run `spotarchive --login youtube` again. The account also needs to be age-verified with Google.
- **The YouTube window doesn't close after signing in**: Spotarchive waits for the window to reach a YouTube page. If Google leaves you on an account page instead, open <https://www.youtube.com/> in the same window.
- **"Spotify is rate-limiting this login's requests"**: Spotify refused the request with `429`. Short waits (up to a minute) are retried automatically with a message for each wait. If Spotify asks for a longer wait, Spotarchive stops right away and says how long. The login is still saved, so run Spotarchive again later without logging in again.
- **"Spotify's web player no longer accepts the ... query"**: Spotify changed its web player in a way this version of Spotarchive doesn't handle; update Spotarchive. See [where the data comes from](fetching.md#where-the-data-comes-from).
- **"Can't read the session file"**: the file is damaged; delete `data/session.json` and log in again. Your playlist selection will need to be made again too.

Back to the [documentation index](README.md).
