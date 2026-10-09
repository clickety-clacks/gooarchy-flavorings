# gooarchy-flavorings: agent guide

Gooarchy flavorings on top of Scottland: which app widgets ship (and whose) and the app defaults the
distro chooses. Installed by the Gooarchy distro and by Scottland's Omarchy adapter (Omarchy has no
widget concept, so without this package Scottland on Omarchy is incomplete).

Scottland's repo (clickety-clacks/scottland, private) is the source of the rules this package follows:
its AGENTS.md, core/INVARIANTS.md (principles P1-P10, especially P9 and P10) and omarchy/INVARIANTS.md
(O20, O21). In short:

- **Mechanisms live in Scottland, flavorings live here.** Widgets here are built only on Scottland's public
  widget interface (docs/widgets.md, WG1-WG23). If a widget needs something the interface lacks, the
  change goes to Scottland core as a generic mechanism, never as app-specific code there.
- **Additions first.** Where Omarchy has no equivalent (widgets), these are additions. Override an
  Omarchy choice only where that gives a better core Gooarchy experience than Omarchy's binding.
- **No silent overrides.** Every override of something the user already had is listed in the
  adapter's override report with what it was, what it is now and why (O20). Adding an override means
  adding its reason.
- **Additive and removable** app tuning: own files plus at most one marked include line (Scottland C7).
- **Testing**: never on osanwe (Mike's daily machine); test on plumbus with real input. Report done
  only after that.

## Branches and releases (Mike, 2026-10-08)

Every agent working in this repository follows this.

- `main` plus version tags (`v0.x.y`). Gooarchy and the Scottland Omarchy adapter install this package
  pinned to a tag, so a change reaches users only when a consumer moves its pin.
- A release branch (`0.x`) exists only when a change must ship together with a specific Scottland
  release; cut it from `main` and merge `main` into it whenever `main` changes.
- Feature branches are short-lived and deleted after merge. A PR lands only on a target tip it was tested
  against (merge queue once the Gooarchy PDO sets it up; until then, update to the tip and rerun the checks
  right before merging), after review by the other harness.
- Shipping: tag, then tell the consumers' delivery owner (the Gooarchy PDO) the tag and its contents.

## Privacy of developer networks (Mike, 2026-10-04)

Never reveal the internal topology or identifiers of any developer's network here (host names, IPs, tailnet
or domain names, user names, home paths), in code, docs, commits, fixtures or logs. If a component needs to
reach a machine, its address is a configurable field set at install time, never a constant.
