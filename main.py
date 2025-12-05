"""
Medical Literature Automated Pipeline
For CT Neurological Prognostication Research

This script automates the process of:
1. Searching PubMed for relevant articles
2. Downloading available PDFs
3. Organizing results in a searchable database
4. Storing files in OneDrive for manual review
"""

import os
import sqlite3
import requests
import time
import logging
import smtplib
import argparse
import atexit
from datetime import datetime, timedelta

from config import (
    PATHS,
    SEARCH_QUERIES,
    ADVANCED_CONFIG,
    PIPELINE_CONFIG,
)

from pipeline import (
    setup_logging,
    validate_environment,
    acquire_run_lock,
    release_run_lock,
    init_database,
    article_exists,
    save_article,
    search_pubmed,
    attempt_pdf_download,
    export_articles_csv,
    export_articles_excel,
    send_email_notification,
    get_pipeline_stats,
)

logger = logging.getLogger(__name__)

# Register cleanup handler
atexit.register(release_run_lock)


def process_search_query(query_name, query_config):
    """Process a single search query and download PDFs."""
    logger.info("Processing search query: %s", query_name)

    # Search PubMed
    articles = search_pubmed(
        query_name,
        query_config['query'],
        incremental=PIPELINE_CONFIG.get('incremental', True)
    )

    new_articles = 0
    new_pdfs = 0
    conn = sqlite3.connect(PATHS['database'])
    session = requests.Session()

    for article in articles:
        pmid = article['pmid']

        # Skip if article already exists
        if article_exists(pmid, conn=conn):
            logger.info("Article %s already in database, skipping", pmid)
            continue

        # Save article to database
        article_data = (
            pmid, article['title'], article['authors'], article['journal'],
            article['year'], article['doi'], article['abstract'],
            article['keywords'], query_name, article['url']
        )
        save_article(article_data, conn=conn)
        new_articles += 1

        # Attempt PDF download
        if attempt_pdf_download(article, query_name, session, conn=conn):
            new_pdfs += 1

        # Rate limiting
        time.sleep(ADVANCED_CONFIG.get('rate_limit_delay', 1))

    # Log search results
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO search_log (query_name, query_text, results_found, new_articles)
        VALUES (?, ?, ?, ?)
    ''', (query_name, query_config['query'], len(articles), new_articles))
    conn.commit()
    conn.close()

    logger.info("Query '%s' completed: %d found, %d new articles, %d new PDFs",
                query_name, len(articles), new_articles, new_pdfs)

    return len(articles), new_articles, new_pdfs


def run_full_pipeline():
    """Run the complete literature search and download pipeline."""
    logger.info("Starting medical literature pipeline")
    start_time = datetime.now()

    # Initialize database
    init_database()

    # Process all search queries
    total_found = 0
    total_new_articles = 0
    total_new_pdfs = 0

    for query_name, query_config in SEARCH_QUERIES.items():
        try:
            found, new_articles, new_pdfs = process_search_query(query_name, query_config)
            total_found += found
            total_new_articles += new_articles
            total_new_pdfs += new_pdfs

        except (sqlite3.Error, requests.RequestException, OSError, ValueError) as e:
            logger.error("Error processing query '%s': %s", query_name, e)

    # Calculate runtime
    end_time = datetime.now()
    runtime = end_time - start_time

    # Create summary report
    summary = f"""
Medical Literature Pipeline - Weekly Report
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Search Results:
- Total articles found: {total_found}
- New articles added: {total_new_articles}
- New PDFs downloaded: {total_new_pdfs}
- Runtime: {runtime}

Search Queries Processed:
"""

    for query_name in SEARCH_QUERIES.keys():
        summary += f"- {query_name}\n"

    summary += f"""
Database Location: {PATHS['database']}
PDF Storage: {PATHS['onedrive_base']}/PDFs/
Log File: {PATHS['log_file']}

Next scheduled run: {(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')}
"""

    logger.info("Pipeline completed successfully")
    logger.info(summary)

    # Generate exports (Excel primary; CSV only if attach_csv enabled)
    xlsx_path = None
    csv_path = None
    attach_csv = PIPELINE_CONFIG.get('attach_csv', False)
    try:
        xlsx_path = export_articles_excel(output_path=None, search_term=None, split_by_topic=True)
    except (OSError, ValueError) as e:
        logger.warning("Excel export failed: %s", e)
    if attach_csv:
        try:
            csv_path = export_articles_csv(output_path=None, search_term=None, split_by_topic=False)
            if isinstance(csv_path, list):
                csv_path = csv_path[0] if csv_path else None
        except (OSError, ValueError) as e:
            logger.warning("CSV export failed: %s", e)

    # Decide attachments based on size guard
    max_mb = PIPELINE_CONFIG.get('max_email_attachment_mb', 25)
    attachments = []
    total_size_mb = 0.0

    def _add(path):
        nonlocal total_size_mb
        try:
            if path and os.path.exists(path):
                sz = os.path.getsize(path) / (1024 * 1024)
                if total_size_mb + sz <= max_mb:
                    attachments.append(path)
                    total_size_mb += sz
                    return True
        except (OSError, IOError):
            pass
        return False

    # Try Excel first, then CSV if allowed and within limit
    excel_ok = _add(xlsx_path)
    csv_ok = False
    if attach_csv and excel_ok:
        csv_ok = _add(csv_path)
        if not csv_ok and csv_path:
            logger.info("CSV attachment omitted due to size limit; sending Excel-only")

    if not attachments:
        summary += "\nAttachments skipped due to size; see exports in OneDrive exports folder.\n"

    # Send email notification with attachments (if any)
    send_email_notification(
        f"Medical Literature Pipeline - {total_new_articles} New Articles",
        summary,
        attachments=attachments
    )


# =========================================================================
# MAIN EXECUTION
# =========================================================================

if __name__ == "__main__":
    # Setup logging
    logger = setup_logging()

    parser = argparse.ArgumentParser(description="Medical Literature Pipeline")
    parser.add_argument('--export', choices=['csv', 'xlsx'], help='Export articles to CSV or Excel')
    parser.add_argument('--output', help='Output file path (.csv or .xlsx)')
    parser.add_argument('--search', help='Filter export by search term (FTS/LIKE)')
    parser.add_argument('--only-export', action='store_true', help='Skip running the pipeline before export')
    parser.add_argument('--split-by-topic', action='store_true', help='Split export by topic')
    parser.add_argument('--no-email', action='store_true', help='Skip email notification after pipeline run')
    parser.add_argument('--validate', action='store_true', help='Validate configuration and environment only')
    parser.add_argument('--force-run', action='store_true', help='Ignore existing run lock')
    parser.add_argument('--no-incremental', action='store_true', help='Disable incremental search window')
    args = parser.parse_args()

    try:
        if args.validate:
            valid = validate_environment()
            logger.info("Validation %s", 'PASSED' if valid else 'FAILED')
            raise SystemExit(0 if valid else 2)

        if args.export:
            if not args.only_export:
                if not acquire_run_lock(force=args.force_run):
                    raise SystemExit(3)
                prev_incremental = PIPELINE_CONFIG.get('incremental', True)
                if args.no_incremental:
                    PIPELINE_CONFIG['incremental'] = False
                try:
                    valid = validate_environment()
                    if not valid:
                        logger.error('Validation failed; aborting run')
                        raise SystemExit(2)
                    run_full_pipeline()
                finally:
                    PIPELINE_CONFIG['incremental'] = prev_incremental
                    release_run_lock()
            if args.export == 'csv':
                export_articles_csv(args.output, args.search, split_by_topic=args.split_by_topic)
            else:
                export_articles_excel(args.output, args.search, split_by_topic=args.split_by_topic)
        else:
            # Default behavior: run the pipeline and email summary
            if not acquire_run_lock(force=args.force_run):
                raise SystemExit(3)
            prev_incremental = PIPELINE_CONFIG.get('incremental', True)
            if args.no_incremental:
                PIPELINE_CONFIG['incremental'] = False
            try:
                valid = validate_environment()
                if not valid:
                    logger.error('Validation failed; aborting run')
                    raise SystemExit(2)
                run_full_pipeline()
                stats = get_pipeline_stats()
                logger.info("Pipeline Statistics: %s", stats)
            finally:
                PIPELINE_CONFIG['incremental'] = prev_incremental
                release_run_lock()
    except (sqlite3.Error, requests.RequestException, OSError, ValueError) as e:
        logger.error("Pipeline failed with error: %s", e)
        if not args.export and not args.no_email:
            try:
                send_email_notification(
                    "Medical Literature Pipeline - ERROR",
                    f"Pipeline failed with error: {e}\n\nCheck log file: {PATHS['log_file']}"
                )
            except (smtplib.SMTPException, OSError):
                pass
