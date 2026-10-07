# Mike's rulings affecting this repository

## Sunlight is transition-only, not a timer (dr_0e88648d, 2026-10-07)

Mike's words: "Sunlight is a standalone package, installed on this machine and included and
installed by default in Gooarchy. Sunlight must change the theme only when day becomes night or
night becomes day; it must not repeatedly enforce the current day/night theme on a timer. Manual
theme changes remain respected until the next day/night transition. This ruling specifies
transition-only behavior, not an indefinite manual override until automatic is selected again."

`gooarchy-theme`'s header comment and the note it prints after `light`, `dark` and `toggle` now
describe this: with Sunlight on, a manually picked mode holds until the next sunrise or sunset,
then Sunlight switches it. Sunlight itself (standalone, outside this repository) is what
implements the transition-only switch; this repository only reads whether Sunlight is on from
`enabled` in `~/.config/scottland/solar.ini`.
