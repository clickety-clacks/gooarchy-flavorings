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


def omarchy_system(home, strata=False):
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
    omarchy_system(home)
    write(os.path.join(home, ".claude.json"), json.dumps({"preferredNotifChannel": "terminal_bell"}))
    write(os.path.join(home, ".stubs", "tmux"), "#!/bin/sh\n", executable=True)
    write(os.path.join(home, ".stubs", "mosh-client"), "#!/bin/sh\n", executable=True)
    stubs(os.path.join(home, ".stubs"))
    r = apply(home, omarchy=omarchy)
    text = read(os.path.join(home, ".config", "scottland", "override-report.d", "gooarchy-flavorings.txt")) or ""
    expect("Omarchy: a bell the user set themselves is not reported", "- Keys: Claude Code" not in text, text)
    expect("Omarchy: a type with no default before is reported",
           "- Keys: Web pages\n  Was: No default app\n  Now: Chromium (chromium.desktop) in a Scottland session" in text, text)
    expect("Omarchy: tmux and mosh titles the user doesn't set are reported",
           all(f"- Keys: {k}\n" in text for k in ("tmux set-titles", "tmux set-titles-string", "mosh window titles")), text)

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
