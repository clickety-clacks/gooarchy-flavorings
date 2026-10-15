# gooarchy-flavorings: contributor guide

Gooarchy flavorings on top of Scottland: which app widgets ship (and whose) and the app defaults the
distro chooses. Installed by the Gooarchy distro and by Scottland's Omarchy adapter (Omarchy has no
widget concept, so without this package Scottland on Omarchy is incomplete).

Scottland defines the core behavior and public interfaces this package follows. In short:

- **Mechanisms live in Scottland, flavorings live here.** Widgets here implement Scottland's public
  widget contract (`WG1`–`WG23`), as defined in
  [Scottland's widget docs](https://github.com/clickety-clacks/scottland/blob/main/docs/widgets.md).
  If a widget needs a capability that interface does not provide, propose the mechanism in
  Scottland core and keep app-specific code here.
- **Additions first.** Where Omarchy has no equivalent (widgets), these are additions. Override an
  Omarchy choice only where that gives a better core Gooarchy experience than Omarchy's binding.
- **No silent overrides.** Every override of something the user already had is listed in the
  adapter's override report with what it was, what it is now and why. Add the reason when adding an
  override.
- **Additive and removable** app tuning uses package-owned files and at most one marked include line.
- **Testing**: do not run acceptance checks on a developer's daily desktop. Use the designated test
  system with real input. Report completion only after that check.

## Branches and releases

Contributors follow these rules.

- Use `main` and version tags (`v0.x.y`). Gooarchy and the Scottland Omarchy adapter install this
  package pinned to a tag, so a change reaches users when a consumer updates its pin.
- Create a `0.x` release branch only when a change must ship with a specific Scottland release. Cut it
  from `main` and merge `main` into it whenever `main` changes.
- Feature branches are short-lived and deleted after merge. A PR lands only on a target tip it was
  tested against. Use a merge queue when enabled; otherwise update to the latest target tip and rerun
  checks immediately before merging. Merge after independent review.
- For a release, create the version tag and notify downstream package maintainers with the tag and its
  contents.

## Machine-specific configuration and privacy

Keep developer machine names, IP addresses, private network or domain names, usernames and home-directory
paths out of source, documentation, commits, fixtures and logs. Any machine address a component needs
must be configurable and supplied at install time, never hard-coded.
