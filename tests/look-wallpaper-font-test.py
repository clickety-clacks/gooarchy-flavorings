#!/usr/bin/env python3
"""Focused behavior checks for Gooarchy wallpaper and installed monospace choices."""
import fcntl
import io
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from contextlib import redirect_stderr
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

    omarchy_home = root / "omarchy-home"
    background_link = omarchy_home / ".local/state/omarchy/current/background"
    background_link.parent.mkdir(parents=True)
    previous_omarchy = root / "stock" / "old.webp"
    previous_omarchy.parent.mkdir()
    previous_omarchy.write_bytes(b"old")
    background_link.symlink_to(previous_omarchy)
    output = []
    errors = []
    calls = []

    def set_omarchy_background(command, **kwargs):
        calls.append(command)
        background_link.unlink()
        background_link.symlink_to(command[1])
        return result()

    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.object(wallpaper.shutil, "which", return_value="/tools/omarchy-theme-bg-set"), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        code = wallpaper.set_wallpaper(
            variant, "a.webp", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=set_omarchy_background,
        )
    saved = json.loads((config / "gooarchy" / "wallpapers.json").read_text())
    selected_omarchy = (backgrounds / "a.webp").absolute()
    expect("explicit Omarchy wallpaper set saves Gooarchy choice and uses its existing setter",
           code == 0 and saved == {str(variant): "a.webp"}
           and calls == [["/tools/omarchy-theme-bg-set", str(selected_omarchy)]]
           and f"{previous_omarchy} -> {selected_omarchy}" in "".join(output)
           and "requested by this wallpaper command" in "".join(output)
           and "Visible wallpaper: unconfirmed" in "".join(output),
           "".join(output) + "\n" + "".join(errors))

    current_output = []
    current_errors = []
    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        current_code = wallpaper.dispatch(
            ["current"],
            output=type("Out", (), {"write": lambda self, text: current_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: current_errors.append(text)})(),
            run=lambda command, **kwargs: result(stdout=f"{variant}\n"),
        )
    expect("Omarchy current distinguishes its selected link from the Gooarchy saved choice",
           current_code == 0
           and f"Omarchy effective wallpaper selection: {selected_omarchy}" in "".join(current_output)
           and "Saved choice: a.webp" in "".join(current_output)
           and "Visible wallpaper: unknown" in "".join(current_output))

    external_omarchy = root / "omarchy" / "outside.webp"
    external_omarchy.parent.mkdir()
    external_omarchy.write_bytes(b"external")
    background_link.unlink()
    background_link.symlink_to(external_omarchy)
    changed_output = []
    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        changed_code = wallpaper.dispatch(
            ["current"],
            output=type("Out", (), {"write": lambda self, text: changed_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: None})(),
            run=lambda command, **kwargs: result(stdout=f"{variant}\n"),
        )
    expect("current reports a later Omarchy selection without replacing Gooarchy's saved choice",
           changed_code == 0
           and "Saved choice: a.webp" in "".join(changed_output)
           and f"Omarchy effective wallpaper selection: {external_omarchy}" in "".join(changed_output))

    pick_output = []
    pick_errors = []
    def pick_omarchy_background(command, **kwargs):
        if command == [str(wallpaper.LOOKUP)]:
            return result(stdout=f"{variant}\n")
        return set_omarchy_background(command, **kwargs)

    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.object(wallpaper.shutil, "which", return_value="/tools/omarchy-theme-bg-set"), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        pick_code = wallpaper.dispatch(
            ["pick", "b.png"],
            output=type("Out", (), {"write": lambda self, text: pick_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: pick_errors.append(text)})(),
            run=pick_omarchy_background,
        )
    expect("explicit Omarchy wallpaper pick uses the same selection setter",
           pick_code == 0
           and calls[-1] == ["/tools/omarchy-theme-bg-set", str((backgrounds / "b.png").absolute())]
           and "requested by this wallpaper command" in "".join(pick_output)
           and json.loads((config / "gooarchy" / "wallpapers.json").read_text())
           == {str(variant): "b.png"}, "".join(pick_output) + "\n" + "".join(pick_errors))

    background_link.unlink()
    background_link.symlink_to(selected_omarchy)

    def next_omarchy_background(command, **kwargs):
        calls.append(command)
        background_link.unlink()
        background_link.symlink_to(command[1])
        return result()

    next_output = []
    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.object(wallpaper.shutil, "which", return_value="/tools/omarchy-theme-bg-set"), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        next_code = wallpaper.dispatch(
            ["next"],
            output=type("Out", (), {"write": lambda self, text: next_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: None})(),
            run=lambda command, **kwargs: result(stdout=f"{variant}\n")
            if command == [str(wallpaper.LOOKUP)] else next_omarchy_background(command, **kwargs),
        )
    expect("Omarchy next advances from the effective installed image through the existing setter",
           next_code == 0 and calls[-1] == ["/tools/omarchy-theme-bg-set", str((backgrounds / "b.png").absolute())]
           and "requested by this wallpaper command" in "".join(next_output))

    failed_set_errors = []
    failed_set_output = []
    def failed_omarchy_set(command, **kwargs):
        if command == [str(wallpaper.LOOKUP)]:
            return result(stdout=f"{variant}\n")
        calls.append(command)
        return result(stderr="Omarchy setter failed", returncode=1)

    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.object(wallpaper.shutil, "which", return_value="/tools/omarchy-theme-bg-set"), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        failed_set_code = wallpaper.dispatch(
            ["set", "a.webp"],
            output=type("Out", (), {"write": lambda self, text: failed_set_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: failed_set_errors.append(text)})(),
            run=failed_omarchy_set,
        )
    expect("failed Omarchy apply is reported while the successfully saved wallpaper request exits successfully",
           failed_set_code == 0 and "Omarchy wallpaper apply failed" in "".join(failed_set_errors)
           and f"{(backgrounds / 'b.png').absolute()} -> {(backgrounds / 'b.png').absolute()}"
           in "".join(failed_set_errors)
           and "Gooarchy choice remains saved" in "".join(failed_set_errors)
           and "Visible wallpaper: unconfirmed" in "".join(failed_set_output),
           "".join(failed_set_errors))

    unavailable_errors = []
    with patch.object(wallpaper, "_omarchy_owns_appearance", return_value=True), \
            patch.object(wallpaper.shutil, "which", return_value=None), \
            patch.dict(os.environ, {"HOME": str(omarchy_home)}):
        unavailable_code = wallpaper.set_wallpaper(
            variant, "a.webp", type("Out", (), {"write": lambda self, text: None})(),
            type("Err", (), {"write": lambda self, text: unavailable_errors.append(text)})(),
            run=lambda command, **kwargs: (_ for _ in ()).throw(AssertionError("no setter should run")),
        )
    expect("a missing Omarchy setter warns while keeping the requested Gooarchy choice saved",
           unavailable_code == 0 and "omarchy-theme-bg-set was not found" in "".join(unavailable_errors)
           and f"{(backgrounds / 'b.png').absolute()} -> {(backgrounds / 'b.png').absolute()}"
           in "".join(unavailable_errors)
           and json.loads((config / "gooarchy" / "wallpapers.json").read_text())
           == {str(variant): "a.webp"})

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
    expect("an active owner without an applied-image acknowledgement warns without failing the saved choice",
           code == 0 and "live application is unconfirmed" in "".join(errors)
           and "will apply when the wallpaper owner starts" not in "".join(output))

    output.clear()
    errors.clear()
    def timed_out_refresh(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 20))

    with patch.object(wallpaper, "wallpaper_owner_active", return_value=True), \
            patch.object(wallpaper, "applied_wallpaper", return_value=None):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=timed_out_refresh,
        )
    expect("an unconfirmed refresh timeout warns without failing the saved choice",
           code == 0 and "live application is unconfirmed" in "".join(errors)
           and "timed out" in "".join(errors))

    output.clear()
    errors.clear()
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=False), \
            patch.object(wallpaper, "applied_wallpaper", return_value=None):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(stderr="refresh failed", returncode=1),
        )
    expect("optional refresh failure warns without failing a successfully saved wallpaper choice",
           code == 0 and "Saved wallpaper: b.png" in "".join(errors)
           and "refresh failed" in "".join(errors))

    output.clear()
    errors.clear()
    with patch.object(wallpaper, "wallpaper_owner_active", return_value=True), \
            patch.object(wallpaper, "applied_wallpaper", return_value=str(image)):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(stderr="Scottland reapply failed", returncode=1),
        )
    expect("optional integration failure does not fail a saved wallpaper that was applied",
           code == 0 and "Saved and applied wallpaper: b.png" in "".join(output)
           and "optional theme integration failed" in "".join(errors)
           and "Scottland reapply failed" in "".join(errors))

    output.clear()
    errors.clear()
    with patch.object(wallpaper, "atomic_write_preferences", side_effect=OSError("read-only")):
        code = wallpaper.set_wallpaper(
            variant, "b.png", type("Out", (), {"write": lambda self, text: output.append(text)})(),
            type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=lambda command, **kwargs: result(),
        )
    expect("failure to save the requested wallpaper choice still returns failure",
           code != 0 and "Wallpaper was not saved" in "".join(errors))

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

with tempfile.TemporaryDirectory(prefix="gooarchy-refresh-unconfirmed-timeout-") as temporary:
    runtime = Path(temporary)
    lock_path = runtime / refresh.WALLPAPER_RUNTIME_LOCK
    held_lock = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    errors = io.StringIO()
    try:
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime),
                                     "WAYLAND_DISPLAY": "wayland-test"}), \
                patch.object(refresh.time, "monotonic", side_effect=[0, 6]), \
                redirect_stderr(errors):
            refresh._refresh_running_wallpaper()
        expect("an active owner's refresh timeout warns and continues without claiming application",
               "live application is unconfirmed" in errors.getvalue()
               and (runtime / refresh.WALLPAPER_REFRESH_REQUEST).is_file())
    finally:
        fcntl.flock(held_lock, fcntl.LOCK_UN)
        os.close(held_lock)

with tempfile.TemporaryDirectory(prefix="gooarchy-refresh-optional-scottland-failure-") as temporary:
    root = Path(temporary)
    current = root / "config" / "gooarchy" / "current-theme"
    light = root / "themes" / "new-light"
    dark = root / "themes" / "new-dark"
    light.mkdir(parents=True)
    dark.mkdir(parents=True)
    lookup = root / "gooarchy-theme-lookup"
    hooks = root / "scottland"
    command = hooks / "libexec" / "scottland-color-scheme"
    command.parent.mkdir(parents=True)
    command.write_text("mocked command\n")
    calls = []
    wallpaper_steps = []
    errors = io.StringIO()

    def optional_failure_run(argv, **kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == (str(lookup), "--pair"):
            return result(stdout=json.dumps({"light": str(light), "dark": str(dark)}))
        return result(stderr="busy", returncode=1)

    with patch.dict(os.environ, {
            "OMARCHY_PATH": str(root / "omarchy-not-installed"),
            "XDG_RUNTIME_DIR": str(root / "runtime"),
            "WAYLAND_DISPLAY": "wayland-test",
            "SCOTTLAND_HOOKS": str(hooks),
    }), patch.object(refresh, "_refresh_running_wallpaper",
                     side_effect=lambda: wallpaper_steps.append("continued")), \
            redirect_stderr(errors):
        refresh_code = refresh.refresh(current, lookup, run=optional_failure_run)

    expect("failed optional Scottland reapply warns while theme links and wallpaper refresh continue",
           refresh_code == 0
           and os.readlink(current / "light") == str(light)
           and os.readlink(current / "dark") == str(dark)
           and calls == [(str(lookup), "--pair"), (str(command), "once")]
           and wallpaper_steps == ["continued"]
           and "Scottland color-scheme reapply failed: busy" in errors.getvalue()
           and "theme links were updated and wallpaper refresh will continue" in errors.getvalue())

    command.unlink()
    calls.clear()
    wallpaper_steps.clear()
    errors = io.StringIO()
    with patch.dict(os.environ, {
            "OMARCHY_PATH": str(root / "omarchy-not-installed"),
            "XDG_RUNTIME_DIR": str(root / "runtime"),
            "WAYLAND_DISPLAY": "wayland-test",
            "SCOTTLAND_HOOKS": str(hooks),
    }), patch.object(refresh, "_refresh_running_wallpaper",
                     side_effect=lambda: wallpaper_steps.append("continued")), \
            redirect_stderr(errors):
        missing_code = refresh.refresh(current, lookup, run=optional_failure_run)

    expect("unavailable optional Scottland reapply warns while wallpaper refresh continues",
           missing_code == 0
           and calls == [(str(lookup), "--pair")]
           and wallpaper_steps == ["continued"]
           and "Scottland color-scheme reapply is unavailable" in errors.getvalue()
           and "theme links were updated and wallpaper refresh will continue" in errors.getvalue())

    calls.clear()
    wallpaper_steps.clear()
    errors = io.StringIO()
    command.write_text("mocked command\n")

    def unexpected_scottland_failure(argv, **kwargs):
        calls.append(tuple(argv))
        if tuple(argv) == (str(lookup), "--pair"):
            return result(stdout=json.dumps({"light": str(light), "dark": str(dark)}))
        raise RuntimeError("unexpected optional integration failure")

    with patch.dict(os.environ, {
            "OMARCHY_PATH": str(root / "omarchy-not-installed"),
            "XDG_RUNTIME_DIR": str(root / "runtime"),
            "WAYLAND_DISPLAY": "wayland-test",
            "SCOTTLAND_HOOKS": str(hooks),
    }), patch.object(refresh, "_refresh_running_wallpaper",
                     side_effect=lambda: wallpaper_steps.append("continued")), \
            redirect_stderr(errors):
        unexpected_code = refresh.refresh(current, lookup, run=unexpected_scottland_failure)

    expect("unexpected optional Scottland exception cannot skip wallpaper refresh or fail theme refresh",
           unexpected_code == 0
           and calls == [(str(lookup), "--pair"), (str(command), "once")]
           and wallpaper_steps == ["continued"]
           and "unexpected optional integration failure" in errors.getvalue()
           and "wallpaper refresh will continue" in errors.getvalue())

with tempfile.TemporaryDirectory(prefix="gooarchy-refresh-wallpaper-error-") as temporary:
    root = Path(temporary)
    current = root / "config" / "gooarchy" / "current-theme"
    light = root / "themes" / "light"
    dark = root / "themes" / "dark"
    light.mkdir(parents=True)
    dark.mkdir(parents=True)
    lookup = root / "gooarchy-theme-lookup"
    errors = io.StringIO()
    with patch.dict(os.environ, {"OMARCHY_PATH": str(root / "omarchy-not-installed")}), \
            patch.object(refresh, "_refresh_running_wallpaper",
                         side_effect=RuntimeError("owner is unavailable")), \
            redirect_stderr(errors):
        refresh_code = refresh.refresh(
            current, lookup,
            run=lambda argv, **kwargs: result(
                stdout=json.dumps({"light": str(light), "dark": str(dark)})),
        )
    expect("optional wallpaper-owner failure warns without failing the completed theme-link refresh",
           refresh_code == 0 and os.readlink(current / "light") == str(light)
           and "Gooarchy wallpaper refresh failed: owner is unavailable" in errors.getvalue()
           and "theme links remain updated" in errors.getvalue())


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
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return result(stdout="Fira Code\t100\nIBM Plex Mono\t100\nProportional Sans\t0\n")

    with patch.object(
            integrations.shutil,
            "which",
            side_effect=lambda name: "/tools/fc-list" if name == "fc-list" else None,
    ):
        families = integrations.font_inventory(run=run)
        output = []
        errors = []
        code = integrations.font_command(
            ["list"],
            output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
            run=run,
        )
    expect("Fontconfig inventory remains available without Ghostty",
           families == ["Fira Code", "IBM Plex Mono"] and len(calls) == 2
           and code == 0 and "".join(output) == "Fira Code\nIBM Plex Mono\n"
           and "Ghostty compatibility is unavailable" in "".join(errors),
           str(families) + " / " + "".join(errors))

    with patch.object(integrations.shutil, "which", return_value="/tools/ghostty"), \
            patch.object(integrations, "ghostty_font_families", return_value={"Fira Code"}):
        supported_output = []
        supported_code = integrations.font_command(
            ["list"],
            output=type("Out", (), {"write": lambda self, text: supported_output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: None})(),
            run=run,
        )
    expect("Fontconfig list is not filtered by Ghostty's optional inventory",
           supported_code == 0 and "".join(supported_output) == "Fira Code\nIBM Plex Mono\n",
           "".join(supported_output))

with tempfile.TemporaryDirectory(prefix="gooarchy-font-write-") as temporary:
    config = Path(temporary) / "config"
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
            patch.object(integrations, "ghostty_font_families", return_value={"Fira Code"}), \
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
    expect("font set writes Fontconfig and Ghostty preferences while preserving user config bytes",
           code == 0 and parsed.findtext(".//edit/string") == "Fira Code"
           and fragment == 'font-family = ""\nfont-family = "Fira Code"\n'
           and user_config.read_bytes().startswith(original)
           and user_config.read_bytes().count(integrations.GHOSTTY_INCLUDE_MARKER.encode()) == 1
           and "Fontconfig effective monospace: Fira Code" in "".join(output), errors)

    expect("the marked Ghostty include is idempotent",
           integrations._ghostty_include(user_config.read_bytes().decode("utf-8"), ghostty_fragment)
           == user_config.read_bytes().decode("utf-8"))

with tempfile.TemporaryDirectory(prefix="gooarchy-font-ghostty-unsupported-") as temporary:
    config = Path(temporary) / "config"
    integrations.CONFIG = config
    integrations.OMARCHY = False
    errors = []
    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]), \
            patch.object(integrations, "ghostty_font_families", return_value={"Other Mono"}), \
            patch.object(integrations, "ghostty_configured_family") as configured, \
            patch.object(integrations, "fontconfig_effective_family",
                         side_effect=["Monospace", "Fira Code"]), \
            patch.object(integrations.shutil, "which", return_value="/tools/ghostty"):
        code = integrations.set_font(
            "Fira Code", output=type("Out", (), {"write": lambda self, text: None})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    fontconfig = config / "fontconfig" / "conf.d" / "99-gooarchy-monospace.conf"
    expect("known Ghostty incompatibility disables only Ghostty and preserves Fontconfig selection",
           code == 0 and fontconfig.is_file()
           and not (config / "gooarchy" / "font.ghostty").exists()
           and not (config / "ghostty" / "config").exists()
           and not configured.called
           and "does not list Fira Code as supported" in "".join(errors)
           and "Fontconfig remains saved as Fira Code" in "".join(errors), errors)

with tempfile.TemporaryDirectory(prefix="gooarchy-font-inventory-unknown-") as temporary:
    config = Path(temporary) / "config"
    integrations.CONFIG = config
    integrations.OMARCHY = False
    errors = []
    with patch.object(integrations, "font_inventory", side_effect=RuntimeError("fc-list failed")), \
            patch.object(integrations, "fontconfig_effective_family",
                         side_effect=["Monospace", "Fira Code"]), \
            patch.object(integrations.shutil, "which", return_value=None):
        code = integrations.set_font(
            "Fira Code", output=type("Out", (), {"write": lambda self, text: None})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    fontconfig = config / "fontconfig" / "conf.d" / "99-gooarchy-monospace.conf"
    expect("unknown Fontconfig inventory warns but does not block the requested preference write",
           code == 0 and fontconfig.is_file()
           and b"<string>Fira Code</string>" in fontconfig.read_bytes()
           and "Fontconfig inventory is unknown" in "".join(errors)
           and "Fontconfig could not confirm that Fira Code is installed" in "".join(errors), errors)

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
            patch.object(integrations, "ghostty_font_families", return_value={"Fira Code"}), \
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
    fontconfig = config / "fontconfig" / "conf.d" / "99-gooarchy-monospace.conf"
    expect("Ghostty rollback preserves CRLF bytes without rolling back Fontconfig",
           code == 0 and failed_after_config_write["value"] and user_config.read_bytes() == original
           and fontconfig.is_file()
           and b"<string>Fira Code</string>" in fontconfig.read_bytes()
           and "Fontconfig remains saved as Fira Code" in "".join(errors), errors)

with tempfile.TemporaryDirectory(prefix="gooarchy-font-invalid-") as temporary:
    config = Path(temporary) / "config"
    integrations.CONFIG = config
    integrations.OMARCHY = False
    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]):
        code = integrations.set_font(
            "Missing Family", output=type("Out", (), {"write": lambda self, text: None})(),
            error=type("Err", (), {"write": lambda self, text: None})(),
        )
    expect("an unavailable font is rejected before any configuration write",
           code == 2 and not config.exists())

with tempfile.TemporaryDirectory(prefix="gooarchy-omarchy-font-set-") as temporary:
    config = Path(temporary) / "config"
    integrations.CONFIG = config
    integrations.OMARCHY = True
    output = []
    errors = []
    with patch.object(integrations, "font_inventory", return_value=["Fira Code"]), \
            patch.object(integrations, "fontconfig_effective_family",
                         side_effect=["Monospace", "Fira Code"]), \
            patch.object(integrations.shutil, "which", return_value=None):
        code = integrations.set_font(
            "Fira Code", output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    fontconfig = config / "fontconfig" / "conf.d" / "99-gooarchy-monospace.conf"
    ghostty_fragment = config / "gooarchy" / "font.ghostty"
    ghostty_config = config / "ghostty" / "config"
    expect("explicit Omarchy font command writes both owned preferences and reports the override",
           code == 0 and fontconfig.is_file() and ghostty_fragment.is_file()
           and integrations.GHOSTTY_INCLUDE_MARKER in ghostty_config.read_text()
           and "Omarchy Fontconfig monospace selection: Monospace -> Fira Code" in "".join(output)
           and "Ghostty: unavailable" in "".join(errors),
           "".join(output) + "\n" + "".join(errors))

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

with tempfile.TemporaryDirectory(prefix="gooarchy-font-current-ghostty-unreadable-") as temporary:
    integrations.CONFIG = Path(temporary) / "config"
    integrations.OMARCHY = False
    output = []
    errors = []
    with patch.object(integrations, "read_saved_font", return_value="Fira Code"), \
            patch.object(integrations, "fontconfig_effective_family", return_value="Fira Code"), \
            patch.object(integrations, "ghostty_configured_family",
                         return_value=(None, "Ghostty configuration is unreadable")), \
            patch.object(integrations.shutil, "which", return_value="/tools/ghostty"):
        code = integrations.font_current(
            output=type("Out", (), {"write": lambda self, text: output.append(text)})(),
            error=type("Err", (), {"write": lambda self, text: errors.append(text)})(),
        )
    expect("font current retains Fontconfig state when Ghostty configuration is unreadable",
           code == 0 and "Fontconfig effective monospace: Fira Code" in "".join(output)
           and "Ghostty configured family: unknown" in "".join(output)
           and "Ghostty configuration is unreadable" in "".join(errors)
           and "Fontconfig state remains available" in "".join(errors),
           "".join(output) + "\n" + "".join(errors))

print(f"{failures} failure(s)")
raise SystemExit(1 if failures else 0)
