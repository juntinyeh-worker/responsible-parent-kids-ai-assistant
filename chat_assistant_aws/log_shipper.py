"""Ship local JSONL log files to S3 and clean up shipped files.

Usage:
    python log_shipper.py                    # Uses env vars for config
    python log_shipper.py --once             # Ship once and exit
    python log_shipper.py --interval 300     # Ship every 5 minutes

Environment variables (set via ECS task definition or .env):
    S3_LOG_BUCKET   — Target S3 bucket name (required)
    S3_LOG_PREFIX   — S3 key prefix (default: "conversation-logs")
    LOCAL_LOG_DIR   — Local log directory (default: "./logs")
    DEPLOY_ENV      — Must be "aws" for S3 shipping to work
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def get_s3_client():
    """Lazy import boto3 to avoid dependency in local mode."""
    import boto3

    return boto3.client("s3")


def ship_logs(log_dir: str, bucket: str, prefix: str, delete_after: bool = True) -> int:
    """Upload completed JSONL log files to S3.

    Only ships files that are NOT the current active file (i.e., rotated files).
    The active file (highest numbered) is skipped since it's still being written to.

    Returns the number of files shipped.
    """
    log_path = Path(log_dir)
    if not log_path.exists():
        logger.info(f"Log directory {log_dir} does not exist, nothing to ship.")
        return 0

    files = sorted(log_path.glob("conversation_log_*.jsonl"))
    if len(files) <= 1:
        # Only the active file exists (or none) — nothing to ship yet
        logger.info("No completed log files to ship (active file is still being written).")
        return 0

    # Ship all files except the last one (which is the active/current file)
    s3 = get_s3_client()
    shipped = 0
    files_to_ship = files[:-1]

    for f in files_to_ship:
        timestamp = datetime.utcnow().strftime("%Y/%m/%d")
        s3_key = f"{prefix}/{timestamp}/{f.name}"

        try:
            logger.info(f"Uploading {f.name} ({f.stat().st_size} bytes) → s3://{bucket}/{s3_key}")
            s3.upload_file(str(f), bucket, s3_key)
            shipped += 1

            if delete_after:
                f.unlink()
                logger.info(f"Deleted local file: {f.name}")
        except Exception as e:
            logger.error(f"Failed to ship {f.name}: {e}")

    return shipped


def ship_active_file(log_dir: str, bucket: str, prefix: str) -> int:
    """Force-ship the current active log file (e.g., on shutdown).

    This is useful for ECS task shutdown to flush remaining logs.
    """
    log_path = Path(log_dir)
    files = sorted(log_path.glob("conversation_log_*.jsonl"))
    if not files:
        return 0

    s3 = get_s3_client()
    active = files[-1]
    if active.stat().st_size == 0:
        return 0

    timestamp = datetime.utcnow().strftime("%Y/%m/%d")
    s3_key = f"{prefix}/{timestamp}/{active.name}"

    try:
        logger.info(f"Shipping active file {active.name} ({active.stat().st_size} bytes) → s3://{bucket}/{s3_key}")
        s3.upload_file(str(active), bucket, s3_key)
        active.unlink()
        logger.info(f"Deleted local file: {active.name}")
        return 1
    except Exception as e:
        logger.error(f"Failed to ship active file {active.name}: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Ship conversation logs to S3")
    parser.add_argument("--once", action="store_true", help="Ship once and exit")
    parser.add_argument("--interval", type=int, default=60, help="Shipping interval in seconds (default: 60)")
    parser.add_argument("--flush", action="store_true", help="Also ship the active file (for shutdown)")
    args = parser.parse_args()

    bucket = os.getenv("S3_LOG_BUCKET")
    prefix = os.getenv("S3_LOG_PREFIX", "conversation-logs")
    log_dir = os.getenv("LOCAL_LOG_DIR", "./logs")

    if not bucket:
        logger.error("S3_LOG_BUCKET environment variable is required.")
        sys.exit(1)

    logger.info(f"Log shipper started: {log_dir} → s3://{bucket}/{prefix}/")

    if args.once or args.flush:
        shipped = ship_logs(log_dir, bucket, prefix)
        if args.flush:
            shipped += ship_active_file(log_dir, bucket, prefix)
        logger.info(f"Shipped {shipped} file(s). Done.")
        return

    # Continuous mode — ship every N seconds
    try:
        while True:
            shipped = ship_logs(log_dir, bucket, prefix)
            if shipped:
                logger.info(f"Shipped {shipped} file(s).")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Shutting down — flushing remaining logs...")
        ship_logs(log_dir, bucket, prefix, delete_after=True)
        ship_active_file(log_dir, bucket, prefix)
        logger.info("Done.")


if __name__ == "__main__":
    main()
