"""A random operation sequence must agree with a model, across remounts."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from harness import driver

from tinyfs.fs import FileSystem

NAMES = ["a", "bb", "ccc", "file-1", "file-2", "log", "tmp", "zed"]
OPS = 320


class DifferentialTest(unittest.TestCase):
    def test_random_sequence_matches_the_model(self):
        rng = random.Random(20260911)
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "disk.img"
            FileSystem.format(str(image), blocks=128)
            fs = FileSystem.mount(str(image))
            model: dict[str, bytes] = {}
            try:
                for step in range(OPS):
                    name = rng.choice(NAMES)
                    action = rng.choices(
                        ["write", "read", "unlink", "list", "remount"],
                        weights=[5, 3, 2, 2, 1],
                    )[0]
                    if action == "write":
                        size = rng.choice([0, 1, 512, 4096, 4097, 9000, driver.MAX_FILE])
                        data = rng.randbytes(size)
                        try:
                            fs.write(name, data)
                        except FileNotFoundError:
                            fs.create(name)
                            fs.write(name, data)
                        model[name] = data
                    elif action == "read":
                        if name in model:
                            self.assertEqual(
                                fs.read(name), model[name],
                                f"step {step}: content of {name!r} diverged",
                            )
                        else:
                            with self.assertRaises(FileNotFoundError):
                                fs.read(name)
                    elif action == "unlink":
                        if name in model:
                            fs.unlink(name)
                            model.pop(name)
                        else:
                            with self.assertRaises(FileNotFoundError):
                                fs.unlink(name)
                    elif action == "list":
                        self.assertEqual(fs.list(), self._expected(model), f"step {step}: listing diverged")
                    else:
                        fs.unmount()
                        fs = FileSystem.mount(str(image))
                        for key, value in model.items():
                            self.assertEqual(
                                fs.read(key), value,
                                f"step {step}: {key!r} did not survive a remount",
                            )
                    if step % 17 == 0:
                        fs.fsync()
                fs.fsync()
                self.assertEqual(fs.list(), self._expected(model))
            finally:
                try:
                    fs.unmount()
                except Exception:
                    pass
            self.assertTrue(driver.fsck(image)["ok"], driver.fsck(image)["problems"])

    @staticmethod
    def _expected(model: dict[str, bytes]) -> list[tuple[str, int]]:
        return sorted((name, len(data)) for name, data in model.items())


if __name__ == "__main__":
    unittest.main()
