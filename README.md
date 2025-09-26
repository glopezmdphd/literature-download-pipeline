# Medical Literature Pipeline

🔬 **Enterprise-ready automated PubMed literature monitoring with local-first design**

Automates PubMed searches, persists results to SQLite with full‑text search, and attempts to fetch open‑access PDFs via DOI, PubMed Central (PMC), and Unpaywall. Optimized for Windows enterprise environments with robust local storage and optional email notifications.

Perfect for clinical research curation (CT neurological prognosis, neuroimaging + AI, biomarkers) with incremental updates and CSV/Excel export.

**Repository:** https://github.com/glopezmdphd/literature-download-pipeline

## ✨ Key Features

- 🏥 **Enterprise Windows compatible** - Local-first design avoids UNC path issues
- 📊 **SQLite database** with full-text search and comprehensive metadata
- 📄 **Smart PDF acquisition** - Unpaywall, PMC, DOI resolution with retry logic  
- 📧 **Email notifications** (optional) - File-only mode for simplified operation
- 🔄 **Incremental updates** - Only searches for new articles since last run
- 📈 **Export capabilities** - CSV/Excel with multi-sheet topic breakdown
- 🔒 **Secure configuration** - Environment variables, .env support, .gitignore protected

## 🚀 Quick Start (Windows Enterprise)

### 1. Local Installation (Recommended for Enterprise)
```powershell
# Clone to local drive (avoids network/UNC issues)
git clone https://github.com/glopezmdphd/literature-download-pipeline.git
cd literature-download-pipeline

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies  
pip install -r requirements.txt
```

### 2. Configuration
The pipeline uses **local-first paths** by default:
```
C:\Users\YourUsername\Medical_Literature_Pipeline\
├── database\articles.db    # SQLite database
├── logs\pipeline.log       # Execution logs  
└── PDFs\                   # Downloaded PDFs by topic
    ├── ct_neurological_prognosis\
    ├── cardiac_biomarkers\
    └── sepsis_prediction\
```

### 3. Environment Setup (Optional)
Create `.env` file for email notifications:
```bash
# PubMed Configuration (Required)
PUBMED_EMAIL=your.email@organization.org

# Email Notifications (Optional - can run in file-only mode)  
EMAIL_USERNAME=your.email@organization.org
EMAIL_PASSWORD=your_app_specific_password  
EMAIL_RECIPIENT=your.email@organization.org
```

**Security Note:** Never commit passwords to Git. Use app-specific passwords or environment variables.

### 4. Run Pipeline
```powershell
# Run with all configured topics
python main.py

# File-only mode (no email notifications)
python main.py --no-email

# Export results to Excel
python main.py --export xlsx --split-by-topic
```

- Email (for notifications)
  - `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `EMAIL_RECIPIENT`
- PubMed (NCBI E-utilities)
  - `PUBMED_EMAIL` (required by NCBI)
  - `PUBMED_API_KEY` (optional but recommended for higher rate limits)
- Unpaywall (optional OA PDF lookup)
  - `UNPAYWALL_EMAIL` (uses `PUBMED_EMAIL` if not set)
- Paths
  - Update `ONEDRIVE_BASE` to point to your OneDrive base folder.
    - Windows example: `C:\\Users\\<you>\\OneDrive - <Org>\\Medical_Literature`
    - macOS example: `/Users/<you>/Library/CloudStorage/OneDrive-<Org>/Medical_Literature`
    - Tip: On macOS, check available OneDrive roots with `ls ~/Library/CloudStorage` and locate the one matching your organization.
  - Or set `USE_AZURE_PATHS = True` and edit `AZURE_PATHS` for Azure
- Advanced behavior
  - `ADVANCED_CONFIG['years_back']` – PubMed date window
  - `ADVANCED_CONFIG['rate_limit_delay']` – seconds between requests
  - `ADVANCED_CONFIG['request_timeout']` – HTTP timeout
  - `ADVANCED_CONFIG['datetype']` – date field for window (`edat` index or `pdat` publication)
  - `PIPELINE_CONFIG['incremental']` – search since last run per query
  - `PIPELINE_CONFIG['max_email_attachment_mb']` – email attachment size guard (default 25 MB)
  - `PIPELINE_CONFIG['attach_csv']` – try attaching CSV alongside Excel when within size guard

- PDF host filters (to avoid retrying known paywalled domains)
  - `PDF_HOSTS['allow']`: if non-empty, only these domains are attempted for PDF links
  - `PDF_HOSTS['deny']`: domains to skip (default includes common paywalled publishers)

Search queries are defined in `config.py` as a dict of named topics:
```python
SEARCH_QUERIES = {
    'ct_neurological_prognosis': {
        'query': '("head CT" OR "cranial CT" OR "brain CT") AND (prognosis OR outcome) AND (neurological OR neurologic)',
        'max_results': 50,
    },
    'neurological_imaging_ai': {
        'query': '("neurological imaging" OR "brain imaging") AND ("artificial intelligence" OR "machine learning") AND (prognosis OR prediction)',
        'max_results': 35,
    },
}
```

### 3) Run

```bash
python main.py
```

Export data (CSV or Excel):
```bash
# Run pipeline, then export all articles
python main.py --export csv
python main.py --export xlsx

# Export only, filtered by a search term
python main.py --export csv --only-export --search "glioma"

# Choose a custom output path
python main.py --export xlsx --output "/path/to/articles.xlsx"

# Split by topic
# - Excel: multi-sheet workbook (one sheet per topic)
# - CSV: multiple files (one per topic) in an exports/by_topic_<date> folder
python main.py --export xlsx --split-by-topic
python main.py --export csv --only-export --split-by-topic
```

Validation and locking:
```bash
# Validate environment (config, paths) without running
python main.py --validate

# Prevent overlapping runs with a lock (default). Override if needed:
python main.py --force-run
```

Incremental searches and date windows:
```bash
# Default: incremental (since last run) using datetype from config (edat by default)

# Disable incremental for a full refresh window (uses years_back)
python main.py --no-incremental
```

### 4) Schedule

- Windows Task Scheduler: run `python` with `main.py` weekly.
- Azure Automation: upload as a runbook and schedule weekly.
- macOS launchd (testing daily at 19:00 local time):
  1. Edit `macos/com.medlit.pipeline.plist` and replace `/ABSOLUTE/PATH/TO/literature-download-pipeline` with your repo path; optionally point to your venv Python.
  2. Copy to `~/Library/LaunchAgents/com.medlit.pipeline.plist`.
  3. Load and start:
     - `launchctl load ~/Library/LaunchAgents/com.medlit.pipeline.plist`
     - `launchctl start com.medlit.pipeline`
  4. Unload when done testing:
     - `launchctl unload ~/Library/LaunchAgents/com.medlit.pipeline.plist`
  Notes: Mac must be awake at run time. Use `caffeinate` or schedule when it’s typically on. The pipeline also has a run lock to avoid overlap.

Using a virtual environment (recommended):
```bash
# From repository root
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Update the plist ProgramArguments line to:
#   cd /ABSOLUTE/PATH/TO/literature-download-pipeline && /ABSOLUTE/PATH/TO/literature-download-pipeline/.venv/bin/python main.py
```

## Continuous Integration

This repo includes a minimal GitHub Actions workflow that installs dependencies and runs unit tests on pushes and pull requests to `main`.

## Notes on PDFs

- Many articles are paywalled; pipeline focuses on metadata first.
- DOI resolution scrapes for direct PDF links when available.
- PMC downloads use common patterns when a PMC ID is linked to the PMID.

## Troubleshooting

- Imports: ensure `pip install -r requirements.txt` ran successfully (Python 3.8+).
- Paths: verify directories exist and you have write permission.
- Email: use app passwords for O365 if required; firewall may block SMTP.
- API Rate Limiting: set `PUBMED_API_KEY` for higher limits; tune `ADVANCED_CONFIG['rate_limit_delay']`.

## What’s Inside

- `main.py` – pipeline logic (search, parse, DB, PDF, email)
- `config.py` – configuration (email, PubMed, paths, queries, advanced)
- `requirements.txt` – dependencies
- `.env.example` – example environment variables (copy to `.env` and edit)

## Version Control (Git + GitHub)

Initialize a local git repo and push to your GitHub remote:

1) Create a new empty repo on GitHub (note the HTTPS or SSH URL)

2) From the repository root:
```bash
git init
git add .
git commit -m "Initial pipeline setup"
git branch -M main
git remote add origin <your_github_repo_url>
git push -u origin main
```

Use HTTPS (with a Personal Access Token) or SSH (with keys) per your org policy. The `.gitignore` avoids committing virtualenvs, local env files, temp logs, and outputs.

## Topics (GitHub)

Consider adding the following topics to your repository to improve discoverability:

- pubmed
- medical-literature
- biomedical-research
- sqlite
- full-text-search
- pdf
- unpaywall
- automation
- neuroimaging
- computed-tomography
- machine-learning

You can add them on the repository Settings → General → Topics.

## License

This project is licensed under the MIT License – see the `LICENSE` file for details.

## 🔧 Recent Improvements (v2.1)

### ✅ **Database Transaction Fix** 
- **Issue:** Articles were being lost due to uncommitted database transactions
- **Fix:** Individual article commits ensure data persistence even if PDF downloads fail
- **Result:** Reliable article metadata storage independent of PDF success

### 📧 **Simplified Email Configuration**
- **File-only mode:** Pipeline works perfectly without email notifications
- **Enterprise-friendly:** No complex app password setup required
- **Optional notifications:** Easy to enable later with environment variables

### 🏢 **Enterprise Windows Optimization**
- **Local-first storage:** Eliminates UNC path issues on corporate networks
- **Virtual environment:** Isolated dependencies prevent conflicts
- **Robust error handling:** Continues processing even with network restrictions

### 📊 **Enhanced PDF Success Rates**
- **Unpaywall integration:** Finds open access versions of paywalled articles
- **Multiple sources:** DOI, PMC, Unpaywall fallback chain
- **Realistic expectations:** 20-30% success rate is normal for medical literature

### 🔍 **Improved Monitoring** 
- **Status checking:** `python quick_status.py` for real-time progress
- **Database verification:** Comprehensive integrity checks
- **Better logging:** Detailed execution tracking
