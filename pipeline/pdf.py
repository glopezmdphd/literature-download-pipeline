"""PDF download and acquisition functions."""

import os
import re
import time
import json
import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from Bio import Entrez  # type: ignore

from config import PATHS, ADVANCED_CONFIG, PIPELINE_CONFIG, UNPAYWALL_CONFIG, PDF_HOSTS
from .utils import backoff_sleep
from .db import update_pdf_status
from .pubmed import configure_entrez

logger = logging.getLogger(__name__)


def create_pdf_filename(title, year, pmid):
    """Create a clean filename for PDF storage."""
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


def is_pdf_host_allowed(url: str) -> bool:
    """Check if the PDF host is allowed based on allow/deny lists."""
    try:
        host = urlparse(url).netloc.lower()
        allow = PDF_HOSTS.get('allow') or []
        deny = PDF_HOSTS.get('deny') or []
        if allow:
            return any(host.endswith(a.lower()) for a in allow)
        return not any(host.endswith(d.lower()) for d in deny)
    except (ValueError, KeyError, AttributeError):
        # Fallback to allow pipeline to proceed even if URL parsing oddities
        return True


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
                            if not is_pdf_host_allowed(pdf_url):
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
                backoff_sleep(i)
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
            backoff_sleep(i)
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
