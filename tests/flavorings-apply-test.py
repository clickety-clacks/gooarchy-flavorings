#!/usr/bin/env python3
"""Test gooarchy-flavorings-apply in throwaway home directories, with stand-ins for the desktop
commands it calls (gsettings, dconf, xdg-user-dirs-update, pgrep). No desktop, no real settings.

  tests/flavorings-apply-test.py      exits 1 if anything fails

Covered: existing private files keep mode 0600 (and their contents); new private files are created
0600; a symlinked config is left alone and reported; an app that's running is deferred; an
explicitly set color scheme (even "default") is kept; an interrupted write leaves the old file and
no temporary file; two runs at once leave valid files. A fresh main install is compared with the
candidate install so only the scoped notice is added and baseline paths keep their modes, bytes and
link targets; installed theme entries are also checked against their source.
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


# Compare fresh installs of current main and this candidate in separate throwaway roots.
def install_package(source, stage):
    return subprocess.run(
        ["make", "install", f"DESTDIR={stage}"],
        cwd=source, capture_output=True, text=True,
    )


with tempfile.TemporaryDirectory() as scratch:
    baseline_source = os.path.join(scratch, "baseline-source")
    baseline_stage = os.path.join(scratch, "baseline-stage")
    candidate_stage = os.path.join(scratch, "candidate-stage")
    os.makedirs(baseline_source)

    fetch = subprocess.run(
        ["git", "fetch", "--no-tags", "--depth=1", "origin", "refs/heads/main"],
        cwd=repo, capture_output=True, text=True,
    )
    expect("fetch current main for the baseline install", fetch.returncode == 0,
           (fetch.stderr or fetch.stdout).strip()[-240:])

    baseline_ready = fetch.returncode == 0
    baseline_commit = ""
    if baseline_ready:
        revision = subprocess.run(
            ["git", "rev-parse", "FETCH_HEAD"], cwd=repo, capture_output=True, text=True,
        )
        baseline_ready = revision.returncode == 0
        expect("identify the fetched main commit", baseline_ready,
               (revision.stderr or revision.stdout).strip()[-240:])
        if baseline_ready:
            baseline_commit = revision.stdout.strip()
            tree = subprocess.run(
                ["git", "rev-parse", f"{baseline_commit}^{{tree}}"],
                cwd=repo, capture_output=True, text=True,
            )
            baseline_ready = tree.returncode == 0
            expect("identify the fetched main tree", baseline_ready,
                   (tree.stderr or tree.stdout).strip()[-240:])
            if baseline_ready:
                print(f"BASELINE main {baseline_commit} tree {tree.stdout.strip()}")

    if baseline_ready:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", baseline_commit],
            cwd=repo, capture_output=True,
        )
        expect("archive the fetched main source", archive.returncode == 0,
               (archive.stderr or archive.stdout).decode(errors="replace")[-240:])
        if archive.returncode == 0:
            extract = subprocess.run(
                ["tar", "-xf", "-", "-C", baseline_source],
                input=archive.stdout, capture_output=True,
            )
            baseline_ready = extract.returncode == 0
            expect("extract the fetched main source", baseline_ready,
                   (extract.stderr or extract.stdout).decode(errors="replace")[-240:])
        else:
            baseline_ready = False

    baseline_install = install_package(baseline_source, baseline_stage) if baseline_ready else None
    baseline_installed = baseline_install is not None and baseline_install.returncode == 0
    expect("fresh main package install succeeds in DESTDIR", baseline_installed,
           ((baseline_install.stderr or baseline_install.stdout).strip()[-240:]
            if baseline_install else "baseline source was unavailable"))

    candidate_install = install_package(repo, candidate_stage)
    candidate_installed = candidate_install.returncode == 0
    expect("candidate package install succeeds in DESTDIR", candidate_installed,
           (candidate_install.stderr or candidate_install.stdout).strip()[-240:])

    notice_name = "CC0-1.0-Watercolor-Dream-themes-only.txt"
    notice_path = os.path.join("usr", "share", "licenses", "gooarchy-flavorings", notice_name)
    source_notice = os.path.join(repo, "licenses", notice_name)
    expected_notice = open(source_notice, "rb").read()

    if baseline_installed and candidate_installed:
        baseline_entries = tree_snapshot(baseline_stage)
        candidate_entries = tree_snapshot(candidate_stage)
        changed_baseline = sorted(
            path for path, entry in baseline_entries.items()
            if candidate_entries.get(path) != entry
        )
        added = set(candidate_entries) - set(baseline_entries)
        added_files = {path for path in added if candidate_entries[path][0] != "directory"}
        added_directories = {path for path in added if candidate_entries[path][0] == "directory"}

        required_notice_directories = set()
        parent = os.path.dirname(notice_path)
        while parent:
            if parent not in baseline_entries:
                required_notice_directories.add(parent)
            parent = os.path.dirname(parent)

        expect("candidate adds only the scoped notice to installed file paths",
               added_files == {notice_path} and added_directories == required_notice_directories,
               {"added_files": sorted(added_files),
                "added_directories": sorted(added_directories),
                "required_notice_directories": sorted(required_notice_directories)})
        expect("every baseline installed path keeps its mode, bytes, type and link target",
               not changed_baseline, changed_baseline[:20])

        installed_notice = candidate_entries.get(notice_path)
        expect("the installed CC0 legal text is unchanged and mode 0644",
               installed_notice == ("file", 0o644, expected_notice))
    else:
        expect("candidate adds only the scoped notice to installed file paths", False,
               "baseline or candidate DESTDIR install did not complete")
        expect("every baseline installed path keeps its mode, bytes, type and link target", False,
               "baseline or candidate DESTDIR install did not complete")
        expect("the installed CC0 legal text is unchanged and mode 0644", False,
               "candidate DESTDIR install did not complete")

    themes_match = True
    theme_details = []
    for theme in ("watercolor-dream-light", "watercolor-dream-dark"):
        source_theme = os.path.join(repo, "themes", theme)
        installed_theme = os.path.join(candidate_stage, "usr", "share", "gooarchy-flavorings", "themes", theme)
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

print(f"{'all passed' if not failures else f'{failures} failed'}")
sys.exit(1 if failures else 0)
