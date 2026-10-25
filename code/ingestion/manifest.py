"""Ghost Data Still Exists - Evidence Manifest Resolver & Provenance Tracker.

Resolves evidence file paths safely relative to the dataset root, checks
file availability, computes integrity hashes to prevent duplicate ingestion,
and maps manifest records to typed EvidenceDocument instances.
"""

from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from code.schemas import EvidenceDocument, EvidenceManifestRecord


SUPPORTED_EXTENSIONS: Set[str] = {".log", ".txt", ".json", ".pdf", ".png", ".sql"}


def resolve_evidence_path(dataset_dir: str | Path, rel_path: str) -> Path:
    """Resolve an evidence file path safely relative to dataset_dir.

    Guards against path traversal attacks. Raises ValueError if rel_path
    attempts to escape the directory.
    """
    base = Path(dataset_dir).resolve()
    target = (base / rel_path).resolve()

    # Guard against path traversal
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"Path traversal detected: {rel_path} resolves outside {dataset_dir}")

    return target


def compute_file_hash(file_path: Path) -> str:
    """Compute the SHA-256 hash of a file for integrity and deduplication."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_manifest(
    manifest_csv_path: str | Path,
    dataset_dir: str | Path,
) -> Tuple[Dict[str, EvidenceDocument], List[Dict[str, str]]]:
    """Load and validate the evidence manifest.

    Args:
        manifest_csv_path: Path to evidence_files.csv
        dataset_dir: Directory containing evidence/ folder (typically dataset/)

    Returns:
        (documents_by_id, manifest_errors)
    """
    manifest_path = Path(manifest_csv_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found: {manifest_csv_path}")

    documents: Dict[str, EvidenceDocument] = {}
    errors: List[Dict[str, str]] = []
    seen_file_ids: Set[str] = set()

    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        expected_cols = {"file_id", "file_path", "origin", "captured_at"}
        if not expected_cols.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Manifest missing required columns: {expected_cols - set(reader.fieldnames or [])}")

        for row_idx, row in enumerate(reader, start=2):
            file_id = (row.get("file_id") or "").strip()
            rel_path = (row.get("file_path") or "").strip()
            origin = (row.get("origin") or "").strip()
            captured_at = (row.get("captured_at") or "").strip()

            if not file_id or not rel_path:
                errors.append({
                    "row": str(row_idx),
                    "file_id": file_id,
                    "error": "Missing file_id or file_path",
                })
                continue

            if file_id in seen_file_ids:
                errors.append({
                    "row": str(row_idx),
                    "file_id": file_id,
                    "error": f"Duplicate file_id encountered in manifest: {file_id}",
                })
                continue
            seen_file_ids.add(file_id)

            try:
                resolved_path = resolve_evidence_path(dataset_dir, rel_path)
            except ValueError as e:
                errors.append({
                    "row": str(row_idx),
                    "file_id": file_id,
                    "error": str(e),
                })
                continue

            if not resolved_path.exists():
                errors.append({
                    "row": str(row_idx),
                    "file_id": file_id,
                    "error": f"Evidence file does not exist: {resolved_path}",
                })
                doc = EvidenceDocument(
                    file_id=file_id,
                    file_path=rel_path,
                    origin=origin,
                    captured_at=captured_at,
                    file_type=resolved_path.suffix.lower(),
                    extraction_status="FAILED",
                    extraction_warning=f"File not found on disk: {resolved_path}",
                )
                documents[file_id] = doc
                continue

            ext = resolved_path.suffix.lower()
            file_size = resolved_path.stat().st_size
            content_hash = compute_file_hash(resolved_path)

            doc = EvidenceDocument(
                file_id=file_id,
                file_path=rel_path,
                origin=origin,
                captured_at=captured_at,
                file_type=ext,
                file_size_bytes=file_size,
                content_hash=content_hash,
                metadata={
                    "resolved_path": str(resolved_path),
                    "is_supported": ext in SUPPORTED_EXTENSIONS,
                },
            )

            if ext not in SUPPORTED_EXTENSIONS:
                doc.extraction_status = "UNSUPPORTED"
                doc.extraction_warning = f"Unsupported file extension: {ext}"

            documents[file_id] = doc

    return documents, errors
