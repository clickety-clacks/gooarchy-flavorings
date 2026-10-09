# Rulings

## Watercolor Dream theme notice

Date: 2026-10-07
Decision: `dr_2c31e1d4`
Mike's exact ruling: "cc0"

The notice applies only to the `themes/watercolor-dream-light/` and
`themes/watercolor-dream-dark/` theme directories and their installed copies. It does not license
Gooarchy flavorings code. The installed notice is
`/usr/share/licenses/gooarchy-flavorings/CC0-1.0-Watercolor-Dream-themes-only.txt`.

## Sunlight is transition-only, not a timer

Date: 2026-10-07
Decision: `dr_0e88648d`
Mike's exact ruling: "Sunlight is a standalone package, installed on this machine and included and
installed by default in Gooarchy. Sunlight must change the theme only when day becomes night or
night becomes day; it must not repeatedly enforce the current day/night theme on a timer. Manual
theme changes remain respected until the next day/night transition. This ruling specifies
transition-only behavior, not an indefinite manual override until automatic is selected again."

The `gooarchy-theme` and `gooarchy-flavorings-apply` notices describe this: with Sunlight on, a
manually picked mode holds until the next sunrise or sunset, then Sunlight switches it. Sunlight
itself (standalone, outside this repository) implements the transition-only switch; this repository
only reads whether Sunlight is on from `enabled` in `~/.config/scottland/solar.ini`.
