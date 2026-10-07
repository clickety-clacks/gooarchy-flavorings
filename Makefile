# Install Gooarchy's flavorings into DESTDIR (a package build's root, or empty for the live system):
#
#   make install DESTDIR=<root> THEMES=<Scottland checkout>/omarchy/themes
#
# The Watercolor Dream themes live in Scottland's repository; THEMES points at the themes of the
# Scottland commit the distro builds, and each theme's Ghostty file is generated from its colors.
# The installed paths are fixed: the scripts refer to them directly.

DESTDIR ?=
THEMES ?=
THEME_NAMES = watercolor-dream-light watercolor-dream-dark
SHARE = $(DESTDIR)/usr/share/gooarchy-flavorings
SCOTTLAND = $(DESTDIR)/usr/lib/scottland

.PHONY: install
install:
	@test -n "$(THEMES)" || { echo "THEMES must name Scottland's omarchy/themes directory" >&2; exit 1; }
	install -Dm644 scottland/flavorings.ini "$(SHARE)/scottland.ini"
	install -Dm755 scottland/config.d/40-gooarchy-flavorings "$(SCOTTLAND)/config.d/40-gooarchy-flavorings"
	install -Dm755 scottland/autostart.d/40-gooarchy-wallpaper "$(SCOTTLAND)/autostart.d/40-gooarchy-wallpaper"
	install -Dm755 scottland/autostart.d/45-gooarchy-flavorings "$(SCOTTLAND)/autostart.d/45-gooarchy-flavorings"
	install -Dm755 scottland/accent.d/20-gooarchy-theme "$(SCOTTLAND)/accent.d/20-gooarchy-theme"
	install -Dm755 libexec/gooarchy-wallpaper "$(DESTDIR)/usr/lib/gooarchy-flavorings/gooarchy-wallpaper"
	install -Dm755 bin/gooarchy-theme "$(DESTDIR)/usr/bin/gooarchy-theme"
	install -Dm755 bin/gooarchy-flavorings-apply "$(DESTDIR)/usr/bin/gooarchy-flavorings-apply"
	install -Dm644 tmux/tmux.conf "$(DESTDIR)/etc/tmux.conf"
	install -Dm644 profile.d/gooarchy-flavorings.sh "$(DESTDIR)/etc/profile.d/gooarchy-flavorings.sh"
	install -Dm644 xdg/scottland-mimeapps.list "$(DESTDIR)/etc/xdg/scottland-mimeapps.list"
	set -e; for theme in $(THEME_NAMES); do \
	  from="$(THEMES)/$$theme"; to="$(SHARE)/themes/$$theme"; \
	  install -Dm644 "$$from/colors.toml" "$$to/colors.toml"; \
	  install -Dm644 "$$(readlink -f "$$from/preview.webp")" "$$to/background.webp"; \
	  python3 themes/ghostty-theme.py "$$from/colors.toml" >"$$to/ghostty"; chmod 644 "$$to/ghostty"; \
	done
