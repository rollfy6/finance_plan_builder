import importlib.util
from pathlib import Path

import fpb._embedded as emb

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location("embed", ROOT / "scripts" / "embed_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_embedded_bundle_matches_source_files():
    # Fails when a source file changed without re-running scripts/embed_assets.py.
    assert (ROOT / "fpb/_embedded.py").read_text(encoding="utf-8") == _generator().render()


def test_bundle_contains_all_assets():
    assert emb.TIMELINE_ESM.startswith("// Plan timeline")
    assert ".fpb-timeline" in emb.TIMELINE_CSS
    assert set(emb.TAX_TABLES) == {"us_federal_2025.yaml", "us_federal_2026.yaml"}
    assert "Brittany and Mark" in emb.EXAMPLE_PLAN
