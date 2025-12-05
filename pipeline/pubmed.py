"""PubMed search and article parsing functions."""

import re
import time
import logging
from datetime import datetime, timedelta
from urllib.error import URLError

from Bio import Entrez  # type: ignore

from config import PUBMED_CONFIG, PATHS, ADVANCED_CONFIG, PIPELINE_CONFIG
from .utils import backoff_sleep
from .db import get_last_search_time

logger = logging.getLogger(__name__)


def configure_entrez():
    """Configure Entrez settings for PubMed access."""
    Entrez.email = PUBMED_CONFIG['email']
    if PUBMED_CONFIG['api_key']:
        Entrez.api_key = PUBMED_CONFIG['api_key']
    Entrez.tool = PUBMED_CONFIG['tool_name']


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
                backoff_sleep(i)
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
                    backoff_sleep(i)
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
                    backoff_sleep(i)
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
