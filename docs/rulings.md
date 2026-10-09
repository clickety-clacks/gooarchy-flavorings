# Rulings

## Watercolor Dream theme notice

Date: 2026-10-07
Decision: `dr_2c31e1d4`
Mike's exact ruling: "cc0"

The notice applies only to the `themes/watercolor-dream-light/` and
`themes/watercolor-dream-dark/` theme directories and their installed copies. It does not license
Gooarchy flavorings code. The installed notice is
`/usr/share/licenses/gooarchy-flavorings/CC0-1.0-Watercolor-Dream-themes-only.txt`.

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
