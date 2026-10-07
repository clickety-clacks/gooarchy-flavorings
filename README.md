# Gooarchy flavorings

Gooarchy flavorings on top of Scottland: which app widgets you get (and whose), plus the app defaults
the distro chooses. Installed by the Gooarchy distro and by Scottland's Omarchy adapter.

Scottland defines what a widget is; this package chooses which ones ship. Where Omarchy has no
equivalent concept (widgets, for example), these are additions, not overrides. Flavorings override an
Omarchy choice only where that gives a better core Gooarchy experience than Omarchy's binding, and
every override is reported to the user with its reason when it's installed (Scottland's Omarchy
adapter, O20/O21). App tuning is additive and removable (Scottland C7).

This repository is the source of record for Gooarchy's flavorings; they used to live in the gooarchy
repository's `flavorings/` directory, and their history came with them. The Watercolor Dream themes
came the same way from Scottland's `omarchy/themes/`.

## What's here

| Path | What | Installed as |
|---|---|---|
| `scottland/flavorings.ini` | Touchpad, file manager and browser keys for Scottland | `/usr/share/gooarchy-flavorings/scottland.ini` |
| `scottland/config.d/`, `autostart.d/`, `accent.d/` | Scottland hooks: append the config above, start the wallpaper and the per-user defaults, give halos the theme's accent | `/usr/lib/scottland/<hook dir>/` |
| `bin/gooarchy-theme` | Switch between the light and dark theme | `/usr/bin/gooarchy-theme` |
| `bin/gooarchy-flavorings-apply` | Fill in per-user defaults, once each, only where nothing is set | `/usr/bin/gooarchy-flavorings-apply` |
| `libexec/gooarchy-wallpaper` | The theme's wallpaper, following light and dark | `/usr/lib/gooarchy-flavorings/gooarchy-wallpaper` |
| `libexec/gooarchy-theme-integrations` | One Strata Watercolor theme entry and Chromium managed color, updated with the session color-scheme | `/usr/lib/gooarchy-flavorings/gooarchy-theme-integrations` |
| `libexec/gooarchy-browser-policy` | Root-owned writer for Chromium's fixed managed-policy path | `/usr/lib/gooarchy-flavorings/gooarchy-browser-policy` |
| `etc/sudoers.d/90-gooarchy-browser-policy` | Passwordless permission for the color-only writer | `/etc/sudoers.d/90-gooarchy-browser-policy` (installed only after `visudo` validation) |
| `themes/watercolor-dream-light/`, `watercolor-dream-dark/` | The Watercolor Dream themes as complete Omarchy theme directories, exactly as Scottland had them | each directory as it is, links kept, at `/usr/share/gooarchy-flavorings/themes/<theme>/`, with `background.webp` and `ghostty` generated from the theme beside it |
| `themes/ghostty-theme.py` | Builds a Ghostty theme from a theme's `colors.toml` (at install) | not installed |
| `tmux/tmux.conf`, `profile.d/`, `xdg/` | tmux titles, mosh titles, default apps | `/etc/tmux.conf`, `/etc/profile.d/`, `/etc/xdg/` |
| `tests/flavorings-apply-test.py` | Tests `gooarchy-flavorings-apply` in throwaway home directories | not installed |
| `tests/theme-integrations-test.py` | Tests Strata preservation, Chromium policy refresh, Omarchy inertness and staged install | not installed |
| `LICENSES/omarchy-browser-policy-MIT.txt` | Retained notice for the Omarchy-derived Chromium policy integration | `/usr/share/licenses/gooarchy-flavorings/omarchy-browser-policy-MIT.txt` |

## On Omarchy

Installed by Scottland's Omarchy adapter, the flavorings keep Omarchy's own look and keys:

- The look stays Omarchy's. The wallpaper hook starts nothing, and `gooarchy-flavorings-apply` neither
  sets the color scheme nor touches Chromium or Ghostty (those steps aren't marked as applied either).
- Super+Shift+F and Super+Shift+B stay Omarchy's file manager and browser: the config hook leaves
  those two bindings out. The touchpad and screenshot settings are appended as on Gooarchy.
- Everything else that takes effect over what the user had is listed in Scottland's override report
  (O20), with what it was and what it is now: the Claude Code and Codex bells Gooarchy set (while
  they're still set that way), the tmux and mosh titles where the user's own config doesn't set
  them, and each default app from `/etc/xdg/scottland-mimeapps.list` that wins over the one the
  user had. The list is a per-user fragment,
  `~/.config/scottland/override-report.d/gooarchy-flavorings.txt`, which the config hook and
  `gooarchy-flavorings-apply` bring up to date (rewriting it only when it changes, removing it when
  nothing is left). Strata opens folders only where it's installed; without it, folders open as
  before and the report doesn't mention it.

Omarchy is recognized by its directory, `$OMARCHY_PATH` or `/usr/share/omarchy`.

## Installing and packaging

    make install DESTDIR=<root>

A package builds from a pinned commit of this repository by running that command into its package
root. It needs make, Python and `visudo`; installation validates the narrowly scoped sudoers rule
before placing it in the package root. The package's runtime dependencies and backup files stay with
the package recipe. The installed paths are fixed because the scripts refer to them directly.

## Testing

    make test

It needs Python and a POSIX shell: the desktop commands it calls, and Omarchy, are stand-ins. The
tests use temporary homes and policy directories; they do not invoke the privileged writer or modify
a live browser profile. `make test` also checks the writer's shell syntax and the sudoers rule with
`visudo`.

## Choices so far
- File browser: [Strata](https://github.com/lgse/strata) (Mike, 2026-10-04), installed and set as the file manager.
