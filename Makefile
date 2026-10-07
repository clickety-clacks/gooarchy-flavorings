# Install Gooarchy's flavorings into DESTDIR (a package build's root, or empty for the live system):
#
#   make install DESTDIR=<root>
#
# Each Watercolor Dream theme installs as its Omarchy-format directory, plus the two files Gooarchy's
# scripts read: background.webp (the background preview.webp links to) and ghostty, generated from
# the theme's colors. The installed paths are fixed: the scripts refer to them directly.

DESTDIR ?=
THEME_NAMES = watercolor-dream-light watercolor-dream-dark
SHARE = $(DESTDIR)/usr/share/gooarchy-flavorings
SCOTTLAND = $(DESTDIR)/usr/lib/scottland

.PHONY: install
install:
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
	  from="themes/$$theme"; to="$(SHARE)/themes/$$theme"; \
	  install -Dm644 -t "$$to" "$$from/colors.toml" "$$from/icons.theme" "$$from/.aether-managed"; \
	  install -Dm644 -t "$$to/backgrounds" "$$from"/backgrounds/*; \
	  ln -sfn "$$(readlink "$$from/preview.webp")" "$$to/preview.webp"; \
	  install -Dm644 "$$(readlink -f "$$from/preview.webp")" "$$to/background.webp"; \
	  python3 themes/ghostty-theme.py "$$from/colors.toml" >"$$to/ghostty"; chmod 644 "$$to/ghostty"; \
	done
