#!/usr/bin/env python3
"""Check Flavorings' package-owned Agentd user-unit enablement links."""

from pathlib import Path
import os
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
EXPECTED_LINKS = {
    "agentd.service": "../agentd.service",
    "agentd-attention.service": "../agentd-attention.service",
}


def main():
    with tempfile.TemporaryDirectory(prefix="gooarchy-agentd-attention-test-") as temporary:
        root = Path(temporary)
        tools = root / "tools"
        tools.mkdir()
        visudo = tools / "visudo"
        visudo.write_text("#!/bin/sh\nexit 0\n")
        visudo.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{tools}{os.pathsep}{env.get('PATH', '')}"
        result = subprocess.run(
            ["make", "install", f"DESTDIR={root / 'stage'}"],
            cwd=REPO,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"staged make install failed with exit {result.returncode}")

        unit_dir = root / "stage/usr/lib/systemd/user"
        wants = unit_dir / "default.target.wants"
        for unit, target in EXPECTED_LINKS.items():
            link = wants / unit
            if not link.is_symlink():
                raise SystemExit(f"missing package enablement link: {link.relative_to(root / 'stage')}")
            actual = os.readlink(link)
            if actual != target:
                raise SystemExit(f"wrong target for {unit}: expected {target!r}, got {actual!r}")

            provided_unit = unit_dir / unit
            if provided_unit.exists():
                raise SystemExit(f"Flavorings must not copy Agentd's unit: {provided_unit}")

        home = root / "home"
        if home.exists():
            raise SystemExit("staged install wrote into a user home")

    print("Agentd and bridge units are globally enabled through vendor target links; no units or home files are copied.")


if __name__ == "__main__":
    main()
