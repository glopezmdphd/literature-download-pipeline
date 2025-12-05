"""Export functions for articles data."""

import os
import re
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Set, List, Union

import pandas as pd

from config import PATHS

logger = logging.getLogger(__name__)


def _sanitize_filename(text: str) -> str:
    """Sanitize text for use in filenames."""
    clean = re.sub(r'[^\w\s-]', '', text)
    clean = re.sub(r'[\s-]+', '_', clean).strip('_')
    return clean[:80] if clean else 'export'


def _get_export_rows(search_term: Optional[str] = None) -> List[dict]:
    """Get article rows from database, optionally filtered by search term."""
    conn = sqlite3.connect(PATHS['database'])
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = []
    try:
        if search_term:
            try:
                cur.execute('''
                    SELECT a.*
                    FROM articles_fts f
                    JOIN articles a ON a.pmid = f.rowid
                    WHERE f MATCH ?
                    ORDER BY a.year DESC
                ''', (search_term,))
            except (sqlite3.Error, ValueError):  # FTS unavailable -> degrade gracefully
                cur.execute('''
                    SELECT * FROM articles
                    WHERE title LIKE ? OR abstract LIKE ? OR keywords LIKE ?
                    ORDER BY year DESC
                ''', (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
        else:
            cur.execute('SELECT * FROM articles ORDER BY year DESC')
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def export_articles_csv(
    output_path: Optional[str] = None,
    search_term: Optional[str] = None,
    split_by_topic: bool = False
) -> Union[str, List[str]]:
    """Export articles to CSV file(s)."""
    rows = _get_export_rows(search_term)
    df = pd.DataFrame(rows)
    exports_dir = os.path.join(PATHS['onedrive_base'], 'exports')
    os.makedirs(exports_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d')

    if not split_by_topic:
        if not output_path:
            base = f"articles_{_sanitize_filename(search_term) if search_term else 'all'}_{stamp}.csv"
            output_path = os.path.join(exports_dir, base)
        df.to_csv(output_path, index=False, encoding='utf-8')
        logger.info("Exported %d rows to CSV: %s", len(df), output_path)
        return output_path
    else:
        # Split by topic: write multiple CSV files, one per search_query
        if 'search_query' not in df.columns or df.empty:
            # Nothing to split, write a single file
            if not output_path:
                base = f"articles_{_sanitize_filename(search_term) if search_term else 'all'}_{stamp}.csv"
                output_path = os.path.join(exports_dir, base)
            df.to_csv(output_path, index=False, encoding='utf-8')
            logger.info("Exported %d rows to CSV: %s", len(df), output_path)
            return [output_path]

        topic_dir = os.path.join(exports_dir, f"by_topic_{stamp}")
        os.makedirs(topic_dir, exist_ok=True)
        outputs = []
        for topic, g in df.groupby('search_query'):
            fname = f"articles_{_sanitize_filename(topic) or 'topic'}_{stamp}.csv"
            path = os.path.join(topic_dir, fname)
            g.to_csv(path, index=False, encoding='utf-8')
            outputs.append(path)
        logger.info("Exported %d topic CSV files to %s", len(outputs), topic_dir)
        return outputs


def export_articles_excel(
    output_path: Optional[str] = None,
    search_term: Optional[str] = None,
    split_by_topic: bool = False
) -> str:
    """Export articles to Excel file with optional topic-based sheets."""
    rows = _get_export_rows(search_term)
    df = pd.DataFrame(rows)
    exports_dir = os.path.join(PATHS['onedrive_base'], 'exports')
    os.makedirs(exports_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d')

    if not output_path:
        base = (
            f"articles_by_topic_{_sanitize_filename(search_term) if search_term else 'all'}_{stamp}.xlsx"
            if split_by_topic
            else f"articles_{_sanitize_filename(search_term) if search_term else 'all'}_{stamp}.xlsx"
        )
        output_path = os.path.join(exports_dir, base)

    def _sheet_name(raw: str, used: Set[str]) -> str:
        # Excel sheet name max 31 chars, disallow some chars
        name = re.sub(r'[\]\[\*\?/\\:]', ' ', raw).strip()
        name = name if name else 'Sheet'
        name = name[:31]
        original = name
        i = 2
        while name in used:
            suffix = f"_{i}"
            name = (original[: 31 - len(suffix)] + suffix) if len(original) + len(suffix) > 31 else original + suffix
            i += 1
        used.add(name)
        return name

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        if split_by_topic and 'search_query' in df.columns and not df.empty:
            used: Set[str] = set()
            for topic, g in df.groupby('search_query'):
                sname = _sheet_name(str(topic) if topic else 'Topic', used)
                g.to_excel(writer, index=False, sheet_name=sname)
                ws = writer.sheets[sname]
                try:
                    ws.auto_filter.ref = ws.dimensions
                    ws.freeze_panes = 'A2'
                except (AttributeError, ValueError):  # Non-critical formatting issues
                    pass
        else:
            df.to_excel(writer, index=False, sheet_name='Articles')
            ws = writer.sheets['Articles']
            try:
                ws.auto_filter.ref = ws.dimensions
                ws.freeze_panes = 'A2'
            except (AttributeError, ValueError):  # Non-critical formatting issues
                pass
    logger.info("Exported %s%d rows to Excel: %s", 'split-by-topic ' if split_by_topic else '', len(df), output_path)
    return output_path
