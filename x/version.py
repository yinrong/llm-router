"""Versioning + tarball lookup helpers."""

from __future__ import annotations

import hashlib
import os

from x import VERSION as X_VERSION


def latest_version(role: str) -> str:
    return X_VERSION


def tarball_info(role: str, releases_dir: str) -> dict:
    """Return {version, sha256, url_path, file_path} for the latest tarball of a role.

    If the tarball is missing (dev/CI), returns metadata with empty sha and a flag.
    """
    if role not in ("b", "c", "a"):
        raise ValueError("role must be b/c/a")
    version = latest_version(role)
    fname = f"{role}-{version}.tgz"
    fpath = os.path.join(releases_dir, fname)
    sha = ""
    exists = os.path.exists(fpath)
    if exists:
        h = hashlib.sha256()
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        sha = h.hexdigest()
    return {
        "version": version,
        "sha256": sha,
        "url_path": f"/api/download/{role}/{version}",
        "file_path": fpath,
        "exists": exists,
    }
