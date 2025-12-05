"""Shared utilities: logging, validation, locking, backoff."""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
import atexit
import random
import time

from config import (
    EMAIL_CONFIG,
    PUBMED_CONFIG,
    PATHS,
    UNPAYWALL_CONFIG,
    PIPELINE_CONFIG,
)

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure logging for the pipeline.

    Uses RotatingFileHandler to prevent unbounded log growth (5 MB max, 3 backups).
    Safely creates log directory only if a directory component exists.
    """
    log_target = PATHS.get('log_file')
    if log_target:
        log_dir = os.path.dirname(log_target)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Console handler (always)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(log_format))
    root_logger.addHandler(console)

    # Rotating file handler (if log path configured)
    if log_target:
        file_handler = RotatingFileHandler(
            log_target,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        root_logger.addHandler(file_handler)

    return logging.getLogger(__name__)


def validate_environment() -> bool:
    """Validate configuration and environment setup."""
    ok = True
    # Email
    if not EMAIL_CONFIG.get('username') or '@' not in EMAIL_CONFIG.get('username', ''):
        logger.warning('EMAIL_CONFIG.username looks unset or invalid')
    if not EMAIL_CONFIG.get('recipient') or '@' not in EMAIL_CONFIG.get('recipient', ''):
        logger.warning('EMAIL_CONFIG.recipient looks unset or invalid')
    # PubMed
    if not PUBMED_CONFIG.get('email') or '@' not in PUBMED_CONFIG.get('email', ''):
        logger.error('PUBMED_CONFIG.email is required by NCBI and appears unset')
        ok = False
    # Paths
    try:
        db_dir = os.path.dirname(PATHS['database']) if PATHS.get('database') else ''
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    except (OSError, IOError) as e:
        logger.error('Path setup failed: %s', e)
        ok = False
    # Unpaywall
    if UNPAYWALL_CONFIG.get('enabled') and not UNPAYWALL_CONFIG.get('email'):
        logger.warning('UNPAYWALL enabled but email not set; set UNPAYWALL_EMAIL or PUBMED_EMAIL')
    return ok


def backoff_sleep(i: int):
    """Exponential backoff with jitter for retry loops."""
    base = PIPELINE_CONFIG.get('backoff_base', 1.0)
    cap = PIPELINE_CONFIG.get('backoff_max', 8.0)
    jitter = PIPELINE_CONFIG.get('backoff_jitter', 0.5)
    delay = min(cap, base * (2 ** i))
    jitter_val = random.uniform(-jitter, jitter)
    time.sleep(max(0.0, delay + jitter_val))


def acquire_run_lock(force: bool = False) -> bool:
    """Acquire a file-based run lock to prevent concurrent runs."""
    lock_path = PATHS.get('lock_file')
    if not lock_path:
        return True
    lock_dir = os.path.dirname(lock_path)
    try:
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
    except OSError as e:
        logger.error("Failed to create lock directory: %s", e)
        return False

    if os.path.exists(lock_path) and not force:
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(lock_path))
            age = datetime.now() - mtime
            if age > timedelta(hours=PIPELINE_CONFIG.get('lock_timeout_hours', 6)):
                logger.warning('Stale lock detected; removing')
                os.remove(lock_path)
            else:
                logger.error('Another run appears active (lock at %s). Use --force-run to override.', lock_path)
                return False
        except (OSError, ValueError) as e:
            logger.error("Error checking lock file: %s", e)
            return False

    try:
        with open(lock_path, 'x', encoding='utf-8') as lock_file:
            lock_file.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")
        logger.info("Acquired run lock: %s", lock_path)
        return True
    except FileExistsError:
        if force:
            try:
                os.remove(lock_path)
                return acquire_run_lock(force=False)
            except (OSError, IOError) as e:
                logger.error("Failed to remove existing lock: %s", e)
                return False
        logger.error('Lock already exists; aborting run')
        return False
    except (OSError, IOError) as e:
        logger.error("Failed to acquire lock: %s", e)
        return False


def release_run_lock():
    """Release the file-based run lock."""
    lock_path = PATHS.get('lock_file')
    try:
        if lock_path and os.path.exists(lock_path):
            os.remove(lock_path)
            logger.info('Released run lock')
    except (OSError, IOError) as e:
        logger.error("Failed to release lock: %s", e)


# Auto-release lock on exit
atexit.register(release_run_lock)
