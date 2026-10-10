"""Sign resources/brand/brand_info.json with the local brand private key.

The private key must stay outside the release package.
Default private key path: F:/work/rsa/brand_private_key.pem
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


BRAND_NAMESPACE = "ai-cartoon-factory-brand"


def canonical_payload(data: dict[str, Any]) -> bytes:
    clean = {str(key): value for key, value in data.items() if key != "signature"}
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def sign_brand(info_path: Path, private_key_path: Path) -> dict[str, Any]:
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise RuntimeError("ssh-keygen was not found on PATH")
    if not private_key_path.exists():
        raise FileNotFoundError(f"private key not found: {private_key_path}")
    data = json.loads(info_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("brand_info.json must contain a JSON object")

    with tempfile.TemporaryDirectory(prefix="aicf_brand_sign_") as temp:
        temp_dir = Path(temp)
        payload_path = temp_dir / "brand_payload.json"
        payload_path.write_bytes(canonical_payload(data))
        subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "sign",
                "-f",
                str(private_key_path),
                "-n",
                BRAND_NAMESPACE,
                str(payload_path),
            ],
            check=True,
        )
        signature_path = payload_path.with_suffix(payload_path.suffix + ".sig")
        data["signature"] = base64.b64encode(signature_path.read_bytes()).decode("ascii")

    info_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", default="resources/brand/brand_info.json")
    parser.add_argument("--private-key", default="F:/work/rsa/brand_private_key.pem")
    args = parser.parse_args()
    info_path = Path(args.info).resolve()
    private_key_path = Path(args.private_key).resolve()
    sign_brand(info_path, private_key_path)
    print(f"signed: {info_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
