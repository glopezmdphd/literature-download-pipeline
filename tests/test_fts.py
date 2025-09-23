import os
import shutil
import sqlite3
import tempfile
import unittest

import main


class TestFTSIndexingAndSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="litpipe_")
        # Point the pipeline to a temp database and logs
        main.PATHS['onedrive_base'] = self.tmpdir
        main.PATHS['database'] = os.path.join(self.tmpdir, 'database', 'articles.db')
        main.PATHS['log_file'] = os.path.join(self.tmpdir, 'logs', 'pipeline.log')
        main.init_database()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fts_search_and_triggers(self):
        # Insert two articles
        a1 = (
            '12345',
            'Glioma prognostic features on CT',
            'Smith, Jane; Doe, John',
            'Neuro Imaging',
            2024,
            '10.1000/xyz123',
            'We investigate prognostic markers on head CT for glioma.',
            'glioma; computed tomography; prognosis',
            'test_topic',
            'https://pubmed.ncbi.nlm.nih.gov/12345/'
        )
        a2 = (
            '67890',
            'CT-based stroke detection using AI',
            'Lee, Alex',
            'Brain Journal',
            2023,
            '10.1000/abc456',
            'Deep learning for ischemic stroke on non-contrast CT.',
            'stroke; artificial intelligence; computed tomography',
            'test_topic',
            'https://pubmed.ncbi.nlm.nih.gov/67890/'
        )

        conn = sqlite3.connect(main.PATHS['database'])
        main.save_article(a1, conn=conn)
        main.save_article(a2, conn=conn)
        conn.commit()
        conn.close()

        # FTS search should find by title/abstract/keywords
        res1 = main.search_database('glioma')
        pmids1 = {str(r[0]) for r in res1}
        self.assertIn('12345', pmids1)

        res2 = main.search_database('stroke')
        pmids2 = {str(r[0]) for r in res2}
        self.assertIn('67890', pmids2)

        # Update triggers should preserve FTS contents after an update
        conn = sqlite3.connect(main.PATHS['database'])
        main.update_pdf_status('12345', pdf_path=os.path.join(self.tmpdir, 'PDFs', 'dummy.pdf'), downloaded=True, attempts=1, conn=conn)
        conn.commit()
        conn.close()

        res3 = main.search_database('prognostic')
        pmids3 = {str(r[0]) for r in res3}
        self.assertIn('12345', pmids3)

    def test_like_fallback_without_fts(self):
        # Insert one article
        a = (
            '24680',
            'Traumatic brain injury patterns on CT',
            'Kim, Taylor',
            'Neuro Emerg',
            2022,
            '10.1000/tbi789',
            'Study of TBI patterns and outcomes on CT imaging.',
            'traumatic brain injury; tbi; ct',
            'test_topic',
            'https://pubmed.ncbi.nlm.nih.gov/24680/'
        )
        conn = sqlite3.connect(main.PATHS['database'])
        main.save_article(a, conn=conn)
        conn.commit()
        # Drop FTS to force fallback path
        conn.execute('DROP TABLE IF EXISTS articles_fts')
        conn.commit()
        conn.close()

        res = main.search_database('injury')
        pmids = {str(r[0]) for r in res}
        self.assertIn('24680', pmids)


if __name__ == '__main__':
    unittest.main()

