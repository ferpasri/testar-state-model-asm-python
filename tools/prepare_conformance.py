"""Prepare pinned shared cases from their public specification release."""

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    compatibility = json.loads((ROOT / "compatibility.json").read_text(encoding="utf-8"))
    tag = compatibility["release"]
    url = "https://codeload.github.com/ferpasri/testar-state-model-asm-specs/zip/refs/tags/" + tag
    with urlopen(Request(url, headers={"User-Agent": "testar-asm-conformance"}), timeout=60) as response:
        content = response.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != compatibility["archive_sha256"]:
        raise ValueError("The pinned conformance archive has changed or exceeds its size limit.")
    destination = ROOT / ".cache/specs"
    hashes = {}
    with ZipFile(io.BytesIO(content)) as archive:
        total = 0
        for entry in archive.infolist():
            parts = PurePosixPath(entry.filename).parts
            if entry.is_dir() or len(parts) < 2 or parts[1] != "conformance":
                continue
            if any(part in (".", "..") or "\\" in part for part in parts):
                raise ValueError("Unsafe archive path.")
            total += entry.file_size
            if total > 64 * 1024 * 1024:
                raise ValueError("Expanded cases exceed their size limit.")
            path = destination.joinpath(*parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(entry)
            path.write_bytes(data)
            hashes["/".join(parts[1:])] = hashlib.sha256(data).hexdigest()
    (destination / "files.json").write_text(json.dumps({"files": hashes}, indent=4), encoding="utf-8")
    manifest = json.loads((destination / "conformance/manifest.json").read_text(encoding="utf-8"))
    for key in ("case_format_version", "asm_contract", "evidence_schema", "rule_language",
                "abstraction_behavior", "asms", "controller"):
        if manifest[key] != compatibility[key]:
            raise ValueError("Unsupported conformance contract: " + key)
    print("Prepared", tag, len(manifest["suites"]["selection"]["cases"]), "shared cases.")


if __name__ == "__main__":
    prepare()
