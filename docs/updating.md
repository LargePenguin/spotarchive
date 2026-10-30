# Updating

Spotarchive can update itself from GitHub, without git.

## Checking and updating

```bash
spotarchive --update   # or -u
```

This compares the installed version with the version on the `main` branch on GitHub. If GitHub has a newer one, Spotarchive updates itself right away. Otherwise it says it's up to date. `--update` runs on its own and can't be combined with other options.

## The check in a plain run

Running plain `spotarchive` checks for updates before anything else (logging in, fetching, downloading):

- If a newer version is available, it asks whether to install it. If you say yes, it updates and then runs the rest of the flow with the new version. If you say no, it continues with the installed version.
- If GitHub can't be reached, it prints a warning and continues.
- If the input isn't an interactive terminal (e.g. a scheduled job), it never updates on its own. It tells you to run `spotarchive --update`.

The single steps (`--login`, `--fetch`, `--download`) don't check for updates.

To skip the check, e.g. when you're offline, set the environment variable `SPOTARCHIVE_NO_UPDATE_CHECK=1`.

## How the update works

Spotarchive updates the checkout it runs from, which is what the [install script](quickstart.md#1-install) sets up. Git isn't needed. Updating:

1. downloads the `main` branch of the repository from GitHub as a zip file and writes every file in it over the same file in the checkout;
2. reinstalls the Python dependencies into the checkout's virtualenv, only if `pyproject.toml` changed. Installed extras (SomeDL, test dependencies) are kept. The install is editable, so the new code is used directly otherwise.

Your data folder, login and settings (`spotarchive.toml`) aren't touched, since they aren't part of the repository. Files you changed by hand *are* overwritten, and files that were removed from the repository since your version are left behind (harmless).

> [!NOTE]
> Only a change of the version number counts as an update. Changes merged on GitHub without a new version number aren't offered.

## When the update fails

- **"this install isn't an editable checkout"**: Spotarchive was installed some other way (e.g. `pip install` from a download). Run the [install script](quickstart.md#1-install) again to update.
- **"Couldn't download the update"** or a GitHub error: check your connection and try again.
- **Reinstalling the dependencies failed**: the code is already updated. Run the install script again to repair the install.

In a plain run, a failed update is reported as a warning, and the run continues with the installed version.

Back to the [documentation index](README.md).
