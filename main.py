"""
Medical Literature Automated Pipeline
For CT Neurological Prognostication Research

This script automates the process of:
1. Searching PubMed for relevant articles
2. Downloading available PDFs
3. Organizing results in a searchable database
4. Storing files in OneDrive for manual review

Author: [Your Name]
Date: [Current Date]
"""

import os
import sqlite3
import requests
import time
import logging
import smtplib
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urljoin, urlparse
import argparse
import pandas as pd
from typing import Optional, Set
import atexit
import random
import re
from urllib.error import URLError
from Bio import Entrez  # type: ignore
from bs4 import BeautifulSoup

from config import (
    EMAIL_CONFIG,
    PUBMED_CONFIG,
    PATHS,
    SEARCH_QUERIES,
    ADVANCED_CONFIG,
    UNPAYWALL_CONFIG,
    PIPELINE_CONFIG,
    PDF_HOSTS
)

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION SECTION - UPDATE THESE VALUES
# ============================================================================

# Email Configuration
# EMAIL_CONFIG = {
#     'smtp_server': 'smtp.office365.com',  # Your enterprise SMTP server
#     'smtp_port': 587,
#     'username': 'your.email@enterprise.com',  # Your email
#     'password': 'your_app_password',  # Use app password for security
#     'recipient': 'your.email@enterprise.com'  # Where to send notifications
# }

# PubMed Configuration
# PUBMED_CONFIG = {
#     'email': 'your.email@enterprise.com',  # Required by NCBI
#     'api_key': 'your_ncbi_api_key',  # Optional but recommended
#     'tool_name': 'CT_Neurological_Literature_Monitor'
# }

# NOTE: Placeholder PATHS and SEARCH_QUERIES removed to avoid overriding config imports.

# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging():
    """Configure logging for the pipeline.

    Safely create the log directory only if a directory component exists. This
    allows tests to inject a simple filename (no directory) without errors at
    import time.
    """
    log_target = PATHS.get('log_file')
    if log_target:
        log_dir = os.path.dirname(log_target)
        if log_dir:  # Guard against empty string
            os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_target) if log_target else logging.StreamHandler(),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)

# ============================================================================
# PREFLIGHT VALIDATION & RUN LOCK
# ============================================================================

def validate_environment() -> bool:
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

# Removed eager directory creation at import time to allow tests to override PATHS safely.

# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def init_database():
    """Initialize SQLite database with required tables.

    Directory creation is guarded to avoid errors when PATHS are overridden in tests
    with temporary directories.
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
    
    # Always commit individual article saves to prevent data loss
    # This ensures articles are saved even if later processing fails
    conn.commit()
    
    if close_conn:
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

# ============================================================================
# PUBMED SEARCH FUNCTIONS
# ============================================================================

def configure_entrez():
    """Configure Entrez settings for PubMed access."""
    Entrez.email = PUBMED_CONFIG['email']
    if PUBMED_CONFIG['api_key']:
        Entrez.api_key = PUBMED_CONFIG['api_key']
    Entrez.tool = PUBMED_CONFIG['tool_name']

def _backoff_sleep(i: int):
    base = PIPELINE_CONFIG.get('backoff_base', 1.0)
    cap = PIPELINE_CONFIG.get('backoff_max', 8.0)
    jitter = PIPELINE_CONFIG.get('backoff_jitter', 0.5)
    delay = min(cap, base * (2 ** i))
    jitter_val = random.uniform(-jitter, jitter)
    time.sleep(max(0.0, delay + jitter_val))

def get_last_search_time(query_name: str):
    try:
        conn = sqlite3.connect(PATHS['database'])
        cur = conn.cursor()
        cur.execute('SELECT MAX(search_date) FROM search_log WHERE query_name = ?', (query_name,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            # SQLite timestamp by default in string
            try:
                return datetime.fromisoformat(row[0])
            except ValueError:
                return None
        return None
    except (sqlite3.Error, OSError):
        return None

def search_pubmed(query_name, query, years_back=5, incremental=True):
    """Search PubMed for articles matching query criteria."""
    configure_entrez()
    years_back = ADVANCED_CONFIG.get('years_back', years_back)

    # Determine date window
    end_date = datetime.now().date()
    mindate = None
    if incremental and PIPELINE_CONFIG.get('incremental', True):
        last_time = get_last_search_time(query_name)
        if last_time:
            mindate = last_time.date()
    if mindate is None:
        mindate = (datetime.now() - timedelta(days=years_back * 365)).date()

    # Build query and parameters
    full_query = f"{query} AND English[lang]"
    datetype = ADVANCED_CONFIG.get('datetype', 'edat')
    logger.info("Searching PubMed (%s %s..%s) with query: %s", datetype, mindate, end_date, full_query)

    try:
        tries = PIPELINE_CONFIG.get('max_retries', 3)

        # 1) esearch to get total count
        count = 0
        for i in range(tries):
            try:
                h = Entrez.esearch(
                    db="pubmed",
                    term=full_query,
                    retmax=0,
                    mindate=mindate.strftime('%Y/%m/%d'),
                    maxdate=end_date.strftime('%Y/%m/%d'),
                    datetype=datetype,
                    usehistory='n'
                )
                res = Entrez.read(h)
                h.close()
                count = int(res.get('Count', 0))
                break
            except (OSError, URLError, ValueError) as ee:
                if i == tries - 1:
                    raise
                logger.warning("esearch(count) retry %d/%d: %s", i+1, tries, ee)
                _backoff_sleep(i)
        time.sleep(ADVANCED_CONFIG.get('rate_limit_delay', 1))

        if count == 0:
            logger.info("Found 0 articles")
            return []

        # 2) Retrieve all PMIDs in pages
        batch = 10000
        pmids = []
        retstart = 0
        while retstart < count:
            for i in range(tries):
                try:
                    h = Entrez.esearch(
                        db="pubmed",
                        term=full_query,
                        retmax=min(batch, count - retstart),
                        retstart=retstart,
                        mindate=mindate.strftime('%Y/%m/%d'),
                        maxdate=end_date.strftime('%Y/%m/%d'),
                        datetype=datetype,
                    )
                    page = Entrez.read(h)
                    h.close()
                    pmids.extend(page.get('IdList', []))
                    break
                except (OSError, URLError, ValueError) as ee:
                    if i == tries - 1:
                        raise
                    logger.warning("esearch(page) retry %d/%d: %s", i+1, tries, ee)
                    _backoff_sleep(i)
            retstart += batch
            time.sleep(ADVANCED_CONFIG.get('rate_limit_delay', 1))

        logger.info("Found %d articles (count=%d)", len(pmids), count)

        # 3) efetch in chunks
        all_articles = []
        chunk_size = 200
        for i0 in range(0, len(pmids), chunk_size):
            chunk = pmids[i0:i0 + chunk_size]
            for i in range(tries):
                try:
                    fh = Entrez.efetch(db="pubmed", id=','.join(chunk), rettype="medline", retmode="xml")
                    arts = Entrez.read(fh)
                    fh.close()
                    all_articles.extend(arts.get('PubmedArticle', []))
                    break
                except (OSError, URLError, ValueError) as ee:
                    if i == tries - 1:
                        raise
                    logger.warning("efetch retry %d/%d (offset %d): %s", i+1, tries, i0, ee)
                    _backoff_sleep(i)
            time.sleep(ADVANCED_CONFIG.get('rate_limit_delay', 1))

        return parse_pubmed_articles(all_articles)

    except (OSError, URLError, ValueError) as e:
        logger.error("Error searching PubMed: %s", e)
        return []

def parse_pubmed_articles(articles):
    """Parse PubMed XML response into structured data."""
    parsed_articles = []
    
    for article in articles:
        try:
            medline_citation = article['MedlineCitation']
            article_data = medline_citation['Article']
            
            # Extract basic information
            pmid = str(medline_citation['PMID'])
            title = article_data.get('ArticleTitle', 'No title')
            
            # Extract authors
            authors = []
            if 'AuthorList' in article_data:
                for author in article_data['AuthorList']:
                    if 'LastName' in author and 'ForeName' in author:
                        authors.append(f"{author['LastName']}, {author['ForeName']}")
            authors_str = '; '.join(authors) if authors else 'No authors listed'
            
            # Extract journal and year
            journal = article_data.get('Journal', {}).get('Title', 'Unknown journal')
            pub_date = article_data.get('Journal', {}).get('JournalIssue', {}).get('PubDate', {})
            year = pub_date.get('Year', 'Unknown')
            if year == 'Unknown' and 'MedlineDate' in pub_date:
                # Try to extract year from MedlineDate
                medline_date = pub_date['MedlineDate']
                year_match = re.search(r'\d{4}', medline_date)
                if year_match:
                    year = year_match.group()
            
            # Extract abstract
            abstract = ''
            if 'Abstract' in article_data and 'AbstractText' in article_data['Abstract']:
                abstract_parts = article_data['Abstract']['AbstractText']
                if isinstance(abstract_parts, list):
                    abstract = ' '.join([str(part) for part in abstract_parts])
                else:
                    abstract = str(abstract_parts)
            
            # Extract DOI from multiple locations
            doi = ''
            if 'ELocationID' in article_data:
                for elocation in article_data['ELocationID']:
                    if getattr(elocation, 'attributes', {}).get('EIdType') == 'doi':
                        doi = str(elocation)
                        break
            if not doi and 'PubmedData' in article:
                pid_list = article['PubmedData'].get('ArticleIdList', [])
                for pid in pid_list:
                    if getattr(pid, 'attributes', {}).get('IdType') == 'doi':
                        doi = str(pid)
                        break
            
            # Extract keywords/MeSH terms
            keywords = []
            if 'MeshHeadingList' in medline_citation:
                for mesh in medline_citation['MeshHeadingList']:
                    if 'DescriptorName' in mesh:
                        keywords.append(str(mesh['DescriptorName']))
            keywords_str = '; '.join(keywords) if keywords else ''
            
            # Create PubMed URL
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            
            parsed_articles.append({
                'pmid': pmid,
                'title': title,
                'authors': authors_str,
                'journal': journal,
                'year': int(year) if year.isdigit() else None,
                'doi': doi,
                'abstract': abstract,
                'keywords': keywords_str,
                'url': url
            })
            
        except (KeyError, TypeError, ValueError) as e:
            logger.error("Error parsing article: %s", e)
            continue
    
    return parsed_articles

# ============================================================================
# PDF DOWNLOAD FUNCTIONS
# ============================================================================

def create_pdf_filename(title, year, pmid):
    """Create a clean filename for PDF storage."""
    # Clean title for filename
    clean_title = re.sub(r'[^\w\s-]', '', title)
    clean_title = re.sub(r'[-\s]+', '_', clean_title)
    clean_title = clean_title[:100]  # Limit length
    
    year_str = str(year) if year else 'unknown_year'
    filename = f"{clean_title}_{year_str}_{pmid}.pdf"
    
    return filename

def create_topic_folder(search_query_name):
    """Create folder structure for organizing PDFs by topic."""
    topic_folder = os.path.join(PATHS['onedrive_base'], 'PDFs', search_query_name)
    os.makedirs(topic_folder, exist_ok=True)
    return topic_folder

def download_pdf_from_doi(session, doi, save_path, max_attempts=3):
    """Attempt to download PDF using DOI."""
    if not doi:
        return False
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    # Try different DOI resolution methods
    doi_urls = [
        f"https://doi.org/{doi}",
        f"https://dx.doi.org/{doi}",
        f"https://www.doi.org/{doi}"
    ]
    
    timeout = ADVANCED_CONFIG.get('request_timeout', 30)
    for _attempt in range(max_attempts):
        for doi_url in doi_urls:
            try:
                logger.info("Attempting PDF download from DOI: %s", doi_url)
                response = session.get(doi_url, headers=headers, timeout=timeout, allow_redirects=True)
                
                # Check if response contains PDF
                ctype = response.headers.get('content-type', '').lower()
                if ctype.startswith('application/pdf') or response.content[:4] == b'%PDF':
                    with open(save_path, 'wb') as f:
                        f.write(response.content)
                    logger.info("Successfully downloaded PDF from DOI: %s", doi)
                    return True
                
                # If not direct PDF, try to find PDF link in the page
                if 'text/html' in ctype:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    pdf_links = soup.find_all('a', href=re.compile(r'\.pdf($|\?)|pdf'))
                    
                    for link in pdf_links[:3]:  # Try first 3 PDF links
                        pdf_url = link.get('href')
                        if pdf_url:
                            if not pdf_url.startswith('http'):
                                pdf_url = urljoin(doi_url, pdf_url)
                            # Host filter
                            if not _is_pdf_host_allowed(pdf_url):
                                logger.info("Skipping blocked PDF host: %s", urlparse(pdf_url).netloc)
                                continue
                            
                            pdf_response = session.get(pdf_url, headers=headers, timeout=timeout, allow_redirects=True)
                            pct = pdf_response.headers.get('content-type', '').lower()
                            if pct.startswith('application/pdf') or pdf_response.content[:4] == b'%PDF':
                                with open(save_path, 'wb') as f:
                                    f.write(pdf_response.content)
                                logger.info("Successfully downloaded PDF from link: %s", pdf_url)
                                return True
                
                time.sleep(max(ADVANCED_CONFIG.get('rate_limit_delay', 1), 1))  # Rate limiting
                
            except (requests.RequestException, OSError, ValueError) as e:
                logger.warning("Failed to download PDF from %s: %s", doi_url, e)
                time.sleep(ADVANCED_CONFIG.get('rate_limit_delay', 1))
    
    return False

def download_pdf_from_pmc(session, pmid, save_path):
    """Attempt to download PDF from PubMed Central."""
    try:
        configure_entrez()
        # Link PubMed PMID to PMC ID
        elink = Entrez.elink(dbfrom='pubmed', db='pmc', id=str(pmid))
        linkset = Entrez.read(elink)
        elink.close()
        ids = []
        try:
            links = linkset[0]['LinkSetDb'][0]['Link']
            ids = [lnk['Id'] for lnk in links]
        except (KeyError, IndexError, TypeError):
            ids = []
        if not ids:
            return False
        pmc_id = ids[0]
        if not str(pmc_id).startswith('PMC'):
            pmc_id = f"PMC{pmc_id}"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0 Safari/537.36'
        }
        timeout = ADVANCED_CONFIG.get('request_timeout', 30)
        candidates = [
            f"https://pmc.ncbi.nlm.nih.gov/{pmc_id}/pdf",
            f"https://pmc.ncbi.nlm.nih.gov/{pmc_id}/pdf/{pmc_id}.pdf",
        ]
        for url in candidates:
            try:
                r = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                ctype = r.headers.get('content-type', '').lower()
                if ctype.startswith('application/pdf') or r.content[:4] == b'%PDF':
                    with open(save_path, 'wb') as f:
                        f.write(r.content)
                    logger.info("Successfully downloaded PMC PDF for PMID %s", pmid)
                    return True
            except (requests.RequestException, OSError, ValueError) as e:
                logger.debug("PMC attempt failed for %s: %s", url, e)
        return False
        
    except (requests.RequestException, OSError, ValueError) as e:
        logger.warning("Failed to download PDF from PMC for PMID %s: %s", pmid, e)
        return False

def try_unpaywall(session, doi):
    """Try to resolve an OA PDF via Unpaywall API. Returns bytes if found, else None."""
    if not UNPAYWALL_CONFIG.get('enabled'):
        return None
    email = UNPAYWALL_CONFIG.get('email')
    if not email or not doi:
        return None
    url = f"https://api.unpaywall.org/v2/{doi}"
    params = {'email': email}
    timeout = ADVANCED_CONFIG.get('request_timeout', 30)
    tries = PIPELINE_CONFIG.get('max_retries', 3)
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                _backoff_sleep(i)
                continue
            data = r.json()
            loc = data.get('best_oa_location') or {}
            pdf_url = loc.get('url_for_pdf') or loc.get('url')
            if pdf_url:
                pr = session.get(pdf_url, timeout=timeout, allow_redirects=True)
                ctype = pr.headers.get('content-type', '').lower()
                if ctype.startswith('application/pdf') or pr.content[:4] == b'%PDF':
                    return pr.content
            return None
        except (requests.RequestException, ValueError):
            _backoff_sleep(i)
    return None

def attempt_pdf_download(article, search_query_name, session, conn=None):
    """Attempt to download PDF for an article using multiple methods."""
    pmid = article['pmid']
    
    # Create filename and path
    filename = create_pdf_filename(article['title'], article['year'], pmid)
    topic_folder = create_topic_folder(search_query_name)
    save_path = os.path.join(topic_folder, filename)
    
    # Skip if already downloaded
    if os.path.exists(save_path):
        logger.info("PDF already exists for PMID %s", pmid)
        update_pdf_status(pmid, save_path, True, 0, conn=conn)
        return True
    
    logger.info("Attempting to download PDF for PMID %s: %s", pmid, article['title'])
    
    attempts = 0
    success = False
    
    # Method 1: Try Unpaywall (if enabled)
    if article['doi'] and not success:
        attempts += 1
        try:
            content = try_unpaywall(session, article['doi'])
            if content:
                with open(save_path, 'wb') as f:
                    f.write(content)
                logger.info("Successfully downloaded PDF via Unpaywall for DOI %s", article['doi'])
                success = True
        except (requests.RequestException, OSError) as e:
            # Network / IO issues are expected occasionally; keep at debug level
            logger.debug("Unpaywall network attempt failed: %s", e)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            # Parsing / unexpected schema problems are more actionable
            logger.warning("Unpaywall parsing failed for DOI %s: %s", article['doi'], e)

    # Method 2: Try DOI landing page
    if article['doi'] and not success:
        attempts += 1
        success = download_pdf_from_doi(session, article['doi'], save_path)
    
    # Method 3: Try PMC (if others failed)
    if not success:
        attempts += 1
        success = download_pdf_from_pmc(session, pmid, save_path)
    
    # Update database with results
    if success:
        update_pdf_status(pmid, save_path, True, attempts, conn=conn)
        logger.info("Successfully downloaded PDF for PMID %s", pmid)
    else:
        update_pdf_status(pmid, None, False, attempts, conn=conn)
        logger.warning("Failed to download PDF for PMID %s after %d attempts", pmid, attempts)
    
    return success

def _is_pdf_host_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        allow = PDF_HOSTS.get('allow') or []
        deny = PDF_HOSTS.get('deny') or []
        if allow:
            return any(host.endswith(a.lower()) for a in allow)
        return not any(host.endswith(d.lower()) for d in deny)
    except (ValueError, KeyError, AttributeError):  # Fallback to allow pipeline to proceed even if URL parsing oddities
        return True

# ============================================================================
# EMAIL NOTIFICATION FUNCTIONS
# ============================================================================

def send_email_notification(subject, message, attachments=None):
    """Send email notification about pipeline results."""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['username']
        msg['To'] = EMAIL_CONFIG['recipient']
        msg['Subject'] = subject
        
        msg.attach(MIMEText(message, 'plain'))

        # Attach files if provided
        if attachments:
            from email.mime.base import MIMEBase
            from email import encoders
            import mimetypes
            for path in attachments:
                try:
                    if not path or not os.path.exists(path):
                        continue
                    ctype, _ = mimetypes.guess_type(path)
                    maintype, subtype = (ctype.split('/', 1) if ctype else ('application', 'octet-stream'))
                    with open(path, 'rb') as f:
                        part = MIMEBase(maintype, subtype)
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(path)}"')
                    msg.attach(part)
                except (OSError, IOError, ValueError) as e:
                    logger.warning("Failed to attach file %s: %s", path, e)
        
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'], timeout=15)
        server.starttls()
        server.login(EMAIL_CONFIG['username'], EMAIL_CONFIG['password'])
        server.send_message(msg)
        server.quit()
        
        logger.info("Email notification sent successfully")
        
    except (smtplib.SMTPException, OSError) as e:
        logger.error("Failed to send email notification: %s", e)

# ============================================================================
# MAIN PIPELINE FUNCTIONS
# ============================================================================

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
    
    logger.info("Query '%s' completed: %d found, %d new articles, %d new PDFs", query_name, len(articles), new_articles, new_pdfs)
    
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

    # Generate exports
    xlsx_path = None
    csv_path = None
    try:
        xlsx_path = export_articles_excel(output_path=None, search_term=None, split_by_topic=True)
    except (OSError, ValueError) as e:
        logger.warning("Excel export failed: %s", e)
    try:
        csv_path = export_articles_csv(output_path=None, search_term=None, split_by_topic=False)
        if isinstance(csv_path, list):
            # Should not happen here; ensure single CSV
            csv_path = csv_path[0] if csv_path else None
    except (OSError, ValueError) as e:
        logger.warning("CSV export failed: %s", e)

    # Decide attachments based on size guard
    max_mb = PIPELINE_CONFIG.get('max_email_attachment_mb', 25)
    attach_csv = PIPELINE_CONFIG.get('attach_csv', True)
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
        except (OSError, IOError):  # Size calculation issues should not abort attachment collection
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

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def search_database(search_term):
    """Search the local database for articles."""
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
    except (sqlite3.Error, ValueError):  # FTS table missing or malformed -> fallback to LIKE search
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

# ============================================================================
# EXPORT FUNCTIONS
# ============================================================================

def _sanitize_filename(text: str) -> str:
    clean = re.sub(r'[^\w\s-]', '', text)
    clean = re.sub(r'[\s-]+', '_', clean).strip('_')
    return clean[:80] if clean else 'export'

def _get_export_rows(search_term: Optional[str] = None):
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

def export_articles_csv(output_path: Optional[str] = None, search_term: Optional[str] = None, split_by_topic: bool = False):
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

def export_articles_excel(output_path: Optional[str] = None, search_term: Optional[str] = None, split_by_topic: bool = False) -> str:
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
            used = set()
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

def acquire_run_lock(force: bool = False) -> bool:
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
    lock_path = PATHS.get('lock_file')
    try:
        if lock_path and os.path.exists(lock_path):
            os.remove(lock_path)
            logger.info('Released run lock')
    except (OSError, IOError) as e:
        logger.error("Failed to release lock: %s", e)

atexit.register(release_run_lock)

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
    parser.add_argument('--split-by-topic', action='store_true', help='Split export by topic (multi-sheet Excel or per-topic CSV files)')
    parser.add_argument('--no-email', action='store_true', help='Skip email notification after pipeline run')
    parser.add_argument('--validate', action='store_true', help='Validate configuration and environment only')
    parser.add_argument('--force-run', action='store_true', help='Ignore existing run lock (use with caution)')
    parser.add_argument('--no-incremental', action='store_true', help='Disable incremental search window for this run')
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
                    "Pipeline failed with error: %s\n\nCheck log file: %s" % (e, PATHS['log_file'])
                )
            except (smtplib.SMTPException, OSError):
                pass

