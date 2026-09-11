"""Semantics of the filesystem API, on a real image, in-process."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness import driver

from tinyfs.fs import FileSystem


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / "disk.img"
        FileSystem.format(str(self.image), blocks=64)
        self.fs = FileSystem.mount(str(self.image))

    def tearDown(self):
        try:
            self.fs.unmount()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_roundtrip_and_listing(self):
        self.fs.create("alpha")
        payload = driver.payload_for("alpha", 9000)
        self.fs.write("alpha", payload)
        self.fs.fsync()
        self.assertEqual(self.fs.read("alpha"), payload)
        self.fs.create("beta")
        self.fs.write("beta", b"")
        self.assertEqual(self.fs.list(), [("alpha", 9000), ("beta", 0)])

    def test_blocks_are_allocated_exactly(self):
        self.fs.create("alpha")
        self.fs.write("alpha", driver.payload_for("alpha", 9000))
        self.fs.fsync()
        self.assertEqual(driver.count_set_bits(self.image, 32), 3)
        self.assertTrue(driver.fsck(self.image)["ok"])

    def test_unlink_returns_the_space(self):
        self.fs.create("alpha")
        self.fs.write("alpha", driver.payload_for("alpha", 9000))
        self.fs.fsync()
        self.fs.unlink("alpha")
        self.fs.fsync()
        self.assertEqual(driver.count_set_bits(self.image, 32), 0)
        self.assertEqual(self.fs.list(), [])
        self.assertTrue(driver.fsck(self.image)["ok"])

    def test_duplicate_and_missing_names(self):
        self.fs.create("alpha")
        with self.assertRaises(FileExistsError):
            self.fs.create("alpha")
        with self.assertRaises(FileNotFoundError):
            self.fs.read("nope")
        with self.assertRaises(FileNotFoundError):
            self.fs.unlink("nope")
        with self.assertRaises(FileNotFoundError):
            self.fs.write("nope", b"data")

    def test_name_and_size_limits(self):
        with self.assertRaises(ValueError):
            self.fs.create("")
        with self.assertRaises(ValueError):
            self.fs.create("x" * 32)
        self.fs.create("big")
        self.fs.write("big", b"z" * driver.MAX_FILE)
        self.assertEqual(self.fs.read("big"), b"z" * driver.MAX_FILE)
        with self.assertRaises(ValueError):
            self.fs.write("big", b"z" * (driver.MAX_FILE + 1))

    def test_disk_full_is_an_error_not_corruption(self):
        small = Path(self.tmp.name) / "small.img"
        FileSystem.format(str(small), blocks=16)          # 8 data blocks
        fs = FileSystem.mount(str(small))
        try:
            fs.create("one")
            fs.write("one", b"a" * driver.MAX_FILE)       # 4 blocks
            fs.create("two")
            fs.write("two", b"b" * driver.MAX_FILE)       # 4 blocks
            fs.create("three")
            with self.assertRaises(OSError):
                fs.write("three", b"c" * 4096)
            fs.fsync()
        finally:
            fs.unmount()
        self.assertTrue(driver.fsck(small)["ok"])

    def test_a_clean_unmount_makes_everything_durable(self):
        self.fs.create("alpha")
        self.fs.write("alpha", driver.payload_for("alpha", 4096))
        self.fs.unmount()
        reopened = FileSystem.mount(str(self.image))
        try:
            self.assertEqual(reopened.read("alpha"), driver.payload_for("alpha", 4096))
        finally:
            reopened.unmount()
        self.assertTrue(driver.fsck(self.image)["ok"])


if __name__ == "__main__":
    unittest.main()
