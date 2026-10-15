import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("protect", ROOT / "scripts" / "protect_site.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_round_trip_with_correct_password():
    m = _mod()
    payload = m.encrypt_html("<html>secret plan</html>", "BRATTLE")
    assert m.decrypt_html(payload, "BRATTLE") == "<html>secret plan</html>"


def test_wrong_password_rejected():
    m = _mod()
    payload = m.encrypt_html("<html>secret plan</html>", "BRATTLE")
    with pytest.raises(ValueError, match="wrong password"):
        m.decrypt_html(payload, "brattle")


def test_ciphertext_contains_no_plaintext():
    m = _mod()
    payload = m.encrypt_html("Mortgage 380000 Brittany", "BRATTLE")
    blob = "".join(payload.values())
    assert "Mortgage" not in blob and "380000" not in blob


def test_each_encryption_uses_fresh_salt_and_iv():
    m = _mod()
    a = m.encrypt_html("same", "BRATTLE")
    b = m.encrypt_html("same", "BRATTLE")
    assert a["salt"] != b["salt"] and a["iv"] != b["iv"] and a["ct"] != b["ct"]


def test_protect_replaces_index_and_keeps_assets(tmp_path):
    m = _mod()
    (tmp_path / "index.html").write_text('<html><script src="../public/wheels/x.whl"></script></html>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("js")
    m.protect(tmp_path, "BRATTLE")
    gate = (tmp_path / "index.html").read_text()
    assert "Mortgage" not in gate and "wheels" not in gate
    assert (tmp_path / "assets" / "app.js").exists()
    assert (tmp_path / ".nojekyll").exists()
    payload = {k: v for k, v in (
        ("salt", gate.split('"salt": "')[1].split('"')[0]),
        ("iv", gate.split('"iv": "')[1].split('"')[0]),
        ("ct", gate.split('"ct": "')[1].split('"')[0]),
    )}
    assert "./public/wheels/x.whl" in m.decrypt_html(payload, "BRATTLE")
