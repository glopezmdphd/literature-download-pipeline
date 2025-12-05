"""
Configuration for Medical Literature Pipeline

- Prefer environment variables for secrets.
  Supported vars:
    EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_RECIPIENT
    PUBMED_EMAIL, PUBMED_API_KEY

- Optionally use a .env file (if python-dotenv is installed).
"""

import os

try:  # Optional: load .env if available
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# ============================================================================
# EMAIL CONFIGURATION
# ============================================================================
EMAIL_CONFIG = {
    'smtp_server': 'smtp.office365.com',  # Your enterprise SMTP (usually this for O365)
    'smtp_port': 587,
    'username': os.getenv('EMAIL_USERNAME', 'george.lopez@swedish.org'),
    'password': os.getenv('EMAIL_PASSWORD', 'your_app_specific_password'),
    'recipient': os.getenv('EMAIL_RECIPIENT', 'george.lopez@swedish.org'),
}

# ============================================================================
# PUBMED/NCBI CONFIGURATION  
# ============================================================================
PUBMED_CONFIG = {
    'email': os.getenv('PUBMED_EMAIL', 'george.lopez@swedish.org'),  # Required by NCBI API
    'api_key': os.getenv('PUBMED_API_KEY', None),  # Optional - get from NCBI account settings
    'tool_name': 'CT_Neurological_Literature_Monitor'
}

# Optional Unpaywall (Open Access) configuration
UNPAYWALL_CONFIG = {
    'enabled': True,
    'email': os.getenv('UNPAYWALL_EMAIL', os.getenv('PUBMED_EMAIL', 'george.lopez@swedish.org')),
}

# ============================================================================
# FILE PATHS - UPDATE THESE FOR YOUR SYSTEM
# ============================================================================
# Base path for OneDrive (check your actual OneDrive path)
ONEDRIVE_BASE = r'C:\Users\YourUsername\OneDrive - YourHealthcareSystem\Medical_Literature'

PATHS = {
    'onedrive_base': ONEDRIVE_BASE,
    'database': f'{ONEDRIVE_BASE}\\database\\articles.db',
    'log_file': f'{ONEDRIVE_BASE}\\logs\\pipeline.log',
    'lock_file': f'{ONEDRIVE_BASE}\\logs\\pipeline.lock'
}

# ============================================================================
# SEARCH QUERIES FOR YOUR RESEARCH
# ============================================================================
# Queries are loaded from queries.yaml if present; otherwise fallback to inline defaults.

def _load_queries():
    """Load search queries from queries.yaml or return inline fallback."""
    _yaml_path = os.path.join(os.path.dirname(__file__), 'queries.yaml')
    if os.path.exists(_yaml_path):
        try:
            import yaml
            with open(_yaml_path, 'r', encoding='utf-8') as f:
                raw = yaml.safe_load(f)
            # Convert flat {name: {query: ...}} to expected format
            if isinstance(raw, dict):
                return {k: v if isinstance(v, dict) else {'query': v} for k, v in raw.items()}
        except (OSError, ValueError, ImportError) as e:
            print(f"Warning: Failed to load queries.yaml: {e}; using inline fallback.")
    # Inline fallback (kept for backward compatibility)
    return {
        'ct_neurological_prognosis': {
            'query': '(("Tomography, X-Ray Computed"[MeSH Terms]) OR ("computed tomography"[tiab]) OR (CT[tiab])) AND (("Prognosis"[MeSH Terms]) OR (prognos*[tiab]) OR (outcome*[tiab])) AND (("Brain Injuries"[MeSH Terms]) OR (neurolog*[tiab]))'
        },
        'ml_ct_brain_analysis': {
            'query': '(("Machine Learning"[MeSH Terms]) OR ("deep learning"[tiab])) AND (("Tomography, X-Ray Computed"[MeSH Terms]) OR (CT[tiab])) AND (("Brain"[MeSH Terms]) OR (brain[tiab]))'
        },
    }

SEARCH_QUERIES = _load_queries()

# ============================================================================
# ADVANCED SETTINGS
# ============================================================================
ADVANCED_CONFIG = {
    'years_back': 5,  # How many years back to search
    'max_pdf_attempts': 3,  # How many times to retry PDF downloads
    'rate_limit_delay': 1,  # Seconds between API calls
    'request_timeout': 30,  # Timeout for web requests in seconds
    'datetype': 'edat',  # 'edat' (index) or 'pdat' (publication)
}

# Pipeline behavior
PIPELINE_CONFIG = {
    'incremental': True,           # Search since last successful run per query
    'lock_timeout_hours': 6,       # Consider lock stale after N hours
    'max_retries': 3,              # Default retry attempts for network calls
    'backoff_base': 1.0,           # Base backoff seconds
    'backoff_max': 8.0,            # Max backoff seconds
    'backoff_jitter': 0.5,         # Add +/- jitter seconds
    'max_email_attachment_mb': 25, # Attachment size guard; skip if exceeded
    'attach_csv': False,           # Excel-only by default; set True to also attach CSV
}

# PDF host filtering: skip attempts on known paywalled domains unless allowed.
# - If 'allow' is non-empty, only those hosts are attempted.
# - Otherwise, 'deny' hosts are skipped.
PDF_HOSTS = {
    'allow': [],
    'deny': [
        'link.springer.com',
        'sciencedirect.com',
        'onlinelibrary.wiley.com',
        'jamanetwork.com',
        'nejm.org',
        'thelancet.com',
        'nature.com',
        'oup.com',
        'tandfonline.com',
        'cambridge.org',
        'sagepub.com',
    ],
}

# ============================================================================
# AZURE VM PATHS (if different from OneDrive sync)
# ============================================================================
# If running on Azure VM without OneDrive sync, use these paths instead:
AZURE_PATHS = {
    'base_dir': r'D:\medical_literature',  # Or wherever you want to store files
    'database': r'D:\medical_literature\database\articles.db',
    'log_file': r'D:\medical_literature\logs\pipeline.log',
    'pdf_storage': r'D:\medical_literature\pdfs'
}

# Set to True if running on Azure VM without OneDrive sync
USE_AZURE_PATHS = False

# If using Azure paths, map to PATHS that main.py expects
if USE_AZURE_PATHS:
    PATHS = {
        'onedrive_base': AZURE_PATHS['base_dir'],
        'database': AZURE_PATHS['database'],
        'log_file': AZURE_PATHS['log_file'],
        'lock_file': os.path.join(os.path.dirname(AZURE_PATHS['log_file']), 'pipeline.lock'),
    }
