"""
Medical Literature Pipeline Package

Modular components for PubMed search, PDF download, export, and notification.
"""

# Import order matters due to circular dependencies
# utils first (no internal deps), then db, then pubmed (depends on db), then pdf (depends on db, pubmed)

from .utils import (
    setup_logging,
    validate_environment,
    acquire_run_lock,
    release_run_lock,
    backoff_sleep,
)
from .db import (
    init_database,
    article_exists,
    save_article,
    update_pdf_status,
    search_database,
    get_pipeline_stats,
    get_last_search_time,
)
from .pubmed import (
    configure_entrez,
    search_pubmed,
    parse_pubmed_articles,
)
from .pdf import (
    create_pdf_filename,
    create_topic_folder,
    download_pdf_from_doi,
    download_pdf_from_pmc,
    try_unpaywall,
    attempt_pdf_download,
    is_pdf_host_allowed,
)
from .export import (
    export_articles_csv,
    export_articles_excel,
)
from .email_notify import send_email_notification

__all__ = [
    # db
    'init_database',
    'article_exists',
    'save_article',
    'update_pdf_status',
    'search_database',
    'get_pipeline_stats',
    'get_last_search_time',
    # pubmed
    'configure_entrez',
    'search_pubmed',
    'parse_pubmed_articles',
    # pdf
    'create_pdf_filename',
    'create_topic_folder',
    'download_pdf_from_doi',
    'download_pdf_from_pmc',
    'try_unpaywall',
    'attempt_pdf_download',
    'is_pdf_host_allowed',
    # export
    'export_articles_csv',
    'export_articles_excel',
    # email
    'send_email_notification',
    # utils
    'setup_logging',
    'validate_environment',
    'acquire_run_lock',
    'release_run_lock',
    'backoff_sleep',
]
