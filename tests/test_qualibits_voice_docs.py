"""The product page must stay honest, and everything it points at must exist.

``docs/QUALIABITS-VOICE.md`` is the front door of this fork: the first thing a
stranger or a customer reads. Growth work drifts in a specific, predictable way
— a status line ages into a sales line, a path moves, a claim outlives its
evidence. These checks pin the parts that can be pinned mechanically:

* it still says nothing is live, and the README still says so too;
* no pricing, no unqualified scale or customer claims;
* every repo path it names exists, and the evidence files it cites say what it
  says they say;
* the Hindi voice names in the page are exactly the probe's own list;
* the API route it names exists in the backend.

What these tests cannot do: judge whether the writing is good, or verify the
measurements themselves. Those stay human and log-based, and the page links the
logs for exactly that reason.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "QUALIABITS-VOICE.md"
_PROBE = _ROOT / "scripts" / "m1_hindi_probe.py"
_README = _ROOT / "README.md"

_HINDI_VOICES = ("hf_alpha", "hf_beta", "hm_omega", "hm_psi")
_REPO_PATH = re.compile(
    r"`((?:scripts|tests|docs|backend|electron|frontend|hermes-reports)/[A-Za-z0-9_.\-/]+?)`"
)
_PRICING = ("pricing", "price", "per minute", "per-minute", "per seat", "per call",
            "₹", "usd", "invoice", "discount", "free trial", "monthly plan")
_SALES = ("production-ready", "production ready", "enterprise-grade", "scales to",
          "99.9", "trusted by", "seamless", "cutting-edge", "world-class",
          "industry-leading", "best-in-class", "revolutionary")


def _prose(path: Path) -> str:
    """Doc text with fenced code blocks removed (commands may contain $)."""
    out, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def test_doc_exists_and_readme_points_at_it():
    assert _DOC.is_file(), "the product page is the entry point; it must exist"
    banner = "\n".join(_README.read_text(encoding="utf-8").splitlines()[:12])
    assert "docs/QUALIABITS-VOICE.md" in banner, "README must link the product page"
    assert "not live" in banner.lower(), "README banner must keep the status line"


def test_doc_says_nothing_is_live_and_names_what_is_not_verified():
    prose = _prose(_DOC)
    assert "M1 is not finished and nothing here is live" in prose
    assert "## What is not verified" in prose
    for missing in ("No GPU run", "No human listening check", "No hosted service",
                    "No telephony"):
        assert missing in prose, f"the page must still state: {missing}"


def test_no_pricing_and_no_sales_language():
    prose = _prose(_DOC).lower()
    for term in _PRICING:
        assert term not in prose, f"pricing or cost language on the product page: {term}"
    for term in _SALES:
        assert term not in prose, f"unsupported sales language on the product page: {term}"


def test_every_repo_path_it_names_exists():
    names = set(_REPO_PATH.findall(_DOC.read_text(encoding="utf-8")))
    assert names, "the page should cite real repo paths, not prose promises"
    missing = sorted(n for n in names if not (_ROOT / n).exists())
    assert not missing, f"the page cites paths that do not exist: {missing}"


def test_cited_test_log_actually_says_139_passed():
    log = _ROOT / "hermes-reports" / "M1-slot23-clean-tests.log"
    assert log.is_file()
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "139 passed" in text, "the '139 tests pass' claim must trace to this log"


def test_hindi_voice_names_match_the_probe():
    tree = ast.parse(_PROBE.read_text(encoding="utf-8"))
    declared = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "HINDI_VOICES" for t in node.targets
        ):
            declared = ast.literal_eval(node.value)
    assert declared is not None, "HINDI_VOICES must stay a literal in the probe"
    assert tuple(declared) == _HINDI_VOICES
    in_page = {v for v in _HINDI_VOICES if f"`{v}`" in _prose(_DOC)}
    assert in_page == set(_HINDI_VOICES), (
        "the page must name the probe's voices, or stop implying it runs them: "
        f"{sorted(set(_HINDI_VOICES) - in_page)}"
    )


def test_named_api_route_exists_in_the_backend():
    route = "/v1/audio/speech"
    assert route in _prose(_DOC)
    hits = [
        path for path in (_ROOT / "backend").rglob("*.py")
        if route in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits, f"the page names {route} but the backend does not serve it"