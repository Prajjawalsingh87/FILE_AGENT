import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import api, storage
from app.search import DataDirSearch
from app.storage import FileStore, UploadError, validate_content, validate_filename


class FilenameValidationTests(unittest.TestCase):
    def test_accepts_plain_text_names(self):
        for name in ["notes.txt", "README.md", "data-2026_10.csv", "my notes.txt", "a.json"]:
            self.assertEqual(validate_filename(name), name)

    def test_rejects_path_traversal_and_separators(self):
        for name in ["../etc/passwd", "..", "a/../b.txt", "dir/notes.txt", "dir\\notes.txt",
                     "/abs.txt", "C:\\x.txt", "a..b.txt"]:
            with self.assertRaises(UploadError, msg=name):
                validate_filename(name)

    def test_rejects_hidden_unsafe_and_reserved_names(self):
        for name in [".env", ".hidden.txt", "", "   ", "bad$name.txt", "x\x00.txt", "con.txt",
                     "-flag.txt", "a" * 101 + ".txt"]:
            with self.assertRaises(UploadError, msg=repr(name)):
                validate_filename(name)

    def test_rejects_disallowed_extensions(self):
        for name in ["app.exe", "photo.png", "archive.zip", "noext", "x.TXT.bin"]:
            with self.assertRaises(UploadError) as ctx:
                validate_filename(name)
            self.assertEqual(ctx.exception.status, 415)

    def test_extension_check_is_case_insensitive(self):
        self.assertEqual(validate_filename("NOTES.TXT"), "NOTES.TXT")


class ContentValidationTests(unittest.TestCase):
    def test_size_limit(self):
        validate_content(b"x" * storage.MAX_UPLOAD_BYTES)
        with self.assertRaises(UploadError) as ctx:
            validate_content(b"x" * (storage.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(ctx.exception.status, 413)

    def test_binary_and_non_utf8_rejected(self):
        with self.assertRaises(UploadError):
            validate_content(b"abc\x00def")
        with self.assertRaises(UploadError):
            validate_content(b"\xff\xfe\xfa")

    def test_utf8_text_ok(self):
        self.assertEqual(validate_content("héllo ✓".encode()), "héllo ✓")


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = FileStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_delete(self):
        self.store.save("a.txt", b"hello")
        (self.root / ".hidden").write_text("x")
        self.assertEqual([f.name for f in self.store.list()], ["a.txt"])
        self.assertEqual(self.store.usage()["bytes"], 5)
        self.store.delete("a.txt")
        self.assertEqual(self.store.list(), [])

    def test_duplicate_needs_overwrite(self):
        self.store.save("a.txt", b"one")
        with self.assertRaises(UploadError) as ctx:
            self.store.save("a.txt", b"two")
        self.assertEqual(ctx.exception.status, 409)
        self.store.save("a.txt", b"two", overwrite=True)
        self.assertEqual((self.root / "a.txt").read_text(), "two")

    def test_total_storage_cap(self):
        with mock.patch.object(storage, "MAX_TOTAL_BYTES", 10):
            self.store.save("a.txt", b"12345678")
            with self.assertRaises(UploadError) as ctx:
                self.store.save("b.txt", b"123")
            self.assertEqual(ctx.exception.status, 507)
            # Overwriting replaces the old size instead of adding to it.
            self.store.save("a.txt", b"1234567890", overwrite=True)

    def test_file_count_cap(self):
        with mock.patch.object(storage, "MAX_STORED_FILES", 2):
            self.store.save("a.txt", b"1")
            self.store.save("b.txt", b"2")
            with self.assertRaises(UploadError):
                self.store.save("c.txt", b"3")

    def test_delete_rejects_traversal_and_missing(self):
        outside = self.root.parent / "outside-victim.txt"
        for name in ["../outside-victim.txt", "..", ".env", "sub/a.txt"]:
            with self.assertRaises(UploadError, msg=name):
                self.store.delete(name)
        with self.assertRaises(UploadError) as ctx:
            self.store.delete("missing.txt")
        self.assertEqual(ctx.exception.status, 404)
        self.assertFalse(outside.exists())


class UploadApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.search = DataDirSearch(self.root)
        self.patches = [
            mock.patch.object(api, "get_store", return_value=FileStore(self.root)),
            mock.patch.object(api, "get_search", return_value=self.search),
        ]
        for p in self.patches:
            p.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def upload(self, name, data, **params):
        return self.client.post("/files", files={"file": (name, data, "text/plain")}, params=params)

    def test_upload_list_search_delete_roundtrip(self):
        r = self.upload("groceries.txt", b"buy oat milk and bread")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["name"], "groceries.txt")

        listing = self.client.get("/files").json()
        self.assertEqual([f["name"] for f in listing["files"]], ["groceries.txt"])
        self.assertEqual(listing["usage"]["max_file_bytes"], storage.MAX_UPLOAD_BYTES)

        hits = self.client.get("/search", params={"q": "oat milk"}).json()["hits"]
        self.assertEqual([h["path"] for h in hits], ["groceries.txt"])

        self.assertEqual(self.client.delete("/files/groceries.txt").status_code, 204)
        self.assertEqual(self.client.get("/search", params={"q": "milk"}).json()["total"], 0)
        self.assertEqual(self.client.delete("/files/groceries.txt").status_code, 404)

    def test_rejections(self):
        self.assertEqual(self.upload("evil.exe", b"MZ").status_code, 415)
        self.assertEqual(self.upload("../escape.txt", b"x").status_code, 400)
        self.assertEqual(self.upload(".env", b"KEY=1").status_code, 400)
        self.assertEqual(self.upload("bin.txt", b"a\x00b").status_code, 415)
        self.assertEqual(self.upload("dup.txt", b"1").status_code, 201)
        self.assertEqual(self.upload("dup.txt", b"2").status_code, 409)
        self.assertEqual(self.upload("dup.txt", b"2", overwrite="true").status_code, 201)
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_oversized_upload_rejected(self):
        r = self.upload("big.txt", b"x" * (storage.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(r.status_code, 413)
        r = self.upload("huge.txt", b"x" * (storage.MAX_UPLOAD_BYTES + 200_000))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.client.get("/files").json()["files"], [])

    def test_delete_traversal_via_url(self):
        r = self.client.delete("/files/..%2F..%2Fetc%2Fpasswd")
        self.assertIn(r.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
