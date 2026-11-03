"""License and dependency audit for the image.

Python: every installed distribution, including transitive ones. Web: every package pnpm resolves.
The allowlist is permissive licenses plus the two weak-copyleft libraries the stack already uses
(psycopg is LGPL-3.0; py-vapid and certifi are MPL-2.0). GPL, AGPL, SSPL, Elastic, and unknown
licenses fail the build.

Run: uv run python scripts/license_audit.py
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PERMISSIVE = {
    "0BSD",
    "AFL-2.1",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "BSL-1.0",
    "CC-BY-4.0",
    "CC0-1.0",
    "CNRI-Python",
    "ISC",
    "MIT",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "Zlib",
}
WEAK_COPYLEFT = {"LGPL-2.1", "LGPL-3.0", "MPL-2.0"}
ALLOWED = PERMISSIVE | WEAK_COPYLEFT
FORBIDDEN = {
    "AGPL-3.0",
    "Elastic-2.0",
    "GPL-2.0",
    "GPL-3.0",
    "SSPL-1.0",
    "UNKNOWN",
}

SKIP_PYTHON = {"fin-agent"}


def _classify(text: str) -> str:
    lowered = text.strip().lower()
    if not lowered:
        return "UNKNOWN"
    if re.search(r"\b(?:agpl|affero)\b", lowered):
        return "AGPL-3.0"
    if re.search(r"\b(?:lgpl|lesser general public)\b", lowered):
        return "LGPL-3.0"
    if re.search(r"\b(?:gplv?[23]?|gnu general public)\b", lowered):
        return "GPL-3.0"
    if "server side public" in lowered or "sspl" in lowered:
        return "SSPL-1.0"
    if "elastic license" in lowered:
        return "Elastic-2.0"
    if "mozilla public" in lowered or "mpl-2.0" in lowered:
        return "MPL-2.0"
    if "apache" in lowered:
        return "Apache-2.0"
    if "blue oak" in lowered or "blueoak" in lowered:
        return "BlueOak-1.0.0"
    if "cc-by-4" in lowered or "creative commons attribution 4" in lowered:
        return "CC-BY-4.0"
    if "cc0" in lowered:
        return "CC0-1.0"
    if "academic free" in lowered or "afl-2.1" in lowered:
        return "AFL-2.1"
    if "boost software" in lowered or "bsl-1.0" in lowered:
        return "BSL-1.0"
    if "cnri" in lowered:
        return "CNRI-Python"
    if "python software foundation" in lowered or re.search(r"\bpsf\b", lowered):
        return "PSF-2.0"
    if "python-2.0" in lowered:
        return "Python-2.0"
    if "unlicense" in lowered or "public domain" in lowered:
        return "Unlicense"
    if "zlib" in lowered:
        return "Zlib"
    if "bsd-3" in lowered or "3-clause bsd" in lowered:
        return "BSD-3-Clause"
    if "bsd-2" in lowered or "2-clause bsd" in lowered:
        return "BSD-2-Clause"
    if "neither the name" in lowered:
        return "BSD-3-Clause"
    if "redistribution and use in source and binary forms" in lowered or re.search(
        r"\bbsd\b", lowered
    ):
        return "BSD-3-Clause"
    if re.search(r"\bisc\b", lowered):
        return "ISC"
    if re.search(r"\bmit\b", lowered) or "permission is hereby granted" in lowered:
        return "MIT"
    return "UNKNOWN"


def _expressions(raw: str) -> set[str]:
    """Split an SPDX expression or a full license text into known license IDs."""
    expression = raw.strip()
    if len(expression) <= 120:
        found = {
            _classify(token)
            for token in re.split(r"\s+(?:and|or)\s+", expression, flags=re.IGNORECASE)
        }
    else:
        found = {_classify(expression)}
    known = {item for item in found if item != "UNKNOWN"}
    return known or {"UNKNOWN"}


def _license_text(dist: metadata.Distribution) -> str:
    chunks: list[str] = []
    for entry in dist.files or ():
        name = entry.name.upper()
        if "LICENSE" in name or "COPYING" in name or name == "NOTICE":
            try:
                path = Path(dist.locate_file(entry))
                if path.is_file() and path.stat().st_size < 200_000:
                    chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(chunks)


def python_packages() -> dict[str, dict[str, str]]:
    packages: dict[str, dict[str, str]] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in SKIP_PYTHON:
            continue
        candidates = [
            dist.metadata.get("License-Expression") or "",
            dist.metadata.get("License") or "",
            " ".join(
                value
                for value in (dist.metadata.get_all("Classifier") or ())
                if value.startswith("License ::")
            ),
        ]
        expressions: set[str] = set()
        for candidate in candidates:
            if candidate.strip():
                expressions |= _expressions(candidate)
        expressions.discard("UNKNOWN")
        if not expressions:
            expressions = _expressions(_license_text(dist))
        packages[name] = {
            "version": dist.version,
            "license": " AND ".join(sorted(expressions or {"UNKNOWN"})),
        }
    return packages


def web_packages() -> dict[str, dict[str, str]] | None:
    web = ROOT / "web"
    if not (web / "node_modules").is_dir():
        return None
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if pnpm is None:
        return None
    result = subprocess.run(
        [pnpm, "licenses", "list", "--json"],
        cwd=web,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr[-1000:], file=sys.stderr)
        return None
    payload = json.loads(result.stdout)
    packages: dict[str, dict[str, str]] = {}
    for expression, entries in payload.items():
        normalized = " AND ".join(sorted(_expressions(expression)))
        for entry in entries:
            packages[entry["name"]] = {
                "version": ", ".join(entry.get("versions") or []),
                "license": normalized,
            }
    return packages


def failures(packages: dict[str, dict[str, str]]) -> list[str]:
    bad: list[str] = []
    for name, info in sorted(packages.items()):
        licenses = {part.strip() for part in info["license"].split(" AND ")}
        if any(part in FORBIDDEN for part in licenses) or licenses & {"UNKNOWN"}:
            bad.append(f"{name} {info['version']}: {info['license']}")
    return bad


def direct_dependencies() -> tuple[set[str], set[str]]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python = {
        re.split(r"[<>=!~\[\s]", item, maxsplit=1)[0].lower()
        for item in pyproject["project"]["dependencies"]
    }
    package_json = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    web = {
        re.split(r"[<>=!~\[\s]", item, maxsplit=1)[0].lower()
        for item in (package_json.get("dependencies") or {})
    }
    return python, web


def notices_failures(python: set[str], web: set[str]) -> list[str]:
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    missing: list[str] = []
    for name in sorted(python | web):
        needle = name.replace("_", "-").lower()
        if needle not in text:
            missing.append(name)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--web-only", action="store_true")
    args = parser.parse_args()

    python = python_packages()
    web = None if args.python_only else web_packages()
    if args.web_only:
        python = {}

    failed = False
    for label, packages in (("python", python), ("web", web)):
        if packages is None:
            print(f"{label}: skipped (web/node_modules is absent; run pnpm install first)")
            continue
        bad = failures(packages)
        weak = sorted(
            name
            for name, info in packages.items()
            if {part.strip() for part in info["license"].split(" AND ")} & WEAK_COPYLEFT
        )
        print(f"{label}: {len(packages)} packages, {len(bad)} forbidden or unknown")
        if weak:
            print(f"{label}: weak-copyleft, noticed: {', '.join(weak)}")
        for line in bad:
            print(f"  FAIL {line}")
        failed = failed or bool(bad)

    if not args.web_only:
        py_direct, web_direct = direct_dependencies()
        missing = notices_failures(py_direct, web_direct)
        if missing:
            print("THIRD_PARTY_NOTICES.md does not name: " + ", ".join(missing))
            failed = True
        else:
            print("notices: every direct dependency is named")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
