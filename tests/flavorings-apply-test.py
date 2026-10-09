#!/usr/bin/env python3
"""Test gooarchy-flavorings-apply and gooarchy-theme in throwaway home directories, with
stand-ins for the desktop commands they call (gsettings, dconf, xdg-user-dirs-update, pgrep). No
desktop, no real settings.

  tests/flavorings-apply-test.py      exits 1 if anything fails

Covered: existing private files keep mode 0600 (and their contents); new private files are created
0600; a symlinked config is left alone and reported; an app that's running is deferred; an
explicitly set color scheme (even "default") is kept; an interrupted write leaves the old file and
no temporary file; two runs at once leave valid files; gooarchy-theme's light/dark/toggle set the
same color-scheme values as before, and both setup/theme Sunlight notices explain transition-only
manual picks and that disabling Sunlight keeps one mode permanently.
"""
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tool = os.path.join(repo, "bin", "gooarchy-flavorings-apply")
theme_tool = os.path.join(repo, "bin", "gooarchy-theme")
failures = 0


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
    }
    for name, text in scripts.items():
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)


def apply(home, **kw):
    bindir = os.path.join(home, ".stubs")
    stubs(bindir, **kw)
    env = {"HOME": home, "PATH": f"{bindir}:/usr/bin:/bin", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}
    return subprocess.run([sys.executable, tool], env=env, capture_output=True, text=True)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


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
    expect("Sunlight-on setup note says manual picks hold until sunrise or sunset",
           "a mode you pick manually holds until the next sunrise or sunset" in r.stdout
           and "when Sunlight switches it" in r.stdout
           and "only if you want to keep one mode permanently" in r.stdout
           and "every few seconds" not in r.stdout, r.stdout)

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
    env = {"HOME": home, "PATH": f"{bindir}:/usr/bin:/bin", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}
    procs = [subprocess.Popen([sys.executable, tool], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(4)]
    for p in procs:
        p.wait()
    data = json.load(open(os.path.join(home, ".claude.json")))
    expect("concurrent runs leave valid files", data == {"keep": 1, "preferredNotifChannel": "terminal_bell"}, data)

# gooarchy-theme: light/dark/toggle keep setting the same color-scheme values as before, and the
# Sunlight note's text and presence follow ~/.config/scottland/solar.ini's [solar] enabled key.
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
    expect("Sunlight-on theme note says manual picks last until transition",
           "a mode you pick manually holds until the next sunrise or sunset" in r.stderr
           and "when Sunlight switches it" in r.stderr
           and "only if you want to keep one mode permanently" in r.stderr
           and "every few seconds" not in r.stderr, r.stderr)

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
