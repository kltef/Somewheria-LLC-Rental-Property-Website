"""Daily backup of the SQLite DB to S3.

Compresses the SQLite file with gzip, writes the artifact to a local staging
directory, and (when AWS credentials are present in the environment) uploads
it to ``s3://$BACKUP_S3_BUCKET/...``.

Environment variables:

- ``AWS_ACCESS_KEY_ID``      AWS credentials. If unset, upload is skipped and
- ``AWS_SECRET_ACCESS_KEY``  the local artifact is left in the staging dir.
- ``BACKUP_S3_BUCKET``       Target bucket. Required for upload.
- ``BACKUP_S3_PREFIX``       Optional key prefix (default ``somewheria/``).
- ``BACKUP_RETENTION_DAYS``  Optional. Local artifacts older than this are
                             pruned after a successful run (default 7).

Usage:

    python scripts/backup_database.py /path/to/somewheria.sqlite3
    python scripts/backup_database.py            # uses AppConfig.sqlite_file

Intended invocation: cron, daily. See docs/RUNBOOK.md.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _default_db_path() -> Path:
    from somewheria_app.config import AppConfig

    return AppConfig().sqlite_file


def _consistent_snapshot(src: Path, dest: Path) -> None:
    """Write a consistent snapshot of the SQLite DB at ``src`` to ``dest``.

    Uses SQLite's online backup API rather than a raw byte copy. With WAL
    mode (which ``Database._connect`` enables), the on-disk file alone does
    not include uncommitted-but-WAL-resident pages, and a raw copy taken
    while the writer is mid-checkpoint can capture a torn state. The backup
    API takes care of both by reading through SQLite, so the destination is
    a self-contained, internally consistent database file regardless of
    whether the writer is active.
    """
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def compress(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"{src.stem}-{stamp}.sqlite3.gz"
    # Snapshot via the SQLite backup API into a sibling temp file inside the
    # staging dir, gzip it, then unlink the intermediate. Doing the snapshot
    # first means the read pressure on the live DB is bounded by the backup
    # call itself rather than dragged out across the gzip stream.
    fd, snapshot_name = tempfile.mkstemp(
        prefix=src.stem + ".",
        suffix=".sqlite3.snapshot",
        dir=str(dest_dir),
    )
    os.close(fd)
    snapshot_path = Path(snapshot_name)
    try:
        _consistent_snapshot(src, snapshot_path)
        with snapshot_path.open("rb") as src_f, gzip.open(dest, "wb", compresslevel=6) as dst_f:
            shutil.copyfileobj(src_f, dst_f)
    finally:
        try:
            snapshot_path.unlink()
        except OSError:
            pass
    return dest


def upload_to_s3(path: Path, bucket: str, prefix: str) -> str:
    # Imported lazily so the script runs (and dry-runs) without boto3 installed.
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "boto3 is required for S3 upload. Install it (pip install boto3) "
            "or run without AWS_* env vars to skip upload."
        ) from exc

    client = boto3.client("s3")
    key = f"{prefix.rstrip('/')}/{path.name}" if prefix else path.name
    client.upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


def prune_local(dest_dir: Path, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    for entry in dest_dir.glob("*.sqlite3.gz"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                pruned += 1
        except OSError:
            pass
    return pruned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        default=None,
        help="Path to the SQLite DB. Defaults to AppConfig().sqlite_file.",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=Path(os.getenv("BACKUP_STAGING_DIR", "backups")),
        help="Local directory to write the compressed artifact. Default: ./backups",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or _default_db_path()
    if not db_path.exists():
        print(f"ERROR: SQLite DB not found at {db_path}", file=sys.stderr)
        return 2

    artifact = compress(db_path, args.staging_dir)
    size_kb = artifact.stat().st_size / 1024
    print(f"Wrote {artifact} ({size_kb:.1f} KB)")

    bucket = os.getenv("BACKUP_S3_BUCKET", "").strip()
    have_creds = bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))
    if bucket and have_creds:
        prefix = os.getenv("BACKUP_S3_PREFIX", "somewheria/")
        try:
            uri = upload_to_s3(artifact, bucket, prefix)
            print(f"Uploaded to {uri}")
        except Exception as exc:
            print(f"ERROR: S3 upload failed: {exc}", file=sys.stderr)
            return 1
    else:
        reason = "no BACKUP_S3_BUCKET" if not bucket else "AWS credentials not set"
        print(f"Skipping S3 upload ({reason})")

    retention = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))
    pruned = prune_local(args.staging_dir, retention)
    if pruned:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention)
        print(f"Pruned {pruned} local artifact(s) older than {cutoff_dt.replace(tzinfo=None).isoformat()}Z")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
