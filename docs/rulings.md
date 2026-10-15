# Product behavior

## Watercolor Dream theme notice

Date: 2026-10-07

The notice applies only to the `themes/watercolor-dream-light/` and
`themes/watercolor-dream-dark/` theme directories and their installed copies. It does not license
Gooarchy flavorings code. The installed notice is
`/usr/share/licenses/gooarchy-flavorings/CC0-1.0-Watercolor-Dream-themes-only.txt`.

## Sunlight changes themes only at day/night transitions

Date: 2026-10-07

Sunlight is a standalone package, installed by default with Gooarchy. It changes the theme only when
day becomes night or night becomes day; it does not repeatedly enforce the current theme on a timer.
A manual theme change remains in effect until the next day/night transition. It is not an indefinite
manual override that requires selecting automatic again.

The `gooarchy-theme` and `gooarchy-flavorings-apply` notices describe this: with Sunlight on, a
manually picked mode holds until the next sunrise or sunset, then Sunlight switches it. Sunlight
implements the transition-only switch; this package only reads whether Sunlight is on from
`enabled` in `~/.config/scottland/solar.ini`.

## Strata and Chromium follow Watercolor Dream

On Gooarchy, the session theme switch keeps Strata's single `gooarchy-watercolor` custom theme entry
matched to Watercolor Dream light or dark. Flavorings changes Strata's saved theme only when its
selection is missing or still Strata's first-run `tokyo-night` default. A different selected theme
and every other saved Strata setting stay untouched. Strata reads the updated theme on its next
launch; flavorings never kills or restarts it.

Chromium's managed `BrowserThemeColor` comes from Watercolor Dream's accent, while
`BrowserColorScheme = "device"` follows the system's light/dark choice. The privileged writer accepts
only one six-digit color and writes only `/etc/chromium/policies/managed/gooarchy-theme.json`; it
does not create policy files when Chromium is absent. A validated sudoers rule grants only that
writer and color argument. A running Chromium receives `--refresh-platform-policy` without restart.
Flavorings does not rewrite the existing per-user frame preference.

On Omarchy, the existing Omarchy marker makes both integrations inert so Omarchy retains its own
Strata and browser appearance. The policy-writer design derives from Omarchy's MIT-licensed browser
policy integration; its copyright and permission notice ship under `LICENSES/`.

## Ghostty theme source

On the Omarchy adapter, Ghostty reads its theme from Omarchy. On Gooarchy, Ghostty reads the
corresponding theme from Scottland.

## Text size

Date: 2026-10-07

The shell, GTK apps and terminals use one text size. Flavorings uses the user's existing
`org.gnome.desktop.interface text-scaling-factor`; it sets no default and writes no text-size value
or per-app copy. Scottland core owns the control that changes this setting and applies it to running
apps.
