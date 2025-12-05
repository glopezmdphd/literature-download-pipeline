"""Smoke tests for PDF and export functionality."""

import os
import shutil
import sqlite3
import tempfile
import unittest

from config import PATHS, PDF_HOSTS
from pipeline import (
    init_database,
    save_article,
    is_pdf_host_allowed,
    export_articles_excel,
    export_articles_csv,
)


class TestPDFHostFiltering(unittest.TestCase):
    """Test PDF host allow/deny list filtering."""

    def setUp(self):
        # Save original config
        self.original_allow = PDF_HOSTS.get('allow', [])
        self.original_deny = PDF_HOSTS.get('deny', [])

    def tearDown(self):
        # Restore original config
        PDF_HOSTS['allow'] = self.original_allow
        PDF_HOSTS['deny'] = self.original_deny

    def test_allow_list_permits_matching_hosts(self):
        """When allow list is set, only matching hosts are permitted."""
        PDF_HOSTS['allow'] = ['ncbi.nlm.nih.gov', 'pmc.ncbi.nlm.nih.gov']
        PDF_HOSTS['deny'] = []

        self.assertTrue(is_pdf_host_allowed('https://pmc.ncbi.nlm.nih.gov/PMC123/pdf'))
        self.assertTrue(is_pdf_host_allowed('https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/pdf'))
        self.assertFalse(is_pdf_host_allowed('https://sci-hub.se/10.1000/xyz'))
        self.assertFalse(is_pdf_host_allowed('https://example.com/paper.pdf'))

    def test_deny_list_blocks_matching_hosts(self):
        """When only deny list is set, matching hosts are blocked."""
        PDF_HOSTS['allow'] = []
        PDF_HOSTS['deny'] = ['sci-hub.se', 'libgen.rs']

        self.assertFalse(is_pdf_host_allowed('https://sci-hub.se/10.1000/xyz'))
        self.assertFalse(is_pdf_host_allowed('https://libgen.rs/article/123'))
        self.assertTrue(is_pdf_host_allowed('https://pmc.ncbi.nlm.nih.gov/PMC123/pdf'))
        self.assertTrue(is_pdf_host_allowed('https://example.com/paper.pdf'))

    def test_empty_lists_allow_all(self):
        """When both lists are empty, all hosts are allowed."""
        PDF_HOSTS['allow'] = []
        PDF_HOSTS['deny'] = []

        self.assertTrue(is_pdf_host_allowed('https://any-host.com/paper.pdf'))
        self.assertTrue(is_pdf_host_allowed('https://sci-hub.se/10.1000/xyz'))

    def test_malformed_url_fallback(self):
        """Malformed URLs should not raise exceptions."""
        PDF_HOSTS['allow'] = ['example.com']
        PDF_HOSTS['deny'] = []

        # These should not raise exceptions (behavior depends on allow list)
        # With allow list active, empty/invalid URLs won't match -> False
        self.assertFalse(is_pdf_host_allowed(''))
        self.assertFalse(is_pdf_host_allowed('not-a-url'))

        # With no allow list, fallback behavior allows through
        PDF_HOSTS['allow'] = []
        PDF_HOSTS['deny'] = []
        self.assertTrue(is_pdf_host_allowed(''))
        self.assertTrue(is_pdf_host_allowed('not-a-url'))


class TestExcelExport(unittest.TestCase):
    """Test Excel export functionality."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="litpipe_export_")
        # Point the pipeline to a temp database
        self.original_onedrive = PATHS['onedrive_base']
        self.original_db = PATHS['database']
        PATHS['onedrive_base'] = self.tmpdir
        PATHS['database'] = os.path.join(self.tmpdir, 'database', 'articles.db')
        init_database()

        # Insert test articles
        self._insert_test_articles()

    def tearDown(self):
        # Restore original paths
        PATHS['onedrive_base'] = self.original_onedrive
        PATHS['database'] = self.original_db
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert_test_articles(self):
        """Insert sample articles for export testing."""
        articles = [
            ('11111', 'Article One', 'Author A', 'Journal A', 2024, '10.1/a', 'Abstract one', 'key1', 'topic_a', 'https://pubmed/11111'),
            ('22222', 'Article Two', 'Author B', 'Journal B', 2023, '10.1/b', 'Abstract two', 'key2', 'topic_a', 'https://pubmed/22222'),
            ('33333', 'Article Three', 'Author C', 'Journal C', 2022, '10.1/c', 'Abstract three', 'key3', 'topic_b', 'https://pubmed/33333'),
        ]
        conn = sqlite3.connect(PATHS['database'])
        for a in articles:
            save_article(a, conn=conn)
        conn.commit()
        conn.close()

    def test_excel_export_creates_file(self):
        """Excel export should create a valid .xlsx file."""
        output_path = os.path.join(self.tmpdir, 'test_export.xlsx')
        result = export_articles_excel(output_path=output_path)

        self.assertEqual(result, output_path)
        self.assertTrue(os.path.exists(output_path))
        self.assertGreater(os.path.getsize(output_path), 0)

    def test_excel_export_split_by_topic(self):
        """Excel export with split_by_topic should create multiple sheets."""
        output_path = os.path.join(self.tmpdir, 'test_split.xlsx')
        result = export_articles_excel(output_path=output_path, split_by_topic=True)

        self.assertTrue(os.path.exists(result))

        # Verify file has content (basic check)
        import openpyxl
        wb = openpyxl.load_workbook(result)
        sheet_names = wb.sheetnames
        wb.close()

        # Should have sheets for topic_a and topic_b
        self.assertGreaterEqual(len(sheet_names), 2)

    def test_csv_export_creates_file(self):
        """CSV export should create a valid .csv file."""
        output_path = os.path.join(self.tmpdir, 'test_export.csv')
        result = export_articles_csv(output_path=output_path)

        self.assertEqual(result, output_path)
        self.assertTrue(os.path.exists(output_path))

        # Verify content
        with open(output_path, 'r') as f:
            content = f.read()
        self.assertIn('Article One', content)
        self.assertIn('Article Two', content)


if __name__ == '__main__':
    unittest.main()
