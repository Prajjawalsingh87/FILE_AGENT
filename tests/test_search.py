import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.api import app
from app.search import BM25Index, DataDirSearch, tokenize
from app.search.bm25 import MAX_QUERY_LENGTH, make_snippet
from app.tools import search_tools


class TokenizeTests(unittest.TestCase):
    def test_lowercases_and_splits_punctuation(self):
        self.assertEqual(
            tokenize("Hello, World! foo_bar-baz"), ["hello", "world", "foo", "bar", "baz"]
        )

    def test_drops_stopwords_and_huge_tokens(self):
        self.assertEqual(tokenize("the cat and " + "x" * 100), ["cat"])


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index()
        self.index.add("a.txt", "apple banana apple cherry")
        self.index.add("b.txt", "banana smoothie with milk")
        self.index.add("c.txt", "completely unrelated text about trains " * 5)

    def test_ranks_by_term_frequency_and_rarity(self):
        total, hits = self.index.search("apple banana")
        self.assertEqual(total, 2)
        self.assertEqual([h.path for h in hits], ["a.txt", "b.txt"])
        self.assertGreater(hits[0].score, hits[1].score)

    def test_rare_term_beats_common_term(self):
        _, hits = self.index.search("smoothie")
        self.assertEqual(hits[0].path, "b.txt")

    def test_filename_is_searchable(self):
        index = BM25Index()
        index.add("notes.txt", "nothing here")
        index.add("other.txt", "nothing either")
        _, hits = index.search("notes")
        self.assertEqual([h.path for h in hits], ["notes.txt"])

    def test_limit_and_offset(self):
        total, first = self.index.search("banana", limit=1)
        _, second = self.index.search("banana", limit=1, offset=1)
        self.assertEqual(total, 2)
        self.assertEqual(len(first), 1)
        self.assertNotEqual(first[0].path, second[0].path)

    def test_no_match_and_empty_query(self):
        self.assertEqual(self.index.search("zebra"), (0, []))
        self.assertEqual(self.index.search("!!!"), (0, []))

    def test_rejects_bad_bounds(self):
        with self.assertRaises(ValueError):
            self.index.search("apple", limit=0)
        with self.assertRaises(ValueError):
            self.index.search("apple", offset=-1)
        with self.assertRaises(ValueError):
            self.index.search("a" * (MAX_QUERY_LENGTH + 1))


class SnippetTests(unittest.TestCase):
    def test_highlights_and_escapes(self):
        snippet = make_snippet("<b>Hello</b> world, hello again", ["hello"])
        self.assertIn("<mark>Hello</mark>", snippet)
        self.assertIn("<mark>hello</mark>", snippet)
        self.assertIn("&lt;b&gt;", snippet)
        self.assertNotIn("<b>", snippet)

    def test_window_centres_on_match(self):
        text = "filler " * 200 + "needle in the haystack " + "filler " * 200
        snippet = make_snippet(text, ["needle"])
        self.assertTrue(snippet.startswith("… "))
        self.assertTrue(snippet.endswith(" …"))
        self.assertIn("<mark>needle</mark>", snippet)
        self.assertLess(len(snippet), 300)


class DataDirSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "one.txt").write_text("alpha beta", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "two.md").write_text("gamma delta", encoding="utf-8")
        (self.root / ".hidden").write_text("alpha", encoding="utf-8")
        (self.root / "blob.bin").write_bytes(b"alpha\x00\x01")
        self.search = DataDirSearch(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_indexes_text_files_recursively_skipping_hidden_and_binary(self):
        _, hits = self.search.search("alpha")
        self.assertEqual([h.path for h in hits], ["one.txt"])
        _, hits = self.search.search("gamma")
        self.assertEqual([h.path for h in hits], ["sub/two.md"])

    def test_rebuilds_only_when_files_change(self):
        self.search.search("alpha")
        self.search.search("alpha")
        self.assertEqual(self.search.builds, 1)

        path = self.root / "one.txt"
        path.write_text("omega", encoding="utf-8")
        later = time.time() + 5
        os.utime(path, (later, later))
        self.assertEqual(self.search.search("alpha"), (0, []))
        self.assertEqual(self.search.search("omega")[0], 1)
        self.assertEqual(self.search.builds, 2)

        (self.root / "three.txt").write_text("omega again", encoding="utf-8")
        self.assertEqual(self.search.search("omega")[0], 2)

    def test_missing_root_is_empty(self):
        self.assertEqual(DataDirSearch(self.root / "nope").search("alpha"), (0, []))


class SearchApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "notes.txt").write_text("Meeting notes: ship the search feature", encoding="utf-8")
        (root / "todo.txt").write_text("buy milk; review search PR; search again", encoding="utf-8")
        self.patch = mock.patch("app.api.get_search", return_value=DataDirSearch(root))
        self.patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_search_returns_ranked_hits_with_highlights(self):
        r = self.client.get("/search", params={"q": "search"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["hits"][0]["path"], "todo.txt")
        self.assertIn("<mark>search</mark>", body["hits"][0]["snippet"])
        self.assertEqual(body["hits"][0]["matched_terms"], ["search"])

    def test_pagination(self):
        body = self.client.get("/search", params={"q": "search", "limit": 1, "offset": 1}).json()
        self.assertEqual((body["total"], len(body["hits"])), (2, 1))
        self.assertEqual(body["hits"][0]["path"], "notes.txt")

    def test_input_limits(self):
        self.assertEqual(self.client.get("/search").status_code, 422)
        bad = ({"q": "   "}, {"q": "x" * 257}, {"q": "a", "limit": 51}, {"q": "a", "offset": -1})
        for params in bad:
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/search", params=params).status_code, 422)


class SearchToolTests(unittest.TestCase):
    def test_tool_formats_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("hello world", encoding="utf-8")
            fake = DataDirSearch(Path(tmp))
            with mock.patch.object(search_tools, "get_search", return_value=fake):
                out = search_tools.search_files("hello")
                self.assertIn("1. a.txt", out)
                self.assertIn("**hello**", out)
                self.assertIn("No files match", search_tools.search_files("zebra"))

    def test_tool_rejects_blank_query(self):
        with self.assertRaises(ValueError):
            search_tools.search_files("  ")


class HomePageTests(unittest.TestCase):
    def test_home_serves_ui(self):
        r = TestClient(app).get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn('id="search-form"', r.text)


if __name__ == "__main__":
    unittest.main()
