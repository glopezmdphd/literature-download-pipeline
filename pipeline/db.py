"""Database functions for the literature pipeline."""

import os
import sqlite3
import logging
from datetime import datetime

from config import PATHS

logger = logging.getLogger(__name__)


def init_database():
    """Initialize SQLite database with required tables.

    Directory creation is guarded to avoid errors when PATHS are overridden in tests.
    """
    db_dir = os.path.dirname(PATHS['database']) if PATHS.get('database') else ''
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(PATHS['database'])
    cursor = conn.cursor()

    # Create articles table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            pmid INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT,
            journal TEXT,
            year INTEGER,
            doi TEXT,
            abstract TEXT,
            keywords TEXT,
            pdf_path TEXT,
            pdf_downloaded BOOLEAN DEFAULT FALSE,
            search_query TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            url TEXT,
            pdf_attempts INTEGER DEFAULT 0
        )
    ''')

    # Create full-text search table (external content) and maintain via triggers
    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title, abstract, keywords, authors, journal,
            content=articles, content_rowid=pmid
        )
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, abstract, keywords, authors, journal)
            VALUES (new.pmid, new.title, new.abstract, new.keywords, new.authors, new.journal);
        END;
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid) VALUES('delete', old.pmid);
        END;
    ''')
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid) VALUES('delete', old.pmid);
            INSERT INTO articles_fts(rowid, title, abstract, keywords, authors, journal)
            VALUES (new.pmid, new.title, new.abstract, new.keywords, new.authors, new.journal);
        END;
    ''')

    # Create search log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_name TEXT,
            query_text TEXT,
            results_found INTEGER,
            new_articles INTEGER,
            search_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


def article_exists(pmid, conn=None):
    """Check if article already exists in database."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(PATHS['database'])
        close_conn = True
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM articles WHERE pmid = ?', (pmid,))
    exists = cursor.fetchone() is not None
    if close_conn:
        conn.close()
    return exists


def save_article(article_data, conn=None):
    """Save article data to database."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(PATHS['database'])
        close_conn = True
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO articles 
        (pmid, title, authors, journal, year, doi, abstract, keywords, search_query, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', article_data)

    if close_conn:
        conn.commit()
        conn.close()


def update_pdf_status(pmid, pdf_path=None, downloaded=False, attempts=0, conn=None):
    """Update PDF download status for an article."""
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(PATHS['database'])
        close_conn = True
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE articles 
        SET pdf_path = ?, pdf_downloaded = ?, pdf_attempts = ?
        WHERE pmid = ?
    ''', (pdf_path, downloaded, attempts, pmid))

    if close_conn:
        conn.commit()
        conn.close()


def get_last_search_time(query_name: str):
    """Get the last search time for a query (for incremental searches)."""
    try:
        conn = sqlite3.connect(PATHS['database'])
        cur = conn.cursor()
        cur.execute('SELECT MAX(search_date) FROM search_log WHERE query_name = ?', (query_name,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            try:
                return datetime.fromisoformat(row[0])
            except ValueError:
                return None
        return None
    except (sqlite3.Error, OSError):
        return None


def search_database(search_term):
    """Search the local database for articles using FTS or LIKE fallback."""
    conn = sqlite3.connect(PATHS['database'])
    cursor = conn.cursor()
    results = []
    try:
        cursor.execute('''
            SELECT a.pmid, a.title, a.authors, a.journal, a.year, a.pdf_downloaded
            FROM articles_fts f
            JOIN articles a ON a.pmid = f.rowid
            WHERE f MATCH ?
            ORDER BY a.year DESC
        ''', (search_term,))
        results = cursor.fetchall()
    except (sqlite3.Error, ValueError):
        cursor.execute('''
            SELECT pmid, title, authors, journal, year, pdf_downloaded
            FROM articles 
            WHERE title LIKE ? OR abstract LIKE ? OR keywords LIKE ?
            ORDER BY year DESC
        ''', (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
        results = cursor.fetchall()
    conn.close()
    return results


def get_pipeline_stats():
    """Get statistics about the pipeline database."""
    conn = sqlite3.connect(PATHS['database'])
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM articles')
    total_articles = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM articles WHERE pdf_downloaded = 1')
    articles_with_pdf = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(DISTINCT search_query) FROM articles')
    unique_searches = cursor.fetchone()[0]

    cursor.execute('SELECT MIN(year), MAX(year) FROM articles WHERE year IS NOT NULL')
    year_range = cursor.fetchone()

    conn.close()

    return {
        'total_articles': total_articles,
        'articles_with_pdf': articles_with_pdf,
        'pdf_success_rate': (articles_with_pdf / total_articles * 100) if total_articles > 0 else 0,
        'unique_searches': unique_searches,
        'year_range': year_range
    }
