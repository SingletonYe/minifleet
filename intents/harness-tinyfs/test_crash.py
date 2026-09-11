"""Power-loss injection: kill the writer mid-write and check what was promised."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness import driver

from tinyfs.fs import FileSystem

OPS = [
    {"op": "put", "name": "f0", "size": 4096, "fsync": True},
    {"op": "put", "name": "f1", "size": 9000, "fsync": True},
    {"op": "put", "name": "f2", "size": 1, "fsync": True},
    {"op": "put", "name": "f3", "size": 16384, "fsync": True},
    {"op": "put", "name": "dirty", "size": 8192, "fsync": False},
]

CRASH_POINTS = [1, 2, 3, 4, 5, 6, 8, 10, 13, 17, 22, 28, 35, 43, 52, 63, 76, 91, 110, 133]


class CrashTest(unittest.TestCase):
    def replay(self, image: Path) -> None:
        """Mounting is what replays a committed journal; then check the result."""

        fs = FileSystem.mount(str(image))
        fs.unmount()

    def assert_promises_kept(self, image: Path, progress: list[dict]) -> None:
        self.replay(image)
        report = driver.fsck(image)
        self.assertTrue(report["ok"], f"structure is inconsistent after the crash: {report['problems']}")
        fs = FileSystem.mount(str(image))
        try:
            for entry in progress:
                if entry.get("op") != "put":
                    continue
                name, size = entry["name"], entry["size"]
                self.assertEqual(
                    fs.read(name), driver.payload_for(name, size),
                    f"{name!r} was fsynced before the crash and did not survive intact",
                )
        finally:
            fs.unmount()

    def test_crash_at_every_write_boundary(self):
        crashes = 0
        for point in CRASH_POINTS:
            with self.subTest(crash_after=point), tempfile.TemporaryDirectory() as tmp:
                image = Path(tmp) / "disk.img"
                progress = Path(tmp) / "progress.log"
                self.assertEqual(driver.mkfs(image, blocks=64).returncode, 0)
                code, entries, tail = driver.workload(image, OPS, progress, crash_after=point)
                crashes += 1 if code == 42 else 0
                self.assert_promises_kept(image, entries)

                # and the filesystem has to keep working afterwards
                fs = FileSystem.mount(str(image))
                try:
                    if any(name == "after" for name, _ in fs.list()):
                        fs.unlink("after")
                    fs.create("after")
                    fs.write("after", driver.payload_for("after", 5000))
                    fs.fsync()
                finally:
                    fs.unmount()
                self.assertTrue(driver.fsck(image)["ok"], tail)
        self.assertGreater(crashes, 0, "no crash point was ever reached: the injection is not working")

    def test_sigkill_between_block_writes(self):
        for delay in (0.02, 0.05, 0.09, 0.15):
            with self.subTest(kill_after=delay), tempfile.TemporaryDirectory() as tmp:
                image = Path(tmp) / "disk.img"
                progress = Path(tmp) / "progress.log"
                self.assertEqual(driver.mkfs(image, blocks=64).returncode, 0)
                code, entries, tail = driver.workload(image, OPS, progress, kill_after=delay)
                self.assertIn(code, (0, -9, 137, 42), f"unexpected exit {code}: {tail}")
                self.assert_promises_kept(image, entries)


if __name__ == "__main__":
    unittest.main()
