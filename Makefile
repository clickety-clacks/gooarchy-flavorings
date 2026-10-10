# Install Gooarchy's flavorings into DESTDIR (a package build's root, or empty for the live system):
#
#   make install DESTDIR=<root>
#
# Each catalogue variant in themes/ installs as it is, links kept, at
# /usr/share/gooarchy-flavorings/themes/<variant>/. Beside it go two derived files: background.webp
# (a copy of its first background) and ghostty. The installed location is the one theme-path
# parameter used by the lookup and its integrations.

DESTDIR ?=
SHARE = $(DESTDIR)/usr/share/gooarchy-flavorings
THEME_SHARE = $(SHARE)/themes
SCOTTLAND = $(DESTDIR)/usr/lib/scottland
LICENSE_DIR = $(DESTDIR)/usr/share/licenses/gooarchy-flavorings

.PHONY: install test
install: validate-sudoers
	python3 libexec/gooarchy-theme-lookup --validate-catalogue themes
	install -Dm644 scottland/flavorings.ini "$(SHARE)/scottland.ini"
	install -Dm755 scottland/config.d/40-gooarchy-flavorings "$(SCOTTLAND)/config.d/40-gooarchy-flavorings"
	install -Dm755 scottland/autostart.d/40-gooarchy-wallpaper "$(SCOTTLAND)/autostart.d/40-gooarchy-wallpaper"
	install -Dm755 scottland/autostart.d/45-gooarchy-flavorings "$(SCOTTLAND)/autostart.d/45-gooarchy-flavorings"
	install -Dm755 scottland/accent.d/20-gooarchy-theme "$(SCOTTLAND)/accent.d/20-gooarchy-theme"
	install -Dm755 libexec/gooarchy-wallpaper "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-wallpaper"
	install -Dm755 libexec/gooarchy-theme-integrations "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-theme-integrations"
	install -Dm755 libexec/gooarchy-browser-policy "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-browser-policy"
	install -Dm440 etc/sudoers.d/90-gooarchy-browser-policy "$(DESTDIR)/etc/sudoers.d/90-gooarchy-browser-policy"
	install -Dm644 LICENSES/omarchy-browser-policy-MIT.txt "$(DESTDIR)/usr/share/licenses/gooarchy-flavorings/omarchy-browser-policy-MIT.txt"
	install -Dm755 libexec/gooarchy-theme-lookup "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-theme-lookup"
	install -Dm755 libexec/gooarchy-theme-refresh "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-theme-refresh"
	install -Dm755 bin/gooarchy-theme "$(DESTDIR)/usr/bin/gooarchy-theme"
	install -Dm755 bin/gooarchy-flavorings-apply "$(DESTDIR)/usr/bin/gooarchy-flavorings-apply"
	install -Dm644 tmux/tmux.conf "$(DESTDIR)/etc/tmux.conf"
	install -Dm644 profile.d/gooarchy-flavorings.sh "$(DESTDIR)/etc/profile.d/gooarchy-flavorings.sh"
	install -Dm644 xdg/scottland-mimeapps.list "$(DESTDIR)/etc/xdg/scottland-mimeapps.list"
	install -Dm644 licenses/CC0-1.0-Watercolor-Dream-themes-only.txt "$(LICENSE_DIR)/CC0-1.0-Watercolor-Dream-themes-only.txt"
	set -e; for from in themes/*-light themes/*-dark; do \
	  [ -d "$$from" ] || continue; theme=$$(basename "$$from"); to="$(THEME_SHARE)/$$theme"; \
	  install -d "$$to"; cp -RP "$$from/." "$$to/"; \
	  background=$$(python3 libexec/gooarchy-theme-lookup --first-background "$$from") || exit $$?; \
	  install -Dm644 "$$background" "$$to/background.webp"; \
	  python3 themes/ghostty-theme.py "$$from/colors.toml" >"$$to/ghostty"; chmod 644 "$$to/ghostty"; \
	done

.PHONY: validate-sudoers
validate-sudoers:
	visudo -cf etc/sudoers.d/90-gooarchy-browser-policy

.PHONY: test
test:
	python3 tests/flavorings-apply-test.py
	python3 tests/theme-integrations-test.py
	bash -n libexec/gooarchy-browser-policy
