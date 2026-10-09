#!/usr/bin/env python3
"""Check the Weather and System Stats WG7 package matches and staged install paths."""
import pathlib
import re
import subprocess
import tempfile
import tomllib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGES = {
    "weather": {
        "directory": "gooarchy-weather",
        "app_id": "scottland-weather",
        "source": ROOT / "scottland/widgets/weather",
    },
    "system-stats": {
        "directory": "gooarchy-system-stats",
        "app_id": "scottland-system-stats",
        "source": ROOT / "scottland/widgets/system-stats",
    },
}
PACKAGE_FILES = {"widget.toml", "shell.qml", "data.py"}


class WidgetPackageTests(unittest.TestCase):
    def test_manifests_match_the_core_app_ids(self):
        app_ids = {name: package["app_id"] for name, package in PACKAGES.items()}
        self.assertNotEqual(app_ids["weather"], app_ids["system-stats"])
        patterns = {}
        for package_name, package in PACKAGES.items():
            with self.subTest(package=package_name):
                manifest = tomllib.loads((package["source"] / "widget.toml").read_text())
                self.assertEqual(manifest.get("id"), package["directory"])
                self.assertEqual(manifest.get("exec"), "quickshell -p %d/shell.qml")
                app_patterns = manifest.get("apps")
                self.assertIsInstance(app_patterns, list)
                self.assertEqual(len(app_patterns), 1)
                pattern = app_patterns[0]
                patterns[package_name] = pattern
                expected_pattern = f"^{re.escape(package['app_id'])}$"
                self.assertEqual(pattern, expected_pattern)
                self.assertTrue(pattern.startswith("^") and pattern.endswith("$"))
                self.assertIsNotNone(re.fullmatch(pattern, app_ids[package_name], re.IGNORECASE))

        self.assertIsNone(
            re.fullmatch(patterns["weather"], app_ids["system-stats"], re.IGNORECASE)
        )
        self.assertIsNone(
            re.fullmatch(patterns["system-stats"], app_ids["weather"], re.IGNORECASE)
        )

    def test_staged_install_contains_only_the_two_widget_packages(self):
        with tempfile.TemporaryDirectory(prefix="gooarchy-widget-install-") as temporary:
            subprocess.run(
                ["make", "install", f"DESTDIR={temporary}"],
                cwd=ROOT,
                check=True,
            )

            widget_root = pathlib.Path(temporary) / "usr/share/scottland/widgets"
            self.assertTrue(widget_root.is_dir())
            installed = {
                path.relative_to(widget_root).as_posix()
                for path in widget_root.rglob("*")
            }
            expected = {
                f"{package['directory']}/{filename}"
                for package in PACKAGES.values()
                for filename in PACKAGE_FILES
            }
            expected.update(package["directory"] for package in PACKAGES.values())
            self.assertEqual(installed, expected)


if __name__ == "__main__":
    unittest.main()
