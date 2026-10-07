#!/usr/bin/env python3
"""Test Gooarchy's Strata theme and Chromium policy integration in temporary paths."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


repo = Path(__file__).resolve().parents[1]
helper_path = repo / "libexec" / "gooarchy-theme-integrations"
loader = importlib.machinery.SourceFileLoader("gooarchy_theme_integrations", str(helper_path))
spec = importlib.util.spec_from_loader(loader.name, loader)
integrations = importlib.util.module_from_spec(spec)
loader.exec_module(integrations)
failures = 0


def expect(name, ok, detail=""):
    global failures
    failures += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}")


def configure(home):
    config = home / ".config"
    managed = home / "system" / "etc" / "chromium" / "policies" / "managed"
    integrations.CONFIG = config
    integrations.SHARE = repo
    integrations.POLICY_DIR = managed
    integrations.POLICY_FILE = managed / "gooarchy-theme.json"
    integrations.OMARCHY = False
    return config, managed


with tempfile.TemporaryDirectory(prefix="gooarchy-theme-command-") as temporary:
    root = Path(temporary)
    home = root / "home"
    home.mkdir()
    config = home / ".config"
    config.mkdir()
    (config / "scottland").mkdir()
    (config / "scottland" / "solar.ini").write_text("[solar]\nenabled = false\n")
    stubs = root / "bin"
    stubs.mkdir()
    gsettings = stubs / "gsettings"
    gsettings.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$HOME/gsettings.log\"\n"
                         "if [ \"$1\" = get ]; then printf '%s\\n' \"${GSETTINGS_VALUE:-prefer-light}\"; fi\n")
    gsettings.chmod(0o755)
    app = root / "app"
    (app / "bin").mkdir(parents=True)
    (app / "libexec").mkdir()
    shutil.copyfile(repo / "bin" / "gooarchy-theme", app / "bin" / "gooarchy-theme")
    (app / "bin" / "gooarchy-theme").chmod(0o755)
    integration_log = home / "integration.log"
    integration = app / "libexec" / "gooarchy-theme-integrations"
    integration.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" >>\"$HOME/integration.log\"\n")
    integration.chmod(0o755)
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(config), "PATH": f"{stubs}:/usr/bin:/bin",
           "GSETTINGS_VALUE": "prefer-light"}
    script = app / "bin" / "gooarchy-theme"
    no_arg = subprocess.run([script], env=env, capture_output=True, text=True)
    expect("theme query leaves the mode and integrations untouched",
           no_arg.returncode == 0 and not integration_log.exists())
    light = subprocess.run([script, "light"], env=env, capture_output=True, text=True)
    dark = subprocess.run([script, "dark"], env=env, capture_output=True, text=True)
    toggle_env = {**env, "GSETTINGS_VALUE": "prefer-dark"}
    toggle = subprocess.run([script, "toggle"], env=toggle_env, capture_output=True, text=True)
    expect("light, dark and toggle pass their selected mode to the integration hook",
           all(result.returncode == 0 for result in (light, dark, toggle))
           and integration_log.read_text().splitlines() == ["light", "dark", "light"],
           integration_log.read_text() if integration_log.exists() else None)
    mode_writes = [line for line in (home / "gsettings.log").read_text().splitlines() if line.startswith("set ")]
    expect("theme commands set the matching desktop mode",
           mode_writes == ["set org.gnome.desktop.interface color-scheme prefer-light",
                           "set org.gnome.desktop.interface color-scheme prefer-dark",
                           "set org.gnome.desktop.interface color-scheme prefer-light"], mode_writes)


with tempfile.TemporaryDirectory(prefix="gooarchy-strata-default-") as temporary:
    home = Path(temporary)
    config, _ = configure(home)
    strata = config / "strata"
    strata.mkdir(parents=True)
    settings = strata / "settings.toml"
    original = ('# Keep this file and its unrelated settings.\n'
                'mode = "theme"\n'
                'theme = "tokyo-night" # Strata first-run default\n'
                'text_size = 21\n')
    settings.write_text(original)
    expect("Strata default selection becomes Gooarchy's theme", integrations.update_strata("light"))
    light = (strata / "themes" / "gooarchy-watercolor.toml").read_text()
    expect("Strata light theme has Watercolor Dream colors",
           'name = "Watercolor Dream"' in light and 'background = "#F4F4ED"' in light
           and 'accent = "#3a5690"' in light, light)
    updated = settings.read_text()
    expect("only Strata's theme selection changes", updated == original.replace(
        'theme = "tokyo-night"', 'theme = "gooarchy-watercolor"'), updated)

    expect("the same Gooarchy theme entry follows dark mode", integrations.update_strata("dark"))
    dark = (strata / "themes" / "gooarchy-watercolor.toml").read_text()
    expect("Strata dark theme uses Watercolor Dream dark colors",
           'background = "#0c2631"' in dark and 'accent = "#4f80b6"' in dark, dark)
    expect("dark mode preserves Strata settings", settings.read_text() == updated)

with tempfile.TemporaryDirectory(prefix="gooarchy-strata-first-run-") as temporary:
    home = Path(temporary)
    config, _ = configure(home)
    expect("Strata first run needs no settings file", integrations.update_strata("dark"))
    settings = config / "strata" / "settings.toml"
    expect("first run saves only the selected theme and mode",
           settings.read_text() == 'mode = "theme"\ntheme = "gooarchy-watercolor"\n', settings.read_text())
    expect("first-run theme remains one custom theme entry",
           len(list((config / "strata" / "themes").glob("*.toml"))) == 1)

with tempfile.TemporaryDirectory(prefix="gooarchy-strata-user-theme-") as temporary:
    home = Path(temporary)
    config, _ = configure(home)
    strata = config / "strata"
    strata.mkdir(parents=True)
    settings = strata / "settings.toml"
    original = 'mode = "theme"\ntheme = "catppuccin-mocha"\ntext_size = 27\n'
    settings.write_text(original)
    expect("a different saved Strata theme is left alone", not integrations.update_strata("dark"))
    expect("user theme settings are byte-for-byte unchanged", settings.read_text() == original)
    expect("no Gooarchy theme file is added for another selected theme",
           not (strata / "themes" / "gooarchy-watercolor.toml").exists())

with tempfile.TemporaryDirectory(prefix="gooarchy-strata-collision-") as temporary:
    home = Path(temporary)
    config, _ = configure(home)
    strata = config / "strata"
    themes = strata / "themes"
    themes.mkdir(parents=True)
    settings = strata / "settings.toml"
    settings.write_text('mode = "theme"\ntheme = "tokyo-night"\n')
    theme_file = themes / "gooarchy-watercolor.toml"
    theme_file.write_text('name = "My custom theme"\nbackground = "#000000"\n')
    original_theme = theme_file.read_text()
    expect("an unmarked custom theme file is protected", not integrations.update_strata("light"))
    expect("the unmarked theme file and selected theme stay untouched",
           theme_file.read_text() == original_theme and 'theme = "tokyo-night"' in settings.read_text())

with tempfile.TemporaryDirectory(prefix="gooarchy-strata-invalid-") as temporary:
    home = Path(temporary)
    config, _ = configure(home)
    strata = config / "strata"
    strata.mkdir(parents=True)
    settings = strata / "settings.toml"
    settings.write_text('mode = "theme"\ntheme = [unterminated\n')
    expect("invalid Strata settings are preserved", not integrations.update_strata("light"))
    expect("invalid settings remain byte-for-byte unchanged",
           settings.read_text() == 'mode = "theme"\ntheme = [unterminated\n')

with tempfile.TemporaryDirectory(prefix="gooarchy-chromium-policy-") as temporary:
    home = Path(temporary)
    _, managed = configure(home)
    managed.mkdir(parents=True)
    calls = []
    chromium = str(home / "bin" / "chromium")

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "sudo":
            content = integrations.expected_policy("#3a5690")
            integrations.POLICY_FILE.write_text(content)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if Path(command[0]).name == "pgrep":
            return SimpleNamespace(returncode=0, stdout="123\n", stderr="")
        if command[0] == chromium:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    with patch.object(integrations.subprocess, "run", side_effect=run), \
            patch.object(integrations.shutil, "which", side_effect=lambda name: chromium if name == "chromium" else "/usr/bin/pgrep"):
        ok = integrations.update_chromium("#3a5690")
    expect("Chromium policy update succeeds without prompts", ok)
    expect("the privileged call carries only the color and fixed writer",
           ["sudo", "-n", integrations.POLICY_WRITER, "3a5690"] in calls, calls)
    expect("the policy contains the Watercolor color and follows system light/dark",
           json.loads(integrations.POLICY_FILE.read_text()) == {
               "BrowserThemeColor": "#3a5690", "BrowserColorScheme": "device"})
    expect("a running Chromium receives a policy refresh without restart",
           [chromium, "--refresh-platform-policy", "--no-startup-window"] in calls, calls)

with tempfile.TemporaryDirectory(prefix="gooarchy-chromium-no-policy-dir-") as temporary:
    home = Path(temporary)
    _, managed = configure(home)
    calls = []
    with patch.object(integrations.subprocess, "run", side_effect=lambda *args, **kwargs: calls.append(args)), \
            patch.object(integrations.shutil, "which", side_effect=lambda name: "/usr/bin/pgrep" if name == "pgrep" else None):
        expect("systems without Chromium are left alone",
               integrations.update_chromium("#3a5690"))
    expect("no privileged call occurs without a Chromium binary", not calls)

with tempfile.TemporaryDirectory(prefix="gooarchy-omarchy-theme-") as temporary:
    home = Path(temporary)
    config, managed = configure(home)
    managed.mkdir(parents=True)
    integrations.OMARCHY = True
    calls = []
    with patch.object(integrations.subprocess, "run", side_effect=lambda *args, **kwargs: calls.append(args)):
        expect("Omarchy leaves both app integrations inert", integrations.apply("light") == 0)
    expect("Omarchy creates no Strata theme or selection", not (config / "strata").exists())
    expect("Omarchy writes no browser policy and invokes no privileged command",
           not integrations.POLICY_FILE.exists() and not calls)

with tempfile.TemporaryDirectory(prefix="gooarchy-install-") as temporary:
    stage = Path(temporary)
    result = subprocess.run(["make", "install", f"DESTDIR={stage}"], cwd=repo,
                            capture_output=True, text=True)
    expect("staged installation validates and installs the policy integration", result.returncode == 0,
           result.stderr or result.stdout)
    policy_writer = stage / "usr/lib/gooarchy-flavorings/gooarchy-browser-policy"
    integration_helper = stage / "usr/lib/gooarchy-flavorings/gooarchy-theme-integrations"
    expect("staged root writer and helper have executable modes",
           policy_writer.is_file() and integration_helper.is_file()
           and policy_writer.stat().st_mode & 0o111 and integration_helper.stat().st_mode & 0o111)
    expect("staged sudoers rule has mode 0440",
           stat.S_IMODE((stage / "etc/sudoers.d/90-gooarchy-browser-policy").stat().st_mode) == 0o440)
    expect("staged license notice is retained",
           (stage / "usr/share/licenses/gooarchy-flavorings/omarchy-browser-policy-MIT.txt").is_file())

print(f"{'all passed' if failures == 0 else f'{failures} failed'}")
raise SystemExit(1 if failures else 0)
