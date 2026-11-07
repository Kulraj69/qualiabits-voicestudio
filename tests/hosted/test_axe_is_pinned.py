"""The accessibility tool the browser run injects is pinned, licensed and named.

M4's slice A added axe-core to the browser suite: it is what finds a form field
with no label or a button whose only content is a glyph, and neither the width
pass nor the theme pass would have. That makes it evidence, and evidence has to
say which tool produced it.

Three things are checked here, and each of them was a way for the run's claim to
become hollow without anyone editing a check:

  * the dependency is pinned to an exact version in ``package.json`` (a range
    would let a different axe run tomorrow and still be cited as this run's),
  * ``package-lock.json`` records that version with an integrity hash and names
    the licence, so a reader can find out what they are running without a
    network call,
  * the file the suite actually injects hashes to the sha256 the run recorded,
    and the acceptance page names both the pin and the licence. A vendored copy
    or a second install path would break that equality and this test would say
    so.

The last check needs ``node_modules``. It is the only one that does, and when
the directory is not there (a cold clone) the test says that the pin and the
recorded hash are still checked and the install was not available to compare
against -- it does not skip the whole file, because the pin and the record are
exactly the part that has to be right before anyone installs anything.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "hosted" / "web"
PACKAGE_JSON = WEB / "package.json"
LOCKFILE = WEB / "package-lock.json"
HARNESS = WEB / "e2e" / "dashboard.mjs"
PAGE = REPO_ROOT / "hosted" / "M4-ACCEPTANCE.md"
INSTALLED = WEB / "node_modules" / "axe-core"
AXE_MIN_JS = INSTALLED / "axe.min.js"
#: What axe-core 4.14.0 declares, in its own package metadata and its own
#: LICENSE.txt. Named here as a constant because the page has to name it too.
LICENCE = "MPL-2.0"
#: The browser run's own evidence file, in the fork's hermes-reports/ directory.
EVIDENCE_RE = re.compile(r"hermes-reports/(M4-slot\d+-web-e2e-r\d+)/result\.json")
EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def package() -> dict:
    return json.loads(PACKAGE_JSON.read_text())


def lock() -> dict:
    return json.loads(LOCKFILE.read_text())


def pinned_version() -> str:
    """The exact version `package.json` pins, with the pin itself asserted."""
    declared = package()["devDependencies"]["axe-core"]
    assert EXACT_VERSION_RE.match(declared), (
        f"axe-core is declared as {declared!r}; an acceptance page cannot cite a run "
        "whose tool version floats, so this has to be an exact x.y.z"
    )
    return declared


def cited_run() -> dict:
    matches = EVIDENCE_RE.findall(PAGE.read_text())
    assert len(set(matches)) == 1, (
        f"the M4 page must cite exactly one run, it cites {sorted(set(matches))}"
    )
    path = REPO_ROOT / "hermes-reports" / matches[0] / "result.json"
    assert path.is_file(), f"the cited run does not exist: {path}"
    return json.loads(path.read_text())


def test_the_dependency_is_pinned_to_an_exact_version():
    version = pinned_version()
    assert version == "4.14.0" or EXACT_VERSION_RE.match(version)
    # It is a devDependency: the shipped dashboard does not import it, only the
    # harness does, and a production install should not carry it.
    assert "axe-core" not in package().get("dependencies", {}), (
        "axe-core moved into the runtime dependencies; it is only the harness's tool"
    )


def test_the_lockfile_records_the_same_version_with_a_licence_and_a_hash():
    version = pinned_version()
    entry = lock()["packages"].get("node_modules/axe-core")
    assert entry, "package-lock.json no longer records an installed axe-core"
    assert entry["version"] == version, (
        f"the lock records axe-core {entry['version']} while package.json pins {version}"
    )
    assert entry.get("license") == LICENCE, (
        f"the lock names the licence {entry.get('license')!r}; this guard and the "
        f"acceptance page name {LICENCE!r}"
    )
    assert entry.get("integrity", "").startswith("sha512-"), (
        "the lock records no sha512 integrity for axe-core, so the install is "
        "not verifiable from the file alone"
    )
    assert entry.get("dev") is True, "axe-core is no longer a development dependency"


def test_the_file_the_suite_injects_is_the_one_the_run_hashed():
    """The strongest form of the claim: bytes, not a version string."""
    result = cited_run()
    tool = result["a11y"]["tool"]
    assert tool["package"] == "axe-core/axe.min.js", (
        f"the run injected {tool['package']!r}; this guard checks axe-core/axe.min.js"
    )
    recorded = tool["sha256"]
    assert SHA256_RE.match(recorded), f"the run recorded a non-hash: {recorded!r}"
    assert tool["version"] == pinned_version(), (
        f"the run reports axe {tool['version']} while the repository pins "
        f"{pinned_version()}"
    )
    if not AXE_MIN_JS.is_file():
        raise AssertionError(
            "node_modules/axe-core is not installed here (a cold clone), so the "
            "recorded hash could not be compared against the real file. The pin and "
            "the run's own record were still checked; install the web dependencies "
            "to check the bytes."
        )
    actual = hashlib.sha256(AXE_MIN_JS.read_bytes()).hexdigest()
    assert actual == recorded, (
        f"the installed axe.min.js hashes to {actual} while the cited run recorded "
        f"{recorded}: the run is evidence about a file that is not this one"
    )


def test_the_harness_resolves_axe_by_require_and_records_its_own_hash():
    """A vendored or copied axe would leave this source assertion unsatisfied."""
    source = HARNESS.read_text()
    assert 'createRequire(import.meta.url).resolve("axe-core/axe.min.js")' in source, (
        "the harness no longer resolves axe through the installed package"
    )
    assert "const axeSha = createHash(\"sha256\").update(axeSource).digest(\"hex\")" in source
    assert "sha256: axeSha" in source, "the harness stopped recording the hash it injected"
    assert 'AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]' in source, (
        "the tag set changed; the run's claim is WCAG 2.1 level A and AA"
    )


def test_the_acceptance_page_names_the_pin_and_the_licence():
    text = PAGE.read_text()
    assert pinned_version() in text, (
        f"the M4 page never names the pinned axe version ({pinned_version()})"
    )
    assert LICENCE in text, (
        f"the M4 page never names axe-core's licence ({LICENCE})"
    )
    assert "axe-core/axe.min.js" in text, (
        "the page never names the file the suite injects"
    )