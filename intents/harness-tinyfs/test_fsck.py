"""fsck has to be an independent checker: it must catch corruption, and never pass it."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from harness import driver

from tinyfs.fs import FileSystem


class FsckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / "disk.img"
        driver.mkfs(self.image, blocks=64)
        fs = FileSystem.mount(str(self.image))
        fs.create("keep")
        fs.write("keep", driver.payload_for("keep", 9000))
        fs.create("other")
        fs.write("other", driver.payload_for("other", 4096))
        fs.fsync()
        fs.unmount()

    def tearDown(self):
        self.tmp.cleanup()


    def test_healthy_image_passes(self):
        report = driver.fsck(self.image)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["exit_code"], 0)

    def test_bad_magic_is_caught(self):
        raw = driver.read_block(self.image, driver.SUPERBLOCK_BLOCK)
        driver.write_block(self.image, driver.SUPERBLOCK_BLOCK,
                           raw.replace(driver.MAGIC.encode(), b"NOTFS1"))
        self.assert_caught()

    def test_truncated_image_is_caught(self):
        with open(self.image, "r+b") as handle:
            handle.truncate(driver.BLOCK * 32)
        self.assert_caught()

    def test_out_of_range_block_pointer_is_caught(self):
        table = driver.read_block(self.image, driver.INODE_TABLE_BLOCK)
        ghost = driver.pack_inode(used=True, kind=1, name="ghost", size=4096, blocks=[9999])
        slot = self.free_slot(table)
        driver.write_block(self.image, driver.INODE_TABLE_BLOCK,
                           self.put_inode(table, slot, ghost))
        self.assert_caught()

    def test_two_inodes_sharing_a_block_is_caught(self):
        table = driver.read_block(self.image, driver.INODE_TABLE_BLOCK)
        shared = struct.unpack_from("<I", table, 48)[0]
        twin = driver.pack_inode(used=True, kind=1, name="twin", size=4096, blocks=[shared])
        slot = self.free_slot(table)
        driver.write_block(self.image, driver.INODE_TABLE_BLOCK,
                           self.put_inode(table, slot, twin))
        self.assert_caught()

    def test_orphan_bitmap_bit_is_caught(self):
        superblock = driver.superblock(self.image)
        last = superblock["data_blocks"] - 1
        raw = bytearray(driver.read_block(self.image, driver.BITMAP_BLOCK))
        raw[last // 8] |= 1 << (last % 8)
        driver.write_block(self.image, driver.BITMAP_BLOCK, bytes(raw))
        self.assert_caught()

    def test_size_without_blocks_is_caught(self):
        table = driver.read_block(self.image, driver.INODE_TABLE_BLOCK)
        liar = driver.pack_inode(used=True, kind=1, name="liar", size=4096, blocks=[])
        slot = self.free_slot(table)
        driver.write_block(self.image, driver.INODE_TABLE_BLOCK,
                           self.put_inode(table, slot, liar))
        self.assert_caught()

    def test_overlong_name_is_caught(self):
        table = bytearray(driver.read_block(self.image, driver.INODE_TABLE_BLOCK))
        table[2] = 40                      # name_len of the first inode
        driver.write_block(self.image, driver.INODE_TABLE_BLOCK, bytes(table))
        self.assert_caught()

    def test_committed_journal_with_a_broken_checksum_is_caught(self):
        driver.write_block(
            self.image, driver.JOURNAL_BLOCK,
            b'{"magic": "TINYFSJ1", "seq": 3, "committed": true, "crc": 12345, '
            b'"payload_blocks": [4, 5]}',
        )
        self.assert_caught()

    # -- helpers ---------------------------------------------------------
    def assert_caught(self) -> None:
        report = driver.fsck(self.image)
        self.assertFalse(report["ok"], "fsck accepted a corrupt image")
        self.assertTrue(report["problems"], "fsck reported failure without saying why")
        self.assertEqual(report["exit_code"], 1)

    @staticmethod
    def free_slot(table: bytes) -> int:
        for index in range(driver.INODE_COUNT):
            if not table[index * driver.INODE_SIZE]:
                return index
        raise AssertionError("no free inode slot to corrupt")

    @staticmethod
    def put_inode(table: bytes, slot: int, inode: bytes) -> bytes:
        body = bytearray(table)
        offset = slot * driver.INODE_SIZE
        body[offset:offset + driver.INODE_SIZE] = inode
        return bytes(body)


if __name__ == "__main__":
    unittest.main()
