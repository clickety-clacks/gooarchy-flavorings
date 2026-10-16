#!/usr/bin/env python3
"""Focused behavior checks for Gooarchy wallpaper and installed monospace choices."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="gooarchy-look-wallpaper-font-test-")
CONFIG = Path(TEMP.name) / "config"
CONFIG.mkdir()
os.environ["XDG_CONFIG_HOME"] = str(CONFIG)
os.environ["OMARCHY_PATH"] = str(Path(TEMP.name) / "omarchy-not-installed")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


wallpaper = load("gooarchy_wallpaper", ROOT / "libexec" / "gooarchy-wallpaper")
integrations = load("gooarchy_theme_integrations", ROOT / "libexec" / "gooarchy-theme-integrations")
refresh = load("gooarchy_theme_refresh", ROOT / "libexec" / "gooarchy-theme-refresh")
failures = 0


def expect(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}")


def result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def configure_wallpaper(root):
    config = root / "config"
    wallpaper.CONFIG_HOME = config
    wallpaper.PREFERENCES = config / "gooarchy" / "wallpapers.json"
    wallpaper.CURRENT_THEME = config / "gooarchy" / "current-theme"
    wallpaper.REFRESH = root / "gooarchy-theme-refresh"
    wallpaper.INTEGRATIONS = root / "gooarchy-theme-integrations"
    wallpaper._omarchy_owns_appearance = lambda: False
    return config


with tempfile.TemporaryDirectory(prefix="gooarchy-wallpaper-state-") as temporary:
    root = Path(temporary)
    config = configure_wallpaper(root)
    variant = root / "themes" / "plain-light"
    backgrounds = variant / "backgrounds"
    backgrounds.mkdir(parents=True)
    (backgrounds / "a.webp").write_bytes(b"a")
    (backgrounds / "b.png").write_bytes(b"b")
    files, selected, state, saved = wallpaper.wallpaper_state(variant)
    expect("the first installed background is the default",
           [item.name for item in files] == ["a.webp", "b.png"]
           and selected.name == "a.webp" and state == "default" and saved is None)

    preferences = {str(variant): "b.png"}
    files, selected, state, saved = wallpaper.wallpaper_state(variant, preferences)
    expect("a choice belongs to its resolved variant", selected.name == "b.png"
           and state == "explicit" and saved == "b.png")
    files, selected, state, saved = wallpaper.wallpaper_state(
        variant, {str(variant): "removed.jpg"})
    expect("a missing saved image visibly falls back to the default",
           selected.name == "a.webp" and state == "saved-unavailable" and saved == "removed.jpg")

    listed = []
    list_errors = []
    code = wallpaper.dispatch(
        ["list"],
        output=type("Out", (), {"write": lambda self, text: listed.append(text)})(),
        error=type("Err", (), {"write": lambda self, text: list_errors.append(text)})(),
        run=lambda command, **kwargs: result(stdout=f"{variant}\n"),
    )
    expect("wallpaper list prints exact filenames for the numbered picker",
           code == 0 and "".join(listed) == "a.webp\nb.png\n" and not list_errors)

    output = []
    errors = []
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=True), \
            patch.object(wallpaper, "applied_wallpaper", return_value=str(backgrounds / "b.png")):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(),
        )
    saved = json.loads((config / "gooarchy" / "wallpapers.json").read_text())
    expect("set persists by variant and reports the acknowledged live image",
           code == 0 and saved == {str(variant): "b.png"}
           and "Saved and applied wallpaper: b.png" in "".join(output))

    prior = (config / "gooarchy" / "wallpapers.json").read_bytes()
    code = wallpaper.set_wallpaper(
        variant, "../outside.png", type("Out", (), {"write": lambda self, text: None})(),
        type("Err", (), {"write": lambda self, text: None})(),
        run=lambda command, **kwargs: result(),
    )
    expect("invalid wallpaper input leaves the saved choice unchanged",
           code == 2 and (config / "gooarchy" / "wallpapers.json").read_bytes() == prior)

    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True):
        code = wallpaper.set_wallpaper(
            variant, "a.webp", type("Out", (), {"write": lambda self, text: None})(),
            type("Err", (), {"write": lambda self, text: None})(),
            run=lambda command, **kwargs: result(),
        )
    expect("Omarchy ownership refuses a wallpaper write", code == 1
           and (config / "gooarchy" / "wallpapers.json").read_bytes() == prior)

with tempfile.TemporaryDirectory(prefix="gooarchy-wallpaper-apply-report-") as temporary:
    root = Path(temporary)
    variant = root / "plain-dark"
    backgrounds = variant / "backgrounds"
    backgrounds.mkdir(parents=True)
    image = backgrounds / "b.png"
    image.write_bytes(b"b")
    configure_wallpaper(root)
    output = []
    errors = []
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=False), \
            patch.object(wallpaper, "applied_wallpaper", return_value=None):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(),
        )
    expect("without a running owner the result says saved for next login, not live success",
           code == 0 and "will apply when the wallpaper owner starts" in "".join(output)
           and "Saved and applied" not in "".join(output))

    output.clear()
    errors.clear()
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=True), \
            patch.object(wallpaper, "applied_wallpaper", return_value=None):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(),
        )
    expect("an active owner without an applied-image acknowledgement is unconfirmed",
           code == 1 and "live application is unconfirmed" in "".join(errors)
           and "will apply when the wallpaper owner starts" not in "".join(output))

    output.clear()
    errors.clear()
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=False), \
            patch.object(wallpaper, "applied_wallpaper", return_value=None):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(stderr="refresh failed", returncode=1),
        )
    expect("refresh failure distinguishes saved choice from live failure",
           code == 1 and "Saved wallpaper: b.png" in "".join(errors)
           and "refresh failed" in "".join(errors))

expect("refresh accepts the extended applied-image acknowledgement",
       refresh._wallpaper_ack_token('{"token":"abc","applied":"/wall/a.webp"}') == "abc")
expect("refresh remains compatible with its former token-only acknowledgement",
       refresh._wallpaper_ack_token("abc\n") == "abc")
expect("refresh confirms only an ack whose applied and requested images match",
       refresh._wallpaper_ack_application_confirmed(
           '{"token":"abc","applied":"/wall/a.webp","requested":"/wall/a.webp"}')
       and not refresh._wallpaper_ack_application_confirmed(
           '{"token":"abc","applied":null,"requested":"/wall/a.webp"}')
       and not refresh._wallpaper_ack_application_confirmed(
           '{"token":"abc","applied":"/wall/old.webp","requested":"/wall/new.webp"}')
       and not refresh._wallpaper_ack_application_confirmed("abc\n"))


class WallpaperChild:
    def __init__(self, failure=False):
        import threading
        self._gooarchy_image_load_failure = threading.Event()
        self._gooarchy_image_load_error = []
        self.terminated = False
        if failure:
            self._gooarchy_image_load_error.append("Failed to load image: broken.png")
            self._gooarchy_image_load_failure.set()

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


old_wallpaper = WallpaperChild()
failed_candidate = WallpaperChild(failure=True)
try:
    wallpaper.replace_wallpaper(old_wallpaper, Path("broken.png"),
                                start=lambda _image: failed_candidate)
except RuntimeError as exception:
    expect("decode failure leaves the working wallpaper owner alive",
           not old_wallpaper.terminated and failed_candidate.terminated
           and "Failed to load image" in str(exception))
else:
    expect("decode failure leaves the working wallpaper owner alive", False,
           "replacement unexpectedly succeeded")

old_wallpaper = WallpaperChild()
unconfirmed_candidate = WallpaperChild()
replacement, confirmed = wallpaper.replace_wallpaper(
    old_wallpaper, Path("slow.png"), start=lambda _image: unconfirmed_candidate,
    wait=lambda process, image: wallpaper.wait_for_swaybg_start(
        process, image, timeout=0),
)
expect("an unconfirmed replacement keeps the transition attempt and previous process without claiming success",
       replacement is unconfirmed_candidate and not confirmed
       and not old_wallpaper.terminated and not unconfirmed_candidate.terminated)

late_failure_candidate = WallpaperChild()
failure_before = wallpaper.swaybg_load_failure(late_failure_candidate)
late_failure_candidate._gooarchy_image_load_error.append(
    "Failed to load image: slow-decode.png")
late_failure_candidate._gooarchy_image_load_failure.set()
expect("an image-load error arriving after the initial probe remains observable",
       failure_before is None
       and wallpaper.swaybg_load_failure(late_failure_candidate)
       == "Failed to load image: slow-decode.png"
       and wallpaper.swaybg_process_failure(late_failure_candidate)
       == "Failed to load image: slow-decode.png")

expect("swaybg's decode and image-read failures are recognized",
       wallpaper._swaybg_load_error("Failed to load background image (decode error)")
       and wallpaper._swaybg_load_error("Failed to read background image: invalid PNG")
       and not wallpaper._swaybg_load_error("Found config * for output DP-1"))

with tempfile.TemporaryDirectory(prefix="gooarchy-font-inventory-") as temporary:
    root = Path(temporary)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "+list-fonts" in command:
            return result(stdout="Fira Code\n  Fira Code Regular\nIBM Plex Mono\n  IBM Plex Mono Bold\n")
        return result(stdout="Fira Code\t100\nIBM Plex Mono\t100\nProportional Sans\t0\n")

    with patch.object(integrations.shutil, "which", side_effect=lambda name: f"/tools/{name}"):
        families = integrations.font_inventory(run=run)
    expect("font choices are the intersection of installed monospace and Ghostty families",
           families == ["Fira Code", "IBM Plex Mono"] and len(calls) == 2, families)

with tempfile.TemporaryDirectory(prefix="gooarchy-font-write-") as temporary:
    root = Path(temporary)
    config = root / "config"
    ghostty_dir = config / "ghostty"
    ghostty_dir.mkdir(parents=True)
    user_config = ghostty_dir / "config"
    original = b"font-size = 13\r\nbackground = '#123456'\r\n"
    user_config.write_bytes(original)
    integrations.CONFIG = config
    integrations.OMARCHY = False
    output = []
    errors = []
    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]), \
            patch.object(integrations, "ghostty_configured_family",
                         side_effect=[("Monospace", None), ("Fira Code", None)]), \
            patch.object(integrations, "fontconfig_effective_family", return_value="Fira Code"), \
            patch.object(integrations, "reload_ghostty",
                         return_value=(True, "no Ghostty process was found in this graphical session")), \
            patch.object(integrations.shutil, "which", return_value="/tools/ghostty"):
        code = integrations.set_font(
            "fira code", output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    fontconfig = config / "fontconfig" / "conf.d" / "99-gooarchy-monospace.conf"
    ghostty_fragment = config / "gooarchy" / "font.ghostty"
    parsed = ElementTree.parse(fontconfig).getroot()
    fragment = ghostty_fragment.read_text()
    expect("font set writes valid owned fragments and preserves the existing config bytes",
           code == 0 and parsed.findtext(".//edit/string") == "Fira Code"
           and fragment == 'font-family = ""\nfont-family = "Fira Code"\n'
           and user_config.read_bytes().startswith(original)
           and user_config.read_bytes().count(integrations.GHOSTTY_INCLUDE_MARKER.encode()) == 1
           and "Saved monospace family: Fira Code" in "".join(output), errors)

    expect("the marked Ghostty include is idempotent",
           integrations._ghostty_include(user_config.read_bytes().decode("utf-8"), ghostty_fragment)
           == user_config.read_bytes().decode("utf-8"))

with tempfile.TemporaryDirectory(prefix="gooarchy-font-crlf-rollback-") as temporary:
    config = Path(temporary) / "config"
    ghostty_dir = config / "ghostty"
    ghostty_dir.mkdir(parents=True)
    user_config = ghostty_dir / "config"
    original = b"# user config\r\nfont-size = 13\r\nbackground = '#123456'\r\n"
    user_config.write_bytes(original)
    integrations.CONFIG = config
    integrations.OMARCHY = False
    real_atomic_write = integrations.atomic_write
    failed_after_config_write = {"value": False}

    def fail_after_config_write(path, content, mode=0o600):
        real_atomic_write(path, content, mode=mode)
        if Path(path) == user_config and not failed_after_config_write["value"]:
            failed_after_config_write["value"] = True
            raise OSError("simulated failure after Ghostty config replacement")

    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]), \
            patch.object(integrations, "ghostty_configured_family",
                         side_effect=[("Monospace", None), ("Fira Code", None)]), \
            patch.object(integrations, "fontconfig_effective_family", return_value="Fira Code"), \
            patch.object(integrations, "reload_ghostty", return_value=(True, "no session client")), \
            patch.object(integrations.shutil, "which", return_value="/tools/ghostty"), \
            patch.object(integrations, "atomic_write", side_effect=fail_after_config_write):
        errors = []
        code = integrations.set_font(
            "Fira Code", output=type("Out", (), {"write": lambda self, text: None})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    expect("font rollback restores CRLF Ghostty config bytes exactly",
           code == 1 and failed_after_config_write["value"] and user_config.read_bytes() == original,
           errors)

with tempfile.TemporaryDirectory(prefix="gooarchy-font-no-write-") as temporary:
    root = Path(temporary)
    config = root / "config"
    integrations.CONFIG = config
    integrations.OMARCHY = False
    output = []
    errors = []
    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]):
        code = integrations.set_font(
            "Missing Family", output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    expect("an unavailable font is rejected before any configuration write",
           code == 2 and not config.exists())

    integrations.OMARCHY = True
    with patch.object(integrations, "font_inventory") as inventory:
        code = integrations.set_font(
            "Fira Code", output=type("Out", (), {"write": lambda self, text: None})(),
            error=type("Err", (), {"write": lambda self, text: None})(),
        )
    expect("Omarchy ownership refuses a font write before probing or writing",
           code == 1 and not config.exists() and not inventory.called)

with tempfile.TemporaryDirectory(prefix="gooarchy-font-current-error-") as temporary:
    integrations.CONFIG = Path(temporary) / "config"
    integrations.OMARCHY = False
    output = []
    errors = []
    with patch.object(integrations, "fontconfig_effective_family",
                      side_effect=RuntimeError("Fontconfig is unavailable")):
        code = integrations.font_current(
            output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    expect("font current reports an unreadable Fontconfig state without a traceback",
           code == 1 and "Font state could not be read" in "".join(errors)
           and not output)

print(f"{failures} failure(s)")
raise SystemExit(1 if failures else 0)
