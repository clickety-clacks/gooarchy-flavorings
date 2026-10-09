#!/usr/bin/env python3
"""Test gooarchy-flavorings-apply in throwaway home directories, with stand-ins for the desktop
commands it calls (gsettings, dconf, xdg-user-dirs-update, pgrep). No desktop, no real settings.

  tests/flavorings-apply-test.py      exits 1 if anything fails

Covered: existing private files keep mode 0600 (and their contents); new private files are created
0600; a symlinked config is left alone and reported; an app that's running is deferred; an
explicitly set color scheme (even "default") is kept; an interrupted write leaves the old file and
no temporary file; two runs at once leave valid files. The package install places the unchanged
scoped notice at its expected path and preserves installed theme entries verbatim to their source.

On Omarchy (a stand-in OMARCHY_PATH): the wallpaper hook starts nothing; the color scheme, Chromium
and Ghostty are neither written nor marked; the config hook leaves out Super+Shift+F and B and adds
nothing else; the override report fragment lists only what changed (the bells Gooarchy set, tmux
and mosh titles the user doesn't set, default apps that win over the previous one, Strata only when
installed), is rewritten only when it changes and is removed when nothing is left. The Gooarchy
cases run with no Omarchy, whatever the test machine has.
"""
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import redirect_stderr
from io import StringIO

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tool = os.path.join(repo, "bin", "gooarchy-flavorings-apply")
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
    scripts = {
        "gsettings": "#!/bin/sh\necho \"$@\" >>\"$HOME/gsettings.log\"\n",
        "dbus-run-session": "#!/bin/sh\nshift; exec \"$@\"\n",
        "dconf": f"#!/bin/sh\nprintf '%s' \"{dconf_value}\"\n",
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
            "OMARCHY_PATH": omarchy or os.path.join(home, "no-omarchy"),
            "XDG_CONFIG_DIRS": os.path.join(home, ".sys", "etc-xdg"),
            "XDG_DATA_DIRS": os.path.join(home, ".sys", "share")}


def apply(home, *args, omarchy=None, **kw):
    bindir = os.path.join(home, ".stubs")
    stubs(bindir, **kw)
    return subprocess.run([sys.executable, tool, *args], env=environment(home, omarchy),
                          capture_output=True, text=True)


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
        fixture = Path(stage) / "wallpaper-fixture"
        (fixture / "backgrounds").mkdir(parents=True)
        (fixture / "backgrounds" / "first.webp").write_bytes(b"fixture")
        wallpaper_lookup.write_text("#!/bin/sh\nprintf '%s\\n' \"$TEST_VARIANT\"\n")
        wallpaper_lookup.chmod(0o755)

        stub_bin = Path(stage) / "wallpaper-test-bin"
        stub_bin.mkdir()
        (stub_bin / "gsettings").write_text("#!/bin/sh\nexec sleep 3\n")
        (stub_bin / "swaybg").write_text("#!/bin/sh\nexit 0\n")
        (stub_bin / "gsettings").chmod(0o755)
        (stub_bin / "swaybg").chmod(0o755)
        with tempfile.TemporaryDirectory() as home:
            wallpaper_env = {
                "HOME": home,
                "XDG_CONFIG_HOME": str(Path(home) / ".config"),
                "PATH": f"{stub_bin}:/usr/bin:/bin",
                "TEST_VARIANT": str(fixture),
            }
            wallpaper_result = subprocess.run(
                [str(wallpaper)], env=wallpaper_env, capture_output=True, text=True, timeout=5,
            )
        expect("installed wallpaper executable exits through its entrypoint with isolated desktop stubs",
               wallpaper_result.returncode == 0,
               (wallpaper_result.stderr or wallpaper_result.stdout).strip()[-240:])

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
    config = os.path.join(home, ".config")
    write(os.path.join(config, "mimeapps.list"), STOCK_USER_MIMEAPPS)
    r = apply(home, omarchy=omarchy)
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

print(f"{'all passed' if not failures else f'{failures} failed'}")
sys.exit(1 if failures else 0)
