"""Encrypt an exported marimo WebAssembly site behind a password.

Usage (after ``marimo export html-wasm app.py -o site --mode run``)::

    SITE_PASSWORD=... python scripts/protect_site.py site

The exported ``index.html`` is encrypted with AES-256-GCM. The key comes from
PBKDF2-SHA256 (600,000 iterations) over the password and a random salt. The
original page is replaced by a gate page that asks for the password. The gate
decrypts the app in the browser with Web Crypto and runs it in a same-origin
iframe. Without the password the page holds only ciphertext.

Assets (marimo runtime, fpb wheel) stay public because they contain no personal
data. Only the notebook page and its embedded plan are encrypted.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

ITERATIONS = 600_000
GATE_TITLE = "Finance Plan Builder"


def _derive_key(password: str, salt: bytes) -> bytes:
    import hashlib

    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=32)


def encrypt_html(html: str, password: str) -> dict[str, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(12)
    ct = AESGCM(_derive_key(password, salt)).encrypt(iv, html.encode("utf-8"), None)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def decrypt_html(payload: dict[str, str], password: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        key = _derive_key(password, base64.b64decode(payload["salt"]))
        pt = AESGCM(key).decrypt(base64.b64decode(payload["iv"]), base64.b64decode(payload["ct"]), None)
    except InvalidTag as exc:
        raise ValueError("wrong password") from exc
    return pt.decode("utf-8")


def _gate_page(payload: dict[str, str]) -> str:
    import json

    data = json.dumps(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{GATE_TITLE}</title>
<style>
  html, body {{ margin: 0; height: 100%; font: 15px/1.4 system-ui, sans-serif; }}
  #gate {{ display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; }}
  #gate form {{ display: flex; gap: 8px; }}
  #gate input {{ font: inherit; padding: 6px 10px; min-width: 240px; }}
  #gate button {{ font: inherit; padding: 6px 14px; cursor: pointer; }}
  #err {{ color: #dc2626; min-height: 1.4em; }}
  #app {{ position: fixed; inset: 0; width: 100%; height: 100%; border: 0; display: none; }}
</style>
</head>
<body>
<div id="gate">
  <h2>{GATE_TITLE}</h2>
  <form id="form">
    <input id="pw" type="password" autocomplete="current-password" placeholder="Password" required>
    <button type="submit">Unlock</button>
  </form>
  <div id="err"></div>
</div>
<iframe id="app" title="{GATE_TITLE}"></iframe>
<script>
const PAYLOAD = {data};
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function decrypt(password) {{
  const enc = new TextEncoder();
  const base = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    {{ name: "PBKDF2", salt: b64(PAYLOAD.salt), iterations: {ITERATIONS}, hash: "SHA-256" }},
    base, {{ name: "AES-GCM", length: 256 }}, false, ["decrypt"]);
  const pt = await crypto.subtle.decrypt({{ name: "AES-GCM", iv: b64(PAYLOAD.iv) }}, key, b64(PAYLOAD.ct));
  return new TextDecoder().decode(pt);
}}

document.getElementById("form").addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  const err = document.getElementById("err");
  const button = ev.submitter || ev.target.querySelector("button");
  button.disabled = true;
  err.textContent = "Unlocking...";
  try {{
    const html = await decrypt(document.getElementById("pw").value);
    document.getElementById("gate").style.display = "none";
    const frame = document.getElementById("app");
    frame.srcdoc = html;
    frame.style.display = "block";
  }} catch (e) {{
    err.textContent = "Wrong password.";
    button.disabled = false;
  }}
}});
</script>
</body>
</html>
"""


def protect(site_dir: Path, password: str) -> None:
    index = site_dir / "index.html"
    html = index.read_text(encoding="utf-8")
    # The wheel link is written relative to the notebook page ("../public/..."). Once
    # the page is inside an iframe served from the site root, it must be "./public/...".
    html = html.replace("../public/", "./public/")
    payload = encrypt_html(html, password)
    index.write_text(_gate_page(payload), encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: SITE_PASSWORD=... python scripts/protect_site.py <site_dir>", file=sys.stderr)
        return 2
    password = os.environ.get("SITE_PASSWORD", "")
    if not password:
        print("SITE_PASSWORD is not set", file=sys.stderr)
        return 2
    protect(Path(argv[1]), password)
    print(f"protected {argv[1]}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
