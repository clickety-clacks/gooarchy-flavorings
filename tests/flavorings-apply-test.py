#!/usr/bin/env python3
"""Test gooarchy-flavorings-apply in throwaway home directories, with stand-ins for the desktop
commands it calls (gsettings, dconf, xdg-user-dirs-update, pgrep). No desktop, no real settings.

  tests/flavorings-apply-test.py      exits 1 if anything fails

Covered: existing private files keep mode 0600 (and their contents); new private files are created
0600; a symlinked config is left alone and reported; an app that's running is deferred; an
explicitly set color scheme (even "default") is kept; an interrupted write leaves the old file and
no temporary file; two runs at once leave valid files. Theme refresh waits for the running wallpaper
helper to redraw the selected background before returning. The package install places the unchanged
scoped notice at its expected path and preserves installed theme entries verbatim to their source.
Both setup/theme Sunlight notices say a manual theme pick leaves Sunlight on, holds through periodic
checks, and changes only at the next scheduled transition; turning Sunlight off keeps one mode
permanently.

On Omarchy (a stand-in OMARCHY_PATH): the wallpaper hook starts nothing; the color scheme, Chromium
and Ghostty are neither written nor marked; the config hook leaves out Super+Shift+F and B and adds
nothing else; the override report fragment lists only what changed (the bells Gooarchy set, tmux
and mosh titles the user doesn't set, default apps that win over the previous one, Strata only when
installed), is rewritten only when it changes and is removed when nothing is left. The Gooarchy
cases run with no Omarchy, whatever the test machine has. Text-size guards cover setup, theme
refresh, light/dark switching, source themes, derived Ghostty files and existing app settings under
the R12 Omarchy directory guard.
"""
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr
from io import StringIO

for name in ("OMARCHY_PATH", "SCOTTLAND_HOOKS", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY"):
    os.environ.pop(name, None)

# Keep default Gooarchy fixtures independent of whether the test host has Omarchy installed.
_test_environment = tempfile.TemporaryDirectory(prefix="gooarchy-flavorings-test-")
TEST_MISSING_OMARCHY = Path(_test_environment.name) / "omarchy-not-installed"
os.environ["OMARCHY_PATH"] = str(TEST_MISSING_OMARCHY)

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tool = os.path.join(repo, "bin", "gooarchy-flavorings-apply")
theme_tool = os.path.join(repo, "bin", "gooarchy-theme")
failures = 0


GIO_STUB = "#!" + sys.executable + "\n" + r'''import os
import pathlib
import sys

mime = sys.argv[2]
desktop_names = [f"{desktop.lower()}-mimeapps.list" for desktop in
                 os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if desktop]
config_dirs = [pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")]
config_dirs.extend(pathlib.Path(path) for path in
                   (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if path)
data_dirs = [pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local/share")]
data_dirs.extend(pathlib.Path(path) for path in
                 (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if path)
application_dirs = [directory / "applications" for directory in data_dirs]

def desktop_exists(desktop):
    return any((directory / desktop).is_file() for directory in application_dirs)

def defaults(path):
    section = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Default Applications]" and "=" in line and not line.startswith("#"):
            key, values = line.split("=", 1)
            if key.strip() == mime:
                return [value for value in values.split(";") if value and desktop_exists(value)]
    return []

for directory in config_dirs + application_dirs:
    for name in desktop_names + ["mimeapps.list"]:
        apps = defaults(directory / name)
        if apps:
            print(f'Default application for "{mime}": {apps[0]}')
            sys.exit(0)

for directory in application_dirs:
    section = None
    try:
        lines = (directory / "mimeinfo.cache").read_text(errors="replace").splitlines()
    except OSError:
        continue
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[MIME Cache]" and "=" in line and not line.startswith("#"):
            key, values = line.split("=", 1)
            if key.strip() == mime:
                apps = [value for value in values.split(";") if value and desktop_exists(value)]
                if apps:
                    print(f'Default application for "{mime}": {apps[0]}')
                    sys.exit(0)

print(f"No default application for {mime}")
'''


def expect(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}")


def stubs(bindir, dconf_value="", running=""):
    os.makedirs(bindir, exist_ok=True)
    home = os.path.dirname(bindir)
    Path(home, ".test-dconf-color-scheme").write_text(dconf_value)
    scripts = {
        "gsettings": """#!/bin/sh
echo "$@" >>"$HOME/gsettings.log"
case "$1:$2:$3" in
  get:org.gnome.desktop.interface:text-scaling-factor)
    if [ -f "$HOME/.test-text-scaling-factor" ]; then cat "$HOME/.test-text-scaling-factor"; else echo 1.0; fi ;;
  set:org.gnome.desktop.interface:text-scaling-factor)
    printf '%s' "$4" >"$HOME/.test-text-scaling-factor" ;;
esac
""",
        "dbus-run-session": "#!/bin/sh\nshift; exec \"$@\"\n",
        "dconf": """#!/bin/sh
echo "$@" >>"$HOME/dconf.log"
if [ "$1" = read ]; then
  case "$2" in
    /org/gnome/desktop/interface/color-scheme) cat "$HOME/.test-dconf-color-scheme" ;;
    /org/gnome/desktop/interface/text-scaling-factor)
      if [ -f "$HOME/.test-text-scaling-factor" ]; then cat "$HOME/.test-text-scaling-factor"; fi ;;
  esac
elif [ "$1" = write ] && [ "$2" = /org/gnome/desktop/interface/text-scaling-factor ]; then
  printf '%s' "$3" >"$HOME/.test-text-scaling-factor"
fi
""",
        "xdg-user-dirs-update": "#!/bin/sh\nmkdir -p \"$HOME/Pictures\"\n",
        "pgrep": f"#!/bin/sh\nfor a; do case \" {running} \" in *\" $a \"*) exit 0 ;; esac; done\nexit 1\n",
        "gio": GIO_STUB,
    }
    for name, text in scripts.items():
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)


def environment(home, omarchy=None):
    """Gooarchy (no Omarchy) unless OMARCHY is a stand-in Omarchy directory."""
    return {"HOME": home, "PATH": f"{os.path.join(home, '.stubs')}:/usr/bin:/bin",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",
            "OMARCHY_PATH": str(omarchy if omarchy is not None else TEST_MISSING_OMARCHY),
            "XDG_CONFIG_DIRS": os.path.join(home, ".sys", "etc-xdg"),
            "XDG_DATA_DIRS": os.path.join(home, ".sys", "share")}


def apply(home, *command_args, omarchy=None, omarchy_path=None, args=None, **kw):
    if command_args and args is not None:
        raise TypeError("pass command arguments positionally or with args=, not both")
    bindir = os.path.join(home, ".stubs")
    stubs(bindir, **kw)
    command_args = command_args if args is None else args
    selected_omarchy = omarchy if omarchy_path is None else omarchy_path
    return subprocess.run([sys.executable, tool, *command_args], env=environment(home, selected_omarchy),
                          capture_output=True, text=True)


def theme(home, mode):
    stubs(str(Path(home) / ".stubs"))
    bindir = Path(home) / ".theme-test-bin"
    bindir.mkdir(exist_ok=True)
    for name, source in (("python3", sys.executable),
                         ("gsettings", str(Path(home) / ".stubs" / "gsettings"))):
        if not (bindir / name).exists():
            (bindir / name).symlink_to(source)
    for name in ("dirname", "sed", "head"):
        source = shutil.which(name)
        if source and not (bindir / name).exists():
            (bindir / name).symlink_to(source)
    env = environment(home)
    env["PATH"] = str(bindir)
    return subprocess.run([os.path.join(repo, "bin", "gooarchy-theme"), mode], env=env,
                          capture_output=True, text=True)


def text_size_writes(home):
    calls = []
    for name in ("gsettings.log", "dconf.log"):
        path = os.path.join(home, name)
        if os.path.exists(path):
            calls.extend(Path(path).read_text().splitlines())
    return [line for line in calls
            if line.startswith("set org.gnome.desktop.interface text-scaling-factor")
            or line.startswith("write /org/gnome/desktop/interface/text-scaling-factor")]


def theme_size_settings():
    found = []
    for root, _, files in os.walk(os.path.join(repo, "themes")):
        for name in files:
            path = os.path.join(root, name)
            if name.endswith(".webp"):
                continue
            with open(path, errors="replace") as source:
                for number, line in enumerate(source, 1):
                    lower = line.lower()
                    if any(key in lower for key in ("font-size", "text-size", "text-scaling-factor")):
                        found.append(f"{path}:{number}: {line.rstrip()}")
    return found


def text_size_value(home):
    path = Path(home) / ".test-text-scaling-factor"
    return path.read_text() if path.exists() else ""


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


lookup = load_module("theme_lookup", os.path.join(repo, "libexec", "gooarchy-theme-lookup"))
refresh = load_module("theme_refresh", os.path.join(repo, "libexec", "gooarchy-theme-refresh"))


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def write_minimal_colors(variant):
    required = (
        "accent", "background", "lighter_background", "foreground", "light_foreground",
        "bright_foreground", "muted", "selection", "selection_foreground", "red", "green",
        "yellow", "blue", "magenta", "cyan", "bright_red", "bright_green", "bright_yellow",
        "bright_blue", "bright_magenta", "bright_cyan",
    )
    (variant / "colors.toml").write_text(
        "".join(f'{key} = "#112233"\n' for key in required)
    )


# The resolver is the only reader of the theme selection record and follows portal precedence.
with tempfile.TemporaryDirectory() as temp:
    empty_selection = Path(temp) / "missing"
    expect("an absent selection record defaults to Watercolor Dream",
           lookup.selected_slug(empty_selection) == "watercolor-dream")
    empty_selection.write_text("  \n")
    expect("an empty selection record defaults to Watercolor Dream",
           lookup.selected_slug(empty_selection) == "watercolor-dream")
    selected = Path(temp) / "theme"
    selected.write_text("gone\nlight\n")
    warning = StringIO()
    with redirect_stderr(warning):
        fallback = lookup.lookup_variant("dark", Path(repo) / "themes", selected)
    expect("an invalid selection warns and falls back without changing the record",
           fallback == (Path(repo) / "themes" / "watercolor-dream-dark").resolve()
           and "gone" in warning.getvalue() and selected.read_text() == "gone\nlight\n")

    calls = []

    def portal_light(command, **kwargs):
        calls.append(command[0])
        if command[0] == "gdbus":
            return completed(command, stdout="(uint32 2,)")
        return completed(command, stdout="prefer-dark")

    expect("portal light mode takes precedence over GNOME dark mode",
           lookup.current_mode(portal_light) == "light" and calls == ["gdbus"])

    def portal_dark(command, **kwargs):
        if command[0] == "gdbus":
            return completed(command, stdout="(uint32 1,)")
        return completed(command, stdout="prefer-light")

    expect("a portal value other than 2 means dark",
           lookup.current_mode(portal_dark) == "dark")

    def no_portal_gnome_light(command, **kwargs):
        return completed(command, 1) if command[0] == "gdbus" else completed(command, stdout="prefer-light")

    expect("GSettings light is used when the portal does not answer",
           lookup.current_mode(no_portal_gnome_light) == "light")

    def no_desktop(command, **kwargs):
        raise FileNotFoundError(command[0])

    expect("dark is the fallback when neither mode source is readable",
           lookup.current_mode(no_desktop) == "dark")
    expect("the checked-in catalogue satisfies the pair and first-WebP rule",
           lookup.validate_catalogue(Path(repo) / "themes") == 0)

    temporary_catalogue = Path(temp) / "themes"
    required = "\n".join(f'{key} = "#123456"' for key in lookup.REQUIRED_KEYS) + "\n"
    for suffix in ("light", "dark"):
        variant = temporary_catalogue / f"plain-{suffix}"
        (variant / "backgrounds").mkdir(parents=True)
        (variant / "colors.toml").write_text(required)
        (variant / "backgrounds" / "z.webp").write_bytes(b"z")
        (variant / "backgrounds" / "a.webp").write_bytes(b"a")
    selected = Path(temp) / "selected-theme"
    selected.write_text("plain\n")
    expect("a newly added valid theme pair needs no integration-specific catalogue entry",
           lookup.validate_theme("plain", temporary_catalogue)[1] is None
           and lookup.lookup_variant("dark", temporary_catalogue, selected)
           == (temporary_catalogue / "plain-dark").resolve()
           and selected.read_text() == "plain\n")
    for suffix in ("light", "dark"):
        variant = temporary_catalogue / f"other-{suffix}"
        (variant / "backgrounds").mkdir(parents=True)
        (variant / "colors.toml").write_text(required)
        (variant / "backgrounds" / "first.webp").write_bytes(b"other")
    real_validate_theme = lookup.validate_theme
    validated_slugs = []

    def switch_selection_after_validation(slug, theme_dirs, require_webp=True):
        validated_slugs.append(slug)
        result = real_validate_theme(slug, theme_dirs, require_webp)
        selected.write_text("other\n")
        return result

    lookup.validate_theme = switch_selection_after_validation
    try:
        pair = lookup.lookup_variants(temporary_catalogue, selected)
    finally:
        lookup.validate_theme = real_validate_theme
    expect("pair lookup uses one selection snapshot for both modes",
           pair == {"light": (temporary_catalogue / "plain-light").resolve(),
                    "dark": (temporary_catalogue / "plain-dark").resolve()}
           and validated_slugs == ["plain"] and selected.read_text() == "other\n")
    cli_config = Path(temp) / "cli-config"
    cli_selection = cli_config / "gooarchy" / "theme"
    cli_selection.parent.mkdir(parents=True)
    cli_selection.write_text("watercolor-dream\n")
    cli_env = os.environ.copy()
    cli_env["XDG_CONFIG_HOME"] = str(cli_config)
    pair_result = subprocess.run(
        [sys.executable, os.path.join(repo, "libexec", "gooarchy-theme-lookup"), "--pair"],
        env=cli_env, capture_output=True, text=True,
    )
    try:
        cli_pair = json.loads(pair_result.stdout)
    except json.JSONDecodeError:
        cli_pair = None
    expect("lookup CLI prints both selected absolute variants as one pair",
           pair_result.returncode == 0 and cli_pair == {
               "light": str((Path(repo) / "themes" / "watercolor-dream-light").resolve()),
               "dark": str((Path(repo) / "themes" / "watercolor-dream-dark").resolve()),
           })
    expect("the first background is selected by byte order of its filename",
           lookup.background_files(temporary_catalogue / "plain-light")[0].name == "a.webp")

    temporary_catalogue = Path(temp) / "invalid-themes"
    for suffix in ("light", "dark"):
        variant = temporary_catalogue / f"broken-{suffix}"
        (variant / "backgrounds").mkdir(parents=True)
        (variant / "colors.toml").write_text(required)
        (variant / "backgrounds" / "a.png").write_bytes(b"png")
        (variant / "backgrounds" / "z.webp").write_bytes(b"webp")
    diagnostics = StringIO()
    with redirect_stderr(diagnostics):
        invalid = lookup.validate_catalogue(temporary_catalogue)
    expect("catalogue validation names a variant whose first background is not WebP",
           invalid == 1 and "broken-light" in diagnostics.getvalue()
           and "first background a.png is not .webp" in diagnostics.getvalue())

    missing_pair_catalogue = Path(temp) / "missing-partner-themes"
    orphan = missing_pair_catalogue / "orphan-light"
    (orphan / "backgrounds").mkdir(parents=True)
    (orphan / "colors.toml").write_text(required)
    (orphan / "backgrounds" / "a.webp").write_bytes(b"webp")
    diagnostics = StringIO()
    with redirect_stderr(diagnostics):
        invalid = lookup.validate_catalogue(missing_pair_catalogue)
    expect("catalogue validation names a missing partner variant",
           invalid == 1 and "orphan-dark" in diagnostics.getvalue()
           and "variant directory is missing" in diagnostics.getvalue())


# The refresh replaces both links atomically and leaves its reserved location with exactly two links.
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    current = root / "config" / "gooarchy" / "current-theme"
    lookup_path = Path(repo) / "libexec" / "gooarchy-theme-lookup"
    selection = root / "config" / "gooarchy" / "theme"
    selection.parent.mkdir(parents=True)
    selection.write_text("plain\n")
    targets = {"light": "/themes/plain-light", "dark": "/themes/plain-dark"}
    calls = []

    def fake_lookup(command, **kwargs):
        calls.append(tuple(command))
        return completed(command, stdout=json.dumps(targets) + "\n")

    refresh.refresh(current, lookup_path, fake_lookup)
    expect("refresh creates the selected light/dark links and no other entry",
           sorted(path.name for path in current.iterdir()) == ["dark", "light"]
           and os.readlink(current / "light") == targets["light"]
           and os.readlink(current / "dark") == targets["dark"])
    targets.update(light="/themes/other-light", dark="/themes/other-dark")
    real_replace = os.replace
    observed_destinations = []

    def observed_replace(source, destination):
        destination = Path(destination)
        if destination.parent == current:
            # The links intentionally point outside this fixture, so exists() follows them and
            # reports False. is_symlink() observes the existing link entry itself.
            observed_destinations.append(destination.is_symlink())
        return real_replace(source, destination)

    os.replace = observed_replace
    try:
        refresh.refresh(current, lookup_path, fake_lookup)
    finally:
        os.replace = real_replace
    expect("refresh atomically replaces each existing link from lookup output",
           os.readlink(current / "light") == targets["light"]
           and os.readlink(current / "dark") == targets["dark"]
           and sorted(path.name for path in current.iterdir()) == ["dark", "light"]
           and not [path for path in current.parent.iterdir() if path.name.endswith(".tmp")]
           and observed_destinations == [True, True]
           and selection.read_text() == "plain\n"
           and calls == [(str(lookup_path), "--pair")] * 2)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    current = root / "config" / "gooarchy" / "current-theme"
    current.mkdir(parents=True)
    user_file = current / "notes"
    user_file.write_text("keep this file\n")

    def valid_lookup(command, **kwargs):
        return completed(command, stdout=json.dumps({
            "light": "/themes/plain-light", "dark": "/themes/plain-dark",
        }))

    try:
        refresh.refresh(current, Path(repo) / "libexec" / "gooarchy-theme-lookup", valid_lookup)
        refused = False
    except RuntimeError as error:
        refused = "unexpected current-theme entry: notes" in str(error)
    expect("refresh refuses unexpected current-theme contents without changing them",
           refused and user_file.read_text() == "keep this file\n"
           and not (current / "light").exists() and not (current / "dark").exists())

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    current = root / "config" / "gooarchy" / "current-theme"
    calls = []

    def incomplete_lookup(command, **kwargs):
        calls.append(tuple(command))
        return completed(command, returncode=1, stderr="catalogue unavailable")

    try:
        refresh.refresh(current, Path(repo) / "libexec" / "gooarchy-theme-lookup", incomplete_lookup)
        refused = False
    except RuntimeError as error:
        refused = "theme lookup failed" in str(error)
    expect("refresh fails before creating its location when pair lookup fails",
           refused and calls == [(str(Path(repo) / "libexec" / "gooarchy-theme-lookup"), "--pair")]
           and not current.exists()
           and [path.name for path in current.parent.iterdir()] == [".current-theme-refresh.lock"])

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    current = root / "config" / "gooarchy" / "current-theme"
    parent = current.parent
    parent.mkdir(parents=True)
    selection = parent / "theme"
    selection.write_text("old\n")
    lock_path = parent / ".current-theme-refresh.lock"
    first_lookup_started = threading.Event()
    release_first_lookup = threading.Event()
    second_lookup_started = threading.Event()
    second_lock_attempted = threading.Event()
    first_finished = threading.Event()
    second_finished = threading.Event()
    errors = []

    def race_lookup(command, **kwargs):
        slug = selection.read_text().strip()
        if threading.current_thread().name == "refresh-first":
            first_lookup_started.set()
            if not release_first_lookup.wait(5):
                raise RuntimeError("test timed out waiting to release first lookup")
        else:
            second_lookup_started.set()
        return completed(command, stdout=json.dumps({
            "light": f"/themes/{slug}-light", "dark": f"/themes/{slug}-dark",
        }))

    real_flock = fcntl.flock

    def observe_second_lock(fd, operation):
        if threading.current_thread().name == "refresh-second" and operation & fcntl.LOCK_EX:
            second_lock_attempted.set()
        return real_flock(fd, operation)

    refresh.fcntl.flock = observe_second_lock

    def run_refresh(done):
        try:
            refresh.refresh(current, Path(repo) / "libexec" / "gooarchy-theme-lookup", race_lookup)
        except Exception as error:
            errors.append(str(error))
        finally:
            done.set()

    first = threading.Thread(name="refresh-first", target=run_refresh, args=(first_finished,))
    second = threading.Thread(name="refresh-second", target=run_refresh, args=(second_finished,))
    probe_fd = None
    probe_holds_lock = False
    first_holds_lock_during_lookup = False
    newer_lookup_waited_for_first_commit = False
    try:
        first.start()
        first_lookup_started_ok = first_lookup_started.wait(5)
        if first_lookup_started_ok:
            probe_fd = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                probe_holds_lock = True
            except BlockingIOError:
                first_holds_lock_during_lookup = True

        selection.write_text("new\n")
        second.start()
        second_lock_seen = second_lock_attempted.wait(5)
        newer_lookup_waited_for_first_commit = not second_lookup_started.is_set()

        if probe_holds_lock:
            real_flock(probe_fd, fcntl.LOCK_UN)
            probe_holds_lock = False
            second_finished.wait(5)
        release_first_lookup.set()
        first.join(5)
        second.join(5)
    finally:
        release_first_lookup.set()
        if probe_holds_lock and probe_fd is not None:
            real_flock(probe_fd, fcntl.LOCK_UN)
        if probe_fd is not None:
            os.close(probe_fd)
        refresh.fcntl.flock = real_flock
        if first.is_alive():
            first.join(5)
        if second.is_alive():
            second.join(5)

    final_targets = {
        mode: os.readlink(current / mode) if (current / mode).is_symlink() else None
        for mode in ("light", "dark")
    }
    expect("refresh serializes pair resolution and commit so an older run cannot overwrite a newer selection",
           first_lookup_started_ok and second_lock_seen and first_holds_lock_during_lookup
           and newer_lookup_waited_for_first_commit and first_finished.is_set()
           and second_finished.is_set() and not errors
           and final_targets == {"light": "/themes/new-light", "dark": "/themes/new-dark"},
           errors)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    omarchy = root / "omarchy"
    omarchy.mkdir()
    refresh_home = root / "refresh-home"
    refresh_home.mkdir()
    stubs(str(refresh_home / ".stubs"))
    (refresh_home / ".test-text-scaling-factor").write_text("1.25")
    refresh_ghostty = refresh_home / ".config" / "ghostty" / "config"
    refresh_gtk = refresh_home / ".config" / "gtk-4.0" / "settings.ini"
    refresh_ghostty.parent.mkdir(parents=True)
    refresh_gtk.parent.mkdir(parents=True)
    refresh_ghostty_original = b"font-size = 13\ntheme = user-theme\n"
    refresh_gtk_original = b"[Settings]\ngtk-font-name=Inter 11\n"
    refresh_ghostty.write_bytes(refresh_ghostty_original)
    refresh_gtk.write_bytes(refresh_gtk_original)
    current = root / "existing" / "gooarchy" / "current-theme"
    current.mkdir(parents=True)
    for theme_mode in ("light", "dark"):
        os.symlink(f"/themes/old-{theme_mode}", current / theme_mode)
    original_links = {theme_mode: os.readlink(current / theme_mode)
                      for theme_mode in ("light", "dark")}
    original_mtimes = {theme_mode: (current / theme_mode).lstat().st_mtime_ns
                       for theme_mode in ("light", "dark")}
    calls = []

    def guarded_lookup(command, **kwargs):
        calls.append(tuple(command))
        return completed(command, stdout=json.dumps({
            "light": "/themes/new-light", "dark": "/themes/new-dark",
        }))

    runtime = root / "runtime"
    runtime.mkdir()
    hooks = root / "hooks" / "libexec"
    hooks.mkdir(parents=True)
    (hooks / "scottland-color-scheme").write_text("#!/bin/sh\nexit 0\n")
    previous_environment = {
        name: os.environ.get(name)
        for name in ("OMARCHY_PATH", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "SCOTTLAND_HOOKS",
                     "HOME", "PATH", "XDG_CONFIG_HOME")
    }
    try:
        os.environ["OMARCHY_PATH"] = str(omarchy)
        os.environ["HOME"] = str(refresh_home)
        os.environ["PATH"] = f"{refresh_home / '.stubs'}:/usr/bin:/bin"
        os.environ["XDG_CONFIG_HOME"] = str(refresh_home / ".config")
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        os.environ["SCOTTLAND_HOOKS"] = str(hooks.parent)
        guarded_result = refresh.refresh(current, Path(repo) / "libexec" / "gooarchy-theme-lookup", guarded_lookup)
        missing_current = root / "missing-parent" / "gooarchy" / "current-theme"
        missing_result = refresh.refresh(
            missing_current, Path(repo) / "libexec" / "gooarchy-theme-lookup", guarded_lookup,
        )
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    expect("refresh leaves current-theme untouched when OMARCHY_PATH is an existing directory",
           guarded_result == 0
           and {theme_mode: os.readlink(current / theme_mode) for theme_mode in ("light", "dark")} == original_links
           and {theme_mode: (current / theme_mode).lstat().st_mtime_ns
                for theme_mode in ("light", "dark")} == original_mtimes
           and not (current.parent / ".current-theme-refresh.lock").exists()
           and calls == [] and text_size_value(refresh_home) == "1.25"
           and not text_size_writes(str(refresh_home))
           and refresh_ghostty.read_bytes() == refresh_ghostty_original
           and refresh_gtk.read_bytes() == refresh_gtk_original,
           "R12/T7 refresh preserves text size and existing Ghostty/GTK configs")
    expect("refresh does not create current-theme parents on Omarchy",
           missing_result == 0 and not missing_current.parent.exists() and calls == [])

    missing_omarchy = root / "omarchy-not-installed"
    calls.clear()
    palette_observations = []

    def ordinary_refresh_run(command, **kwargs):
        calls.append(tuple(command))
        if Path(command[0]).name in ("gsettings", "dconf"):
            stub = refresh_home / ".stubs" / Path(command[0]).name
            return subprocess.run([str(stub), *command[1:]], env=environment(str(refresh_home)),
                                  capture_output=True, text=True)
        if tuple(command[-1:]) == ("once",):
            palette_observations.append({
                theme_mode: os.readlink(ordinary_current / theme_mode)
                for theme_mode in ("light", "dark")
            })
        return completed(command, stdout=json.dumps({
            "light": "/themes/new-light", "dark": "/themes/new-dark",
        }))

    previous_environment = {
        name: os.environ.get(name)
        for name in ("OMARCHY_PATH", "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "SCOTTLAND_HOOKS",
                     "HOME", "PATH", "XDG_CONFIG_HOME")
    }
    try:
        os.environ["OMARCHY_PATH"] = str(missing_omarchy)
        os.environ["HOME"] = str(refresh_home)
        os.environ["PATH"] = f"{refresh_home / '.stubs'}:/usr/bin:/bin"
        os.environ["XDG_CONFIG_HOME"] = str(refresh_home / ".config")
        os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        os.environ["SCOTTLAND_HOOKS"] = str(hooks.parent)
        ordinary_current = root / "ordinary" / "gooarchy" / "current-theme"
        ordinary_result = refresh.refresh(
            ordinary_current, Path(repo) / "libexec" / "gooarchy-theme-lookup", ordinary_refresh_run,
        )
    finally:
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    expect("refresh runs normally when OMARCHY_PATH does not exist",
           ordinary_result == 0 and ordinary_current.is_dir()
           and calls == [
               (str(Path(repo) / "libexec" / "gooarchy-theme-lookup"), "--pair"),
               (str(hooks / "scottland-color-scheme"), "once"),
           ]
           and palette_observations == [{
               "light": "/themes/new-light", "dark": "/themes/new-dark",
           }]
           and text_size_value(refresh_home) == "1.25"
           and not text_size_writes(str(refresh_home)),
           "theme refresh updates derived links while preserving text size (T2)")

# make install copies catalogue entries verbatim and derives files without a second checkout.
with tempfile.TemporaryDirectory() as destdir:
    result = subprocess.run(["make", "install", f"DESTDIR={destdir}"], cwd=repo,
                            capture_output=True, text=True)
    expect("make install succeeds into an isolated DESTDIR", result.returncode == 0,
           result.stderr.strip()[-300:])
    if result.returncode == 0:
        installed = Path(destdir) / "usr/share/gooarchy-flavorings/themes"
        for source in sorted((Path(repo) / "themes").glob("*-light")) + sorted((Path(repo) / "themes").glob("*-dark")):
            target = installed / source.name
            exact = target.is_dir()
            for source_file in source.rglob("*"):
                relative = source_file.relative_to(source)
                installed_file = target / relative
                if source_file.is_symlink():
                    exact = exact and installed_file.is_symlink() and os.readlink(source_file) == os.readlink(installed_file)
                elif source_file.is_file():
                    exact = exact and installed_file.is_file() and source_file.read_bytes() == installed_file.read_bytes()
            first = lookup.background_files(source)[0]
            exact = exact and (target / "background.webp").read_bytes() == first.read_bytes()
            exact = exact and (target / "ghostty").is_file()
            expect(f"{source.name} installs byte-for-byte with derived files", exact)
            ghostty = target / "ghostty"
            derived_size_lines = ([line for line in ghostty.read_text().splitlines()
                                   if any(key in line.lower()
                                          for key in ("font-size", "text-size", "text-scaling-factor"))]
                                 if ghostty.is_file() else ["derived Ghostty file is missing"])
            expect(f"{source.name} derived Ghostty has no text-size setting (T5)",
                   not derived_size_lines, derived_size_lines)


# Setup writes only the current-theme Ghostty line to a new config and leaves a saved config alone.
apply_module = load_module("flavorings_apply", tool)
with tempfile.TemporaryDirectory() as temp:
    config = Path(temp) / "config"
    current = config / "gooarchy" / "current-theme"
    current.mkdir(parents=True)
    for variant in ("light", "dark"):
        os.symlink(f"/themes/plain-{variant}", current / variant)
    apply_module.CONFIG = config
    apply_module.STATE = Path(temp) / "state" / "gooarchy"
    expect("new Ghostty config uses the absolute current-theme pair",
           apply_module.ghostty()
           and (config / "ghostty/config").read_text() ==
           f"# Gooarchy flavorings: follow the selected theme and desktop mode.\n"
           f"theme = light:{current}/light/ghostty,dark:{current}/dark/ghostty\n")
    saved = config / "ghostty/config"
    saved.write_text("# user settings\nfont-size = 13\n")
    previous = saved.read_bytes()
    report = StringIO()
    with redirect_stderr(report):
        left_alone = apply_module.ghostty()
    expect("an existing Ghostty config stays byte-for-byte unchanged",
           left_alone and saved.read_bytes() == previous)
    expect("a saved Ghostty config without a theme suggests the current-theme line",
           f"for Gooarchy's theme, add: theme = light:{current}/light/ghostty,"
           f"dark:{current}/dark/ghostty" in report.getvalue())

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    omarchy = root / "omarchy"
    omarchy.mkdir()
    refresh_attempts = []
    original_config = apply_module.CONFIG
    original_report = apply_module.REPORT
    original_state = apply_module.STATE
    original_markers = apply_module.MARKERS
    original_steps = apply_module.STEPS
    original_omarchy = apply_module.OMARCHY
    original_helper_path = apply_module.helper_path
    try:
        apply_module.OMARCHY = omarchy.is_dir()
        apply_module.CONFIG = root / "config"
        apply_module.REPORT = apply_module.CONFIG / "scottland" / "override-report.d" / "gooarchy-flavorings.txt"
        apply_module.STATE = root / "state" / "gooarchy"
        apply_module.MARKERS = apply_module.STATE / "flavorings"
        apply_module.STEPS = []

        def record_refresh_attempt(installed, name):
            refresh_attempts.append((installed, name))
            return root / "unused-refresh-helper"

        apply_module.helper_path = record_refresh_attempt
        result = apply_module.main()
    finally:
        apply_module.CONFIG = original_config
        apply_module.REPORT = original_report
        apply_module.STATE = original_state
        apply_module.MARKERS = original_markers
        apply_module.STEPS = original_steps
        apply_module.OMARCHY = original_omarchy
        apply_module.helper_path = original_helper_path
    expect("integrated apply skips the refresh helper when OMARCHY is true",
           result == 0 and not refresh_attempts
           and (root / "state" / "gooarchy" / "flavorings.lock").is_file())

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    home = root / "home"
    home.mkdir()
    omarchy = root / "omarchy"
    omarchy.mkdir()
    config = home / ".config"
    current = config / "gooarchy" / "current-theme"
    current.mkdir(parents=True)
    for theme_mode in ("light", "dark"):
        os.symlink(f"/themes/kept-{theme_mode}", current / theme_mode)
    original_links = {
        theme_mode: (os.readlink(current / theme_mode), (current / theme_mode).lstat().st_ino,
                     (current / theme_mode).lstat().st_mtime_ns)
        for theme_mode in ("light", "dark")
    }
    ghostty_config = config / "ghostty" / "config"
    ghostty_config.parent.mkdir(parents=True)
    ghostty_config.write_text("# saved Ghostty config\nfont-size = 13\n")
    original_ghostty = ghostty_config.read_bytes()
    gtk_config = config / "gtk-4.0" / "settings.ini"
    gtk_config.parent.mkdir(parents=True)
    gtk_original = b"[Settings]\ngtk-font-name=Inter 11\n"
    gtk_config.write_bytes(gtk_original)
    scale = home / ".test-text-scaling-factor"
    scale.write_text("1.25")

    result = apply(str(home), omarchy_path=omarchy)
    expect("Omarchy setup leaves existing Ghostty config and current-theme links untouched",
           result.returncode == 0 and ghostty_config.read_bytes() == original_ghostty
           and gtk_config.read_bytes() == gtk_original
           and {theme_mode: (os.readlink(current / theme_mode), (current / theme_mode).lstat().st_ino,
                             (current / theme_mode).lstat().st_mtime_ns)
                for theme_mode in ("light", "dark")} == original_links
           and not (current.parent / ".current-theme-refresh.lock").exists()
           and not (home / ".local/state/gooarchy/flavorings/ghostty").exists(),
           (result.stderr or result.stdout).strip()[-180:])
    expect("R12/T7 setup preserves text size and existing app configs",
           os.path.isdir(omarchy) and result.returncode == 0 and text_size_value(home) == "1.25"
           and not text_size_writes(str(home))
           and ghostty_config.read_bytes() == original_ghostty and gtk_config.read_bytes() == gtk_original)
    listing = apply(str(home), omarchy_path=omarchy, args=("--list",))
    expect("Omarchy setup listing identifies Ghostty as Omarchy's own",
           listing.returncode == 0 and "ghostty: Omarchy's own" in listing.stdout)

    ordinary_home = root / "ordinary-home"
    ordinary_home.mkdir()
    missing_omarchy = root / "omarchy-not-installed"
    ordinary = apply(str(ordinary_home), omarchy_path=missing_omarchy)
    ordinary_current = ordinary_home / ".config/gooarchy/current-theme"
    expect("setup refreshes current-theme and configures Ghostty when OMARCHY_PATH is missing",
           ordinary.returncode == 0 and ordinary_current.is_dir()
           and all((ordinary_current / theme_mode).is_symlink()
                   for theme_mode in ("light", "dark"))
           and (ordinary_home / ".config/ghostty/config").is_file()
           and (ordinary_home / ".local/state/gooarchy/flavorings/ghostty").is_file(),
           (ordinary.stderr or ordinary.stdout).strip()[-180:])

with tempfile.TemporaryDirectory() as home:
    os.umask(0o022)
    claude = os.path.join(home, ".claude.json")
    with open(claude, "w") as f:
        json.dump({"mcpServers": {"x": {"token": "not-a-real-secret"}}}, f)
    os.chmod(claude, 0o600)
    r = apply(home)
    data = json.load(open(claude))
    expect("existing private file keeps mode 0600", mode(claude) == 0o600, oct(mode(claude)))
    expect("existing contents kept and the key added",
           data.get("mcpServers") and data.get("preferredNotifChannel") == "terminal_bell", data)
    codex = os.path.join(home, ".codex", "config.toml")
    expect("a new private file is created 0600", os.path.exists(codex) and mode(codex) == 0o600,
           os.path.exists(codex) and oct(mode(codex)))
    prefs = os.path.join(home, ".config", "chromium", "Default", "Preferences")
    expect("Chromium's new Preferences is 0600", mode(prefs) == 0o600, oct(mode(prefs)))
    expect("no temporary files left", not [n for n, _, fs in os.walk(home) for f in fs if f.endswith(".gooarchy")])
    expect("an unset color scheme gets Watercolor Dream light",
           "set org.gnome.desktop.interface color-scheme prefer-light" in open(os.path.join(home, "gsettings.log")).read())
    expect("Sunlight-on setup note keeps Sunlight enabled through periodic checks",
           "schedule stays on after a manual theme pick" in r.stdout
           and "keeping that choice through periodic checks" in r.stdout
           and "changing it only at the next scheduled sunrise or sunset" in r.stdout
           and "only if you want to keep one mode permanently" in r.stdout, r.stdout)
    expect("setup leaves an unset text size unset (T2/T3)", not (Path(home) / ".test-text-scaling-factor").exists())
    expect("setup does not write the text-scaling key (T2)", not text_size_writes(home))
    default = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"],
                             env=environment(home), capture_output=True, text=True)
    expect("an unset text size keeps the GNOME schema default of 1.0 (T3)",
           default.returncode == 0 and default.stdout.strip() == "1.0", default.stdout.strip())

with tempfile.TemporaryDirectory() as home:
    scale = Path(home) / ".test-text-scaling-factor"
    scale.write_text("1.25")
    result = apply(home)
    expect("setup preserves an existing text size (T3)", result.returncode == 0 and text_size_value(home) == "1.25")
    expect("setup does not write an existing text size (T2)", result.returncode == 0 and not text_size_writes(home))

with tempfile.TemporaryDirectory() as home:
    scale = Path(home) / ".test-text-scaling-factor"
    scale.write_text("1.25")
    dark = theme(home, "dark")
    light = theme(home, "light")
    expect("light/dark theme switches do not write text size (T2/T5)",
           dark.returncode == 0 and light.returncode == 0 and not text_size_writes(home),
           (dark.stderr or light.stderr).strip()[-180:])
    expect("light/dark theme switches preserve the existing text size (T5)", text_size_value(home) == "1.25")

with tempfile.TemporaryDirectory() as home:
    scale = Path(home) / ".test-text-scaling-factor"
    scale.write_text("1.25")
    config = Path(home) / ".config" / "ghostty" / "config"
    gtk = Path(home) / ".config" / "gtk-4.0" / "settings.ini"
    config.parent.mkdir(parents=True)
    gtk.parent.mkdir(parents=True)
    ghostty_original = b"font-size = 13\ntheme = user-theme\n"
    gtk_original = b"[Settings]\ngtk-font-name=Inter 11\n"
    config.write_bytes(ghostty_original)
    gtk.write_bytes(gtk_original)
    result = apply(home)
    switched = theme(home, "dark")
    expect("setup and theme change preserve existing text size (T2)",
           result.returncode == 0 and switched.returncode == 0 and text_size_value(home) == "1.25")
    expect("setup and theme change write no text-size value (T2)",
           result.returncode == 0 and switched.returncode == 0 and not text_size_writes(home))
    expect("setup and theme change preserve existing Ghostty and GTK configs byte for byte (T6)",
           config.read_bytes() == ghostty_original and gtk.read_bytes() == gtk_original)

size_lines = theme_size_settings()
expect("theme sources contain no font-size or text-size setting (T5)", not size_lines, size_lines)

with tempfile.TemporaryDirectory() as home:
    real = os.path.join(home, "dotfiles-claude.json")
    with open(real, "w") as f:
        f.write("{}")
    os.symlink(real, os.path.join(home, ".claude.json"))
    r = apply(home, dconf_value="'default'")
    expect("a symlinked config is left alone", os.path.islink(os.path.join(home, ".claude.json"))
           and open(real).read() == "{}", r.stderr.strip()[:120])
    expect("the symlink is reported", "symlink" in r.stderr)
    expect("an explicitly set color scheme (even 'default') is kept",
           "color-scheme prefer-light" not in (open(os.path.join(home, "gsettings.log")).read()
                                              if os.path.exists(os.path.join(home, "gsettings.log")) else "")
           and "kept your color scheme" in r.stderr, r.stderr.strip()[:160])

with tempfile.TemporaryDirectory() as home:
    r = apply(home, running="claude codex chromium")
    expect("running apps are deferred (no files, no markers)",
           not os.path.exists(os.path.join(home, ".claude.json"))
           and not os.path.exists(os.path.join(home, ".local/state/gooarchy/flavorings/claude-code")), r.stdout.strip()[:160])
    r = apply(home)
    expect("and applied at a later run", os.path.exists(os.path.join(home, ".claude.json")))

# An interrupted write: the replacement step fails midway.
loader = importlib.machinery.SourceFileLoader("apply", tool)
spec = importlib.util.spec_from_loader("apply", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
with tempfile.TemporaryDirectory() as home:
    target = os.path.join(home, "settings.json")
    with open(target, "w") as f:
        f.write('{"old": true}')
    os.chmod(target, 0o600)
    real_replace = mod.os.replace
    mod.os.replace = lambda *a: (_ for _ in ()).throw(OSError("simulated interruption"))
    try:
        mod.write_file(target, '{"new": true}')
        raised = False
    except OSError:
        raised = True
    finally:
        mod.os.replace = real_replace
    expect("an interrupted write leaves the old file and no temporary file",
           raised and open(target).read() == '{"old": true}' and os.listdir(home) == ["settings.json"],
           os.listdir(home))

# Two runs at once (the installer and the session's autostart).
with tempfile.TemporaryDirectory() as home:
    with open(os.path.join(home, ".claude.json"), "w") as f:
        json.dump({"keep": 1}, f)
    bindir = os.path.join(home, ".stubs")
    stubs(bindir)
    env = environment(home)
    procs = [subprocess.Popen([sys.executable, tool], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(4)]
    for p in procs:
        p.wait()
    data = json.load(open(os.path.join(home, ".claude.json")))
    expect("concurrent runs leave valid files", data == {"keep": 1, "preferredNotifChannel": "terminal_bell"}, data)

# Snapshot paths without following symlinks. Comparing these tuples checks installed modes,
# file bytes and symlink targets, as well as file types and directory modes.
def tree_snapshot(root):
    entries = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root)
            metadata = os.lstat(path)
            entry_mode = stat.S_IMODE(metadata.st_mode)
            if os.path.islink(path):
                entries[relative] = ("symlink", entry_mode, os.readlink(path))
            elif stat.S_ISDIR(metadata.st_mode):
                entries[relative] = ("directory", entry_mode)
            elif stat.S_ISREG(metadata.st_mode):
                with open(path, "rb") as stream:
                    entries[relative] = ("file", entry_mode, stream.read())
            else:
                entries[relative] = ("other", entry_mode, stat.S_IFMT(metadata.st_mode))
    return entries


with tempfile.TemporaryDirectory() as stage:
    candidate_install = subprocess.run(
        ["make", "install", f"DESTDIR={stage}"],
        cwd=repo, capture_output=True, text=True,
    )
    candidate_installed = candidate_install.returncode == 0
    expect("candidate package install succeeds in DESTDIR", candidate_installed,
           (candidate_install.stderr or candidate_install.stdout).strip()[-240:])

    if candidate_installed:
        wallpaper = Path(stage) / "usr/lib/gooarchy-flavorings/gooarchy-wallpaper"
        wallpaper_lookup = wallpaper.with_name("gooarchy-theme-lookup")
        refresh_entry = wallpaper.with_name("gooarchy-theme-refresh")
        integration_helper = wallpaper.with_name("gooarchy-theme-integrations")
        integration_helper.write_text(
            "#!/bin/sh\n"
            "config_home=${XDG_CONFIG_HOME:-$HOME/.config}\n"
            "selection=$config_home/gooarchy/theme\n"
            "if [ -r \"$selection\" ]; then IFS= read -r slug < \"$selection\"; else slug=default; fi\n"
            "printf '%s\\n' \"$slug\" >>\"$HOME/integration.log\"\n"
            "if [ -f \"$HOME/fail-integrations\" ] && [ \"$slug\" = broken ]; then "
            "echo 'simulated app integration failure' >&2; exit 1; fi\n"
        )
        integration_helper.chmod(0o755)

        refresh_home = Path(stage) / "refresh-home"
        refresh_config = refresh_home / ".config"
        refresh_current = refresh_config / "gooarchy" / "current-theme"
        refresh_current.mkdir(parents=True)
        for theme_mode in ("light", "dark"):
            os.symlink(f"/themes/kept-{theme_mode}", refresh_current / theme_mode)
        original_refresh_links = {
            theme_mode: (os.readlink(refresh_current / theme_mode),
                         (refresh_current / theme_mode).lstat().st_ino,
                         (refresh_current / theme_mode).lstat().st_mtime_ns)
            for theme_mode in ("light", "dark")
        }
        refresh_omarchy = Path(stage) / "refresh-omarchy"
        refresh_omarchy.mkdir()
        refresh_lookup_calls = refresh_home / "lookup.log"
        wallpaper_lookup.write_text(
            "#!/bin/sh\nprintf 'called\\n' >>\"$HOME/lookup.log\"\n"
            "printf '{\"light\":\"/themes/new-light\",\"dark\":\"/themes/new-dark\"}\\n'\n"
        )
        wallpaper_lookup.chmod(0o755)
        refresh_env = {
            "HOME": str(refresh_home),
            "XDG_CONFIG_HOME": str(refresh_config),
            "OMARCHY_PATH": str(refresh_omarchy),
            "PATH": "/usr/bin:/bin",
        }
        direct_refresh = subprocess.run(
            [str(refresh_entry)], env=refresh_env, capture_output=True, text=True, timeout=5,
        )
        expect("installed refresh exits on Omarchy without reading or changing current-theme",
               direct_refresh.returncode == 0
               and {theme_mode: (os.readlink(refresh_current / theme_mode),
                                 (refresh_current / theme_mode).lstat().st_ino,
                                 (refresh_current / theme_mode).lstat().st_mtime_ns)
                    for theme_mode in ("light", "dark")} == original_refresh_links
               and not (refresh_current.parent / ".current-theme-refresh.lock").exists()
               and not refresh_lookup_calls.exists(),
               (direct_refresh.stderr or direct_refresh.stdout).strip()[-240:])

        fixture = Path(stage) / "wallpaper-fixture"
        (fixture / "backgrounds").mkdir(parents=True)
        (fixture / "backgrounds" / "first.webp").write_bytes(b"fixture")
        write_minimal_colors(fixture)
        wallpaper_lookup.write_text(
            "#!/bin/sh\nprintf 'lookup\\n' >>\"$HOME/wallpaper-calls.log\"\n"
            "printf '%s\\n' \"$TEST_VARIANT\"\n"
        )
        wallpaper_lookup.chmod(0o755)

        stub_bin = Path(stage) / "wallpaper-test-bin"
        stub_bin.mkdir()
        (stub_bin / "gsettings").write_text(
            "#!/bin/sh\nprintf 'gsettings\\n' >>\"$HOME/wallpaper-calls.log\"\nexec sleep 3\n",
        )
        (stub_bin / "swaybg").write_text(
            "#!/bin/sh\nprintf 'swaybg\\n' >>\"$HOME/wallpaper-calls.log\"\nexit 0\n",
        )
        (stub_bin / "gsettings").chmod(0o755)
        (stub_bin / "swaybg").chmod(0o755)
        missing_omarchy = Path(stage) / "omarchy-not-installed"
        with tempfile.TemporaryDirectory() as home:
            wallpaper_env = {
                "HOME": home,
                "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                "PATH": f"{stub_bin}:/usr/bin:/bin",
                "TEST_VARIANT": str(fixture),
                "OMARCHY_PATH": str(missing_omarchy),
            }
            wallpaper_result = subprocess.run(
                [str(wallpaper)], env=wallpaper_env, capture_output=True, text=True, timeout=5,
            )
            calls = Path(home) / "wallpaper-calls.log"
            normal_wallpaper_started = (calls.is_file()
                                        and set(calls.read_text().splitlines())
                                        == {"gsettings", "lookup", "swaybg"})
        expect("installed wallpaper executable exits through its entrypoint with isolated desktop stubs",
               wallpaper_result.returncode == 0 and normal_wallpaper_started,
               (wallpaper_result.stderr or wallpaper_result.stdout).strip()[-240:])

        omarchy = Path(stage) / "omarchy"
        omarchy.mkdir()
        with tempfile.TemporaryDirectory() as home:
            runtime = Path(home) / "runtime"
            runtime.mkdir()
            wallpaper_env["HOME"] = home
            wallpaper_env["XDG_CONFIG_HOME"] = str(Path(home) / ".config")
            wallpaper_env["XDG_RUNTIME_DIR"] = str(runtime)
            wallpaper_env["OMARCHY_PATH"] = str(omarchy)
            omarchy_wallpaper = subprocess.run(
                [str(wallpaper)], env=wallpaper_env, capture_output=True, text=True, timeout=5,
            )
            no_wallpaper_process = (not (Path(home) / "wallpaper-calls.log").exists()
                                   and not any(runtime.iterdir())
                                   and not (Path(home) / ".config/gooarchy/current-theme").exists())
        expect("direct wallpaper helper starts no monitor, lookup, or wallpaper process on Omarchy",
               omarchy_wallpaper.returncode == 0 and no_wallpaper_process,
               (omarchy_wallpaper.stderr or omarchy_wallpaper.stdout).strip()[-240:])

        sync_root = Path(stage) / "wallpaper-sync-test"
        sync_home = sync_root / "home"
        runtime = sync_root / "runtime"
        hooks = sync_root / "hooks"
        themes = sync_root / "themes"
        for directory in (sync_home, runtime, hooks / "libexec"):
            directory.mkdir(parents=True, exist_ok=True)
        for slug in ("old", "new", "broken"):
            for theme_mode in ("light", "dark"):
                backgrounds = themes / f"{slug}-{theme_mode}" / "backgrounds"
                backgrounds.mkdir(parents=True)
                (backgrounds / f"{slug}.webp").write_bytes(b"fixture")
                write_minimal_colors(backgrounds.parent)

        sync_config = Path(sync_home) / ".config"
        selection = sync_config / "gooarchy" / "theme"
        selection.parent.mkdir(parents=True)
        selection.write_text("old\n")
        (sync_home / "theme-mode").write_text("dark\n")
        (sync_home / "gsettings-events").write_text("")
        current = selection.parent / "current-theme"
        current.mkdir()
        for theme_mode in ("light", "dark"):
            os.symlink(themes / f"old-{theme_mode}", current / theme_mode)

        wallpaper_lookup.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "root = Path(os.environ['TEST_THEME_ROOT'])\n"
            "slug = (Path(os.environ['XDG_CONFIG_HOME']) / 'gooarchy' / 'theme').read_text().strip()\n"
            "mode = (Path(os.environ['HOME']) / 'theme-mode').read_text().strip()\n"
            "if sys.argv[1:] == ['--pair']:\n"
            "    print(json.dumps({mode: str(root / f'{slug}-{mode}') for mode in ('light', 'dark')}))\n"
            "else:\n"
            "    print(root / f\"{slug}-{mode}\")\n"
        )
        wallpaper_lookup.chmod(0o755)
        sync_bin = sync_root / "bin"
        sync_bin.mkdir()
        (sync_bin / "gsettings").write_text("#!/bin/sh\nexec tail -n 0 -f \"$HOME/gsettings-events\"\n")
        (sync_bin / "swaybg").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$HOME/swaybg.log\"\nexec sleep 30\n",
        )
        for name in ("gsettings", "swaybg"):
            (sync_bin / name).chmod(0o755)
        palette_command = hooks / "libexec" / "scottland-color-scheme"
        palette_command.write_text(
            "#!/bin/sh\n"
            "printf '%s|%s|%s\\n' \"$*\" \"$TEST_MODE\" "
            "\"$(readlink \"$XDG_CONFIG_HOME/gooarchy/current-theme/dark\")\" "
            ">>\"$HOME/palette.log\"\n"
        )
        palette_command.chmod(0o755)
        sync_env = {
            "HOME": str(sync_home),
            "XDG_CONFIG_HOME": str(sync_config),
            "XDG_RUNTIME_DIR": str(runtime),
            "WAYLAND_DISPLAY": "wayland-test",
            "SCOTTLAND_HOOKS": str(hooks),
            "OMARCHY_PATH": str(sync_root / "omarchy-not-installed"),
            "PATH": f"{sync_bin}:/usr/bin:/bin",
            "TEST_THEME_ROOT": str(themes),
            "TEST_MODE": "dark",
        }
        single_wallpaper_log = sync_home / "swaybg.log"
        startup_integration_log = sync_home / "integration.log"
        wallpaper_helper = subprocess.Popen(
            [str(wallpaper)], env=sync_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 5
        startup_ready = False
        while wallpaper_helper.poll() is None and time.monotonic() < deadline:
            if (startup_integration_log.is_file()
                    and startup_integration_log.read_text().splitlines() == ["old"]):
                startup_ready = True
                break
            time.sleep(0.01)
        synced = False
        sync_detail = ""
        try:
            if startup_ready and wallpaper_helper.poll() is None:
                # Startup integrations run after the initial theme-link signature is captured.
                selection.write_text("new\n")
                sync_result = subprocess.run(
                    [str(wallpaper.with_name("gooarchy-theme-refresh"))],
                    env=sync_env, capture_output=True, text=True, timeout=8,
                )
                # Snapshot immediately when refresh returns; no later poll may satisfy R8.
                swaybg_calls = single_wallpaper_log.read_text().splitlines()
                redraw_at_return = any("new-dark/backgrounds/new.webp" in line
                                        for line in swaybg_calls[1:])
                palette_calls = (sync_home / "palette.log").read_text().splitlines()
                integration_calls = (sync_home / "integration.log").read_text().splitlines()
                request_path = runtime / "gooarchy-wallpaper.request"
                ack_path = runtime / "gooarchy-wallpaper.ack"
                request_token = request_path.read_text().strip() if request_path.is_file() else ""
                ack_token = ack_path.read_text().strip() if ack_path.is_file() else ""
                synced = (sync_result.returncode == 0 and len(swaybg_calls) >= 2
                          and "old-dark/backgrounds/old.webp" in swaybg_calls[0]
                          and redraw_at_return
                          and request_token and ack_token == request_token
                          and palette_calls == [f"once|dark|{themes / 'new-dark'}"]
                          and integration_calls == ["old", "new"]
                          and os.readlink(current / "dark") == str(themes / "new-dark")
                          and wallpaper_helper.poll() is None
                          and (runtime / "gooarchy-wallpaper.lock").is_file())
                sync_detail = (sync_result.stderr or sync_result.stdout).strip()[-240:]
                if not synced:
                    sync_detail = (f"{sync_detail} rc={sync_result.returncode}; "
                                   f"redraw_at_return={redraw_at_return}; "
                                   f"wallpapers={swaybg_calls}; palette={palette_calls}; "
                                   f"integrations={integration_calls}; "
                                   f"dark={os.readlink(current / 'dark')}; "
                                   f"request={request_token!r}; ack={ack_token!r}; "
                                   f"runtime={sorted(path.name for path in runtime.iterdir())}").strip()
            else:
                sync_detail = "wallpaper helper did not log completed startup integrations before the deadline"
        except Exception as error:
            sync_detail = f"{type(error).__name__}: {error}"
        finally:
            if wallpaper_helper.poll() is None:
                wallpaper_helper.terminate()
            try:
                _, helper_error = wallpaper_helper.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                wallpaper_helper.kill()
                _, helper_error = wallpaper_helper.communicate(timeout=5)
        if helper_error.strip():
            sync_detail = f"{sync_detail}; helper stderr: {helper_error.strip()[-240:]}".strip("; ")
        expect("same-mode refresh waits for wallpaper acknowledgement after redraw of committed target",
               synced, sync_detail)

        failure_home = sync_root / "failure-home"
        failure_runtime = sync_root / "failure-runtime"
        failure_home.mkdir()
        failure_runtime.mkdir()
        failure_config = failure_home / ".config"
        failure_selection = failure_config / "gooarchy" / "theme"
        failure_selection.parent.mkdir(parents=True)
        failure_selection.write_text("old\n")
        failure_current = failure_selection.parent / "current-theme"
        failure_current.mkdir()
        for theme_mode in ("light", "dark"):
            os.symlink(themes / f"old-{theme_mode}", failure_current / theme_mode)
        (failure_home / "theme-mode").write_text("dark\n")
        (failure_home / "gsettings-events").write_text("")
        failure_env = {
            **sync_env,
            "HOME": str(failure_home),
            "XDG_CONFIG_HOME": str(failure_config),
            "XDG_RUNTIME_DIR": str(failure_runtime),
        }
        failure_wallpaper_log = failure_home / "swaybg.log"
        failure_integration_log = failure_home / "integration.log"
        failure_helper = subprocess.Popen(
            [str(wallpaper)], env=failure_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        failure_detail = ""
        failure_survived = False
        mode_followed = False
        failure_stderr = ""
        try:
            deadline = time.monotonic() + 5
            while failure_helper.poll() is None and time.monotonic() < deadline:
                if (failure_integration_log.is_file()
                        and failure_integration_log.read_text().splitlines() == ["old"]):
                    break
                time.sleep(0.01)
            if (failure_helper.poll() is None and failure_integration_log.is_file()
                    and failure_integration_log.read_text().splitlines() == ["old"]):
                (failure_home / "fail-integrations").write_text("fail\n")
                failure_selection.write_text("broken\n")
                failed_refresh = subprocess.run(
                    [str(refresh_entry)], env=failure_env, capture_output=True, text=True, timeout=8,
                )
                request_path = failure_runtime / "gooarchy-wallpaper.request"
                ack_path = failure_runtime / "gooarchy-wallpaper.ack"
                request_token = request_path.read_text().strip() if request_path.is_file() else ""
                ack_token = ack_path.read_text().strip() if ack_path.is_file() else ""
                swaybg_calls = failure_wallpaper_log.read_text().splitlines()
                failure_survived = (
                    failed_refresh.returncode != 0 and failure_helper.poll() is None
                    and request_token and ack_token != request_token
                    and any("old-dark/backgrounds/old.webp" in line for line in swaybg_calls)
                    and any("broken-dark/backgrounds/broken.webp" in line for line in swaybg_calls)
                )
                failure_detail = (f"refresh_rc={failed_refresh.returncode}; "
                                  f"alive={failure_helper.poll() is None}; "
                                  f"request={request_token!r}; ack={ack_token!r}; "
                                  f"wallpapers={swaybg_calls}")

                (failure_home / "fail-integrations").unlink()
                (failure_home / "theme-mode").write_text("light\n")
                with (failure_home / "gsettings-events").open("a") as events:
                    events.write("prefer-light\n")
                deadline = time.monotonic() + 5
                while failure_helper.poll() is None and time.monotonic() < deadline:
                    current_calls = failure_wallpaper_log.read_text().splitlines()
                    if any("broken-light/backgrounds/broken.webp" in line for line in current_calls):
                        mode_followed = True
                        break
                    time.sleep(0.01)
            else:
                failure_detail = "wallpaper helper did not reach the startup integration log barrier"
        except Exception as error:
            failure_detail = f"{type(error).__name__}: {error}"
        finally:
            if failure_helper.poll() is None:
                failure_helper.terminate()
            try:
                _, failure_stderr = failure_helper.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                failure_helper.kill()
                _, failure_stderr = failure_helper.communicate(timeout=5)
        if failure_stderr.strip():
            failure_detail = f"{failure_detail}; helper stderr: {failure_stderr.strip()[-240:]}".strip("; ")
        expect("failed requested integrations leave wallpaper alive without acknowledging the refresh",
               failure_survived, failure_detail)
        expect("wallpaper continues following mode changes after a failed requested refresh",
               mode_followed, failure_detail)

    notice_name = "CC0-1.0-Watercolor-Dream-themes-only.txt"
    notice_relative_path = os.path.join(
        "usr", "share", "licenses", "gooarchy-flavorings", notice_name,
    )
    installed_notice_path = os.path.join(stage, notice_relative_path)
    source_notice_path = os.path.join(repo, "licenses", notice_name)
    if candidate_installed:
        expect("CC0 notice is installed at its scoped path",
               os.path.isfile(installed_notice_path) and not os.path.islink(installed_notice_path),
               notice_relative_path)
        installed_notice = tree_snapshot(stage).get(notice_relative_path)
        with open(source_notice_path, "rb") as source_notice:
            expected_notice = source_notice.read()
        expect("installed CC0 legal text matches source bytes",
               installed_notice is not None and installed_notice[0] == "file"
               and installed_notice[2] == expected_notice)
        expect("installed CC0 notice mode is 0644",
               installed_notice is not None and installed_notice[0] == "file"
               and installed_notice[1] == 0o644,
               oct(installed_notice[1]) if installed_notice and installed_notice[0] == "file" else "missing")

        themes_match = True
        theme_details = []
        for theme in ("watercolor-dream-light", "watercolor-dream-dark"):
            source_theme = os.path.join(repo, "themes", theme)
            installed_theme = os.path.join(stage, "usr", "share", "gooarchy-flavorings", "themes", theme)
            source_entries = tree_snapshot(source_theme)
            installed_entries = tree_snapshot(installed_theme) if os.path.isdir(installed_theme) else {}
            extra_entries = set(installed_entries) - set(source_entries)
            expected_generated = {"background.webp", "ghostty"} - set(source_entries)
            generated_present = all(installed_entries.get(item, (None,))[0] == "file"
                                    for item in ("background.webp", "ghostty"))
            changed_entries = [path for path, entry in source_entries.items()
                               if installed_entries.get(path) != entry]
            same = (generated_present and not changed_entries
                    and extra_entries == expected_generated)
            if not same:
                themes_match = False
                theme_details.append({"theme": theme,
                                      "changed": changed_entries[:10],
                                      "extra": sorted(extra_entries)})
        expect("installed Watercolor Dream theme entries remain verbatim to source",
               themes_match, theme_details)
    else:
        expect("CC0 notice is installed at its scoped path", False, "candidate DESTDIR install did not complete")
        expect("installed CC0 legal text matches source bytes", False, "candidate DESTDIR install did not complete")
        expect("installed CC0 notice mode is 0644", False, "candidate DESTDIR install did not complete")
        expect("installed Watercolor Dream theme entries remain verbatim to source",
               False, "candidate DESTDIR install did not complete")
# Omarchy ---------------------------------------------------------------------------------------

def write(path, text, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if executable:
        os.chmod(path, 0o755)


def read(path):
    return open(path).read() if os.path.exists(path) else None


def omarchy_system(home, strata=False, implicit_html=False):
    """Stand-in system dirs: Omarchy's apps and default list, and Gooarchy's Scottland list."""
    share = os.path.join(home, ".sys", "share", "applications")
    apps = {"org.gnome.Nautilus.desktop": "Files", "chromium.desktop": "Chromium"}
    if strata:
        apps["io.github.lgse.Strata.desktop"] = "Strata"
    for app, name in apps.items():
        write(os.path.join(share, app), f"[Desktop Entry]\nType=Application\nName={name}\nExec=true\n")
    write(os.path.join(share, "mimeapps.list"), "[Default Applications]\ninode/directory=org.gnome.Nautilus.desktop\n"
          "x-scheme-handler/http=chromium.desktop\nx-scheme-handler/https=chromium.desktop\n")
    write(os.path.join(home, ".sys", "etc-xdg", "scottland-mimeapps.list"),
          read(os.path.join(repo, "xdg", "scottland-mimeapps.list")))
    if implicit_html:
        write(os.path.join(share, "mimeinfo.cache"),
              "[MIME Cache]\ntext/html=chromium.desktop;\n")


def fragment_lines_ok(text):
    """Only lines Scottland's grouped fragment format accepts (a comment would garble the report)."""
    return all(not line.strip() or line.strip().startswith(("## ", "Reason:", "- Keys:", "Was:", "Now:", "Origin:"))
               for line in text.splitlines())


STOCK_USER_MIMEAPPS = ("[Default Applications]\ntext/html=chromium.desktop\n"  # what Omarchy's setup leaves
                       "x-scheme-handler/http=chromium.desktop\nx-scheme-handler/https=chromium.desktop\n")


def quiet_titles(home):
    """The user's own tmux and shell settings, so no title entries whatever the machine has."""
    write(os.path.join(home, ".config", "tmux", "tmux.conf"), "set -g set-titles on\nset -g set-titles-string '#h:#W'\n")
    write(os.path.join(home, ".profile"), "export MOSH_TITLE_NOPREFIX=\n")


with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as omarchy:
    omarchy_system(home)
    quiet_titles(home)
    scale = Path(home) / ".test-text-scaling-factor"
    scale.write_text("1.25")
    config = os.path.join(home, ".config")
    write(os.path.join(config, "mimeapps.list"), STOCK_USER_MIMEAPPS)
    r = apply(home, omarchy=omarchy)
    expect("R12/T7: an existing text size stays unchanged when OMARCHY_PATH is a directory",
           os.path.isdir(omarchy) and r.returncode == 0 and text_size_value(home) == "1.25")
    expect("R12/T7: flavorings writes no text-size value under the directory predicate",
           os.path.isdir(omarchy) and r.returncode == 0 and not text_size_writes(home))
    markers = os.path.join(home, ".local", "state", "gooarchy", "flavorings")
    expect("Omarchy: the color scheme is not set", "color-scheme" not in (read(os.path.join(home, "gsettings.log")) or ""))
    expect("Omarchy: no Chromium or Ghostty files", not os.path.exists(os.path.join(config, "chromium"))
           and not os.path.exists(os.path.join(config, "ghostty")))
    expect("Omarchy: the look steps are not marked",
           not any(os.path.exists(os.path.join(markers, n)) for n in ("color-scheme", "chromium", "ghostty")),
           os.listdir(markers))
    expect("Omarchy: the bells and folders are applied",
           json.load(open(os.path.join(home, ".claude.json"))).get("preferredNotifChannel") == "terminal_bell"
           and os.path.exists(os.path.join(home, ".codex", "config.toml"))
           and all(os.path.exists(os.path.join(markers, n)) for n in ("folders", "claude-code", "codex")))
    report = os.path.join(config, "scottland", "override-report.d", "gooarchy-flavorings.txt")
    text = read(report) or ""
    expect("Omarchy: the report lists the two bells Gooarchy set",
           "- Keys: Claude Code notifications" in text and "- Keys: Codex notifications" in text, text)
    expect("Omarchy: the report has nothing on the look, titles, apps, or folders without Strata",
           not any(w in text for w in ("Chromium", "Ghostty", "color", "tmux", "mosh", "Folders", "Strata", "Web pages")), text)
    expect("Omarchy: the report is in Scottland's fragment format", fragment_lines_ok(text) and "Origin: Gooarchy flavoring" in text)
    expect("Omarchy: --list shows the look as Omarchy's",
           "color-scheme: Omarchy's own" in apply(home, "--list", omarchy=omarchy).stdout)

    before = os.stat(report)
    apply(home, "--override-report", omarchy=omarchy)
    after = os.stat(report)
    expect("Omarchy: an unchanged report is not rewritten", (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns))

    omarchy_system(home, strata=True)
    apply(home, "--override-report", omarchy=omarchy)
    text = read(report) or ""
    expect("Omarchy with Strata: folders are reported, Files to Strata",
           "- Keys: Folders\n  Was: Files (org.gnome.Nautilus.desktop)\n  Now: Strata (io.github.lgse.Strata.desktop)" in text, text)
    expect("Omarchy: browser links already opening in Chromium are not reported",
           "http links" not in text and "Web pages" not in text, text)

    write(os.path.join(config, "mimeapps.list"), STOCK_USER_MIMEAPPS + "inode/directory=org.gnome.Nautilus.desktop\n")
    apply(home, "--override-report", omarchy=omarchy)
    expect("Omarchy: the user's own folder choice wins and isn't reported", "Folders" not in (read(report) or ""))

    write(os.path.join(home, ".claude.json"), json.dumps({"preferredNotifChannel": "notifications"}))
    write(os.path.join(home, ".codex", "config.toml"), "[tui]\nnotification_method = \"osc9\"\n")
    apply(home, "--override-report", omarchy=omarchy)
    expect("Omarchy: with nothing left to report, the fragment is removed", not os.path.exists(report), read(report))
    expect("Omarchy: the fragment's directory stays for Scottland to watch", os.path.isdir(os.path.dirname(report)))

with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as omarchy:
    omarchy_system(home, implicit_html=True)
    quiet_titles(home)
    apply(home, "--override-report", omarchy=omarchy)
    report = os.path.join(home, ".config", "scottland", "override-report.d", "gooarchy-flavorings.txt")
    text = read(report) or ""
    expect("Omarchy: an implicit Chromium MIME association is not reported as a new Web pages default",
           "Web pages" not in text, text)

    omarchy_system(home, strata=True, implicit_html=True)
    apply(home, "--override-report", omarchy=omarchy)
    text = read(report) or ""
    expect("Omarchy with Strata: implicit Chromium fallback stays unreported while Folders is reported",
           "- Keys: Folders\n" in text and "Web pages" not in text, text)

with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as omarchy:
    omarchy_system(home)
    write(os.path.join(home, ".claude.json"), json.dumps({"preferredNotifChannel": "terminal_bell"}))
    write(os.path.join(home, ".stubs", "tmux"), "#!/bin/sh\n", executable=True)
    write(os.path.join(home, ".stubs", "mosh-client"), "#!/bin/sh\n", executable=True)
    write(os.path.join(home, ".profile"), "# export MOSH_TITLE_NOPREFIX=0\n")
    stubs(os.path.join(home, ".stubs"))
    r = apply(home, omarchy=omarchy)
    text = read(os.path.join(home, ".config", "scottland", "override-report.d", "gooarchy-flavorings.txt")) or ""
    expect("Omarchy: a bell the user set themselves is not reported", "- Keys: Claude Code" not in text, text)
    expect("Omarchy: a type with no default before is reported",
           "- Keys: Web pages\n  Was: No default app\n  Now: Chromium (chromium.desktop) in a Scottland session" in text, text)
    expect("Omarchy: tmux and mosh titles the user doesn't set are reported",
           all(f"- Keys: {k}\n" in text for k in ("tmux set-titles", "tmux set-titles-string", "mosh window titles")), text)
    expect("Omarchy: a commented mosh setting does not hide the package override",
           "- Keys: mosh window titles\n" in text, text)
    write(os.path.join(home, ".profile"), "export MOSH_TITLE_NOPREFIX=0\n")
    apply(home, "--override-report", omarchy=omarchy)
    text = read(os.path.join(home, ".config", "scottland", "override-report.d", "gooarchy-flavorings.txt")) or ""
    expect("Omarchy: an active mosh setting is respected",
           "- Keys: mosh window titles\n" not in text, text)

with tempfile.TemporaryDirectory() as home:
    apply(home)
    expect("Gooarchy: no override report fragment",
           not os.path.exists(os.path.join(home, ".config", "scottland", "override-report.d")))

# The Scottland hooks, from copies that point at this checkout instead of the installed paths.
with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as omarchy:
    stubs(os.path.join(home, ".stubs"))
    write(os.path.join(home, ".stubs", "gooarchy-flavorings-apply"),
          f"#!/bin/sh\necho apply \"$@\" >>\"$HOME/apply.log\"\nexec {sys.executable} {tool} \"$@\"\n", executable=True)
    ini = os.path.join(repo, "scottland", "flavorings.ini")
    hook = os.path.join(home, "40-gooarchy-flavorings")
    write(hook, read(os.path.join(repo, "scottland", "config.d", "40-gooarchy-flavorings"))
          .replace("/usr/share/gooarchy-flavorings/scottland.ini", ini), executable=True)
    gooarchy = subprocess.run([hook, "/base.ini"], env=environment(home), capture_output=True, text=True)
    expect("Gooarchy: the config hook appends the flavorings unchanged",
           gooarchy.stdout == read(ini) and not os.path.exists(os.path.join(home, "apply.log")))
    out = subprocess.run([hook, "/base.ini"], env=environment(home, omarchy), capture_output=True, text=True).stdout
    kept = [line for line in read(ini).splitlines()
            if not any(k in line for k in ("file_manager", "_browser", "Strata is", "Chromium is", "apps start"))]
    expect("Omarchy: the config hook leaves out Super+Shift+F and B and nothing else",
           out.splitlines() == kept and "KEY_F" not in out and "KEY_B" not in out and "tap_to_click" in out, out)
    expect("Omarchy: the config hook brings the report up to date", "apply --override-report" in (read(os.path.join(home, "apply.log")) or ""))

    started = os.path.join(home, "wallpaper-started")
    wallpaper = os.path.join(home, "40-gooarchy-wallpaper")
    write(os.path.join(home, "gooarchy-wallpaper"), f"#!/bin/sh\ntouch {started}\n", executable=True)
    write(wallpaper, read(os.path.join(repo, "scottland", "autostart.d", "40-gooarchy-wallpaper"))
          .replace("/usr/lib/gooarchy-flavorings/gooarchy-wallpaper", os.path.join(home, "gooarchy-wallpaper")), executable=True)
    r = subprocess.run([wallpaper], env=environment(home, omarchy), capture_output=True, text=True)
    expect("Omarchy: the wallpaper hook starts nothing", r.returncode == 0 and not os.path.exists(started))
    subprocess.run([wallpaper], env=environment(home), capture_output=True, text=True)
    expect("Gooarchy: the wallpaper hook starts the wallpaper", os.path.exists(started))


# gooarchy-theme: the mode commands keep their color-scheme behavior, and the Sunlight notice
# follows ~/.config/scottland/solar.ini without suggesting an indefinite manual override.
def theme_stubs(bindir):
    os.makedirs(bindir, exist_ok=True)
    gsettings = """#!/bin/sh
state="$HOME/.gsettings-state"
case "$1" in
  get) printf "'%s'\\n" "$(cat "$state" 2>/dev/null || echo prefer-light)" ;;
  set) printf "%s\\n" "$4" >"$state" ;;
esac
"""
    path = os.path.join(bindir, "gsettings")
    with open(path, "w") as f:
        f.write(gsettings)
    os.chmod(path, 0o755)
    sudo = os.path.join(bindir, "sudo")
    with open(sudo, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(sudo, 0o755)
    pgrep = os.path.join(bindir, "pgrep")
    with open(pgrep, "w") as f:
        f.write("#!/bin/sh\nexit 1\n")
    os.chmod(pgrep, 0o755)
    chromium = os.path.join(bindir, "chromium")
    with open(chromium, "w") as f:
        f.write('#!/bin/sh\nprintf "invoked\\n" >"$HOME/chromium-invoked"\nexit 1\n')
    os.chmod(chromium, 0o755)


def theme_run(home, arg=None, solar=None):
    bindir = os.path.join(home, ".stubs")
    theme_stubs(bindir)
    if solar is not None:
        solar_path = os.path.join(home, ".config", "scottland", "solar.ini")
        os.makedirs(os.path.dirname(solar_path), exist_ok=True)
        with open(solar_path, "w") as f:
            f.write(solar)
    env = {"HOME": home, "PATH": f"{bindir}:/usr/bin:/bin"}
    args = [theme_tool] + ([arg] if arg else [])
    return subprocess.run(args, env=env, capture_output=True, text=True)


def gsettings_value(home):
    path = os.path.join(home, ".gsettings-state")
    return open(path).read().strip() if os.path.exists(path) else ""


with tempfile.TemporaryDirectory() as home:
    r = theme_run(home, "light", solar="[solar]\nenabled = true\n")
    expect("light still sets prefer-light", gsettings_value(home) == "prefer-light", r.stdout + r.stderr)
    expect("Sunlight-on theme note keeps Sunlight enabled through periodic checks",
           "schedule stays on after a manual theme pick" in r.stderr
           and "keeping that choice through periodic checks" in r.stderr
           and "changing it only at the next scheduled sunrise or sunset" in r.stderr
           and "only if you want to keep one mode permanently" in r.stderr, r.stderr)
    expect("Sunlight notice fixture never launches Chromium",
           not os.path.exists(os.path.join(home, "chromium-invoked")), r.stderr)

with tempfile.TemporaryDirectory() as home:
    r = theme_run(home, "dark", solar="[solar]\nenabled = true\n")
    expect("dark still sets prefer-dark", gsettings_value(home) == "prefer-dark", r.stdout + r.stderr)

with tempfile.TemporaryDirectory() as home:
    theme_run(home, "dark", solar="[solar]\nenabled = true\n")
    r = theme_run(home, "toggle", solar="[solar]\nenabled = true\n")
    expect("toggle from dark still sets prefer-light", gsettings_value(home) == "prefer-light", r.stdout + r.stderr)

with tempfile.TemporaryDirectory() as home:
    r = theme_run(home, "light", solar="[solar]\nenabled = false\n")
    expect("Sunlight-off prints no note", r.stderr.strip() == "", r.stderr)

print(f"{'all passed' if not failures else f'{failures} failed'}")
sys.exit(1 if failures else 0)
