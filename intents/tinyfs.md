# tinyfs: a crash-consistent filesystem core

A small filesystem that lives in one image file and keeps its structure intact across a
power loss. The hard part is not the API, it is the promise: after `fsync` returns, the
data is there, and after a crash the structure is never broken. That promise is what this
intent is judged on - a random operation sequence is compared against a model across
remounts, the writer is killed at block-write boundaries and the promises are checked
afterwards, an independent fsck has to reject every corrupted image, and the filesystem
has to stay inside its throughput budgets.

## Deliverables

- tinyfs/device.py - the block device, the on-disk layout, format_image and the block/inode allocator
- tinyfs/journal.py - the write-ahead journal: append, commit, replay, clear
- tinyfs/fsck.py - the independent structural checker: it reads the frozen byte format, never tinyfs/device.py
- tinyfs/fs.py - the filesystem API everything else is written against | depends: device, journal
- tinyfs/cli.py - the process boundary: mkfs, put, get, ls, rm, fsck, workload | depends: fsck
- tinyfs/__init__.py - package marker and the public exports | of: cli
- tinyfs/__main__.py - the `python3 -m tinyfs` entry point | of: cli
- tests/test_device.py - the device writer's own tests
- tests/test_journal.py - the journal writer's own tests
- tests/test_fsck.py - the checker writer's own tests
- tests/test_fs.py - the API writer's own tests
- tests/test_cli.py - the CLI writer's own tests

## Acceptance

- [harness:api] the documented API semantics hold: duplicates, missing names, name and size limits, disk full, and durability across a clean unmount
- [harness:differential] a random operation sequence matches a model, including across remounts and after re-reading every file
- [harness:crash] killing the writer at a block-write boundary never breaks the structure, and every fsync that had returned is honoured afterwards
- [harness:fsck] fsck accepts a healthy image and rejects a corrupted one, saying why, without modifying the image
- [policy] nothing outside the standard library is imported

## Constraints

- Python 3.12 standard library only
- the on-disk format in the contract is frozen: the harness reads the image byte by byte and will not negotiate
- harness/ is frozen: workers may read it, never modify it
- the CLI must import tinyfs.fs and tinyfs.fsck lazily, inside its subcommand functions, so that cli.py can be built and tested before those modules land
- a dependency is build-time coupling, not runtime coupling: fsck and cli may only depend on what they must *import to be written and unit-tested*. cli.py's own tests cover the argument surface and never import tinyfs.fs; whether the CLI actually drives the filesystem is settled by the frozen harness, not by the worker

## Out of scope

- subdirectories, rename, permissions, ownership, symlinks
- indirect blocks: a file is at most four data blocks
- concurrency: one process at a time owns the image
- a real block device: the image is a plain file

## Budgets

- fs_write_mbps >= 2
- fs_read_mbps >= 5
- fs_create_ops >= 100

## Probes

- fs_write_mbps, fs_read_mbps, fs_create_ops: python3 -m harness.perf_probe

## Harness

- dir: harness-tinyfs
- modules: test_api, test_differential, test_crash, test_fsck

## Contract

### Geometry

- block size 4096 bytes, image length exactly `blocks * 4096`.
- block 0: superblock, JSON, UTF-8, zero padded: `{"magic": "TINYFS1", "block_size": 4096, "blocks": N, "inode_count": 64, "journal_start": 1, "journal_blocks": 3, "inode_table_block": 4, "bitmap_block": 5, "data_start": 8, "data_blocks": N - 8}`.
- block 1: journal header, JSON, UTF-8, zero padded: `{"magic": "TINYFSJ1", "seq": int, "committed": bool, "crc": int, "payload_blocks": [slot, ...], "homes": [home_block, ...]}` where slot is a journal block (2 or 3) and home is the block that payload belongs to.
- blocks 2-3: journal payload, at most 2 blocks, raw bytes.
- block 4: inode table, 64 inodes of 64 bytes each.
- block 5: block bitmap, 4096 bytes; bit `i` describes data block `data_start + i`.
- blocks 6-7: reserved, must stay zero.
- blocks 8..: data blocks.

### Inode layout (64 bytes, little endian)

- `[0]` used (0 or 1), `[1]` kind (1 = file), `[2]` name_len (0..31), `[3]` padding
- `[4:8]` size, `[8:12]` mtime (both unsigned 32 bit)
- `[16:48]` name bytes, not NUL terminated
- `[48:64]` four unsigned 32 bit data block indices; 0 means unused, a used block index is always >= data_start

### Limits

- a name is 1 to 31 bytes, otherwise ValueError
- a file is 0 to 16384 bytes, otherwise ValueError
- an inode is allocated for the whole life of a file

### tinyfs.device - frozen surface

- `format_image(path: str, blocks: int) -> None` writes a fresh, empty image.
- `BlockDevice(path: str)` with `read_block(index: int) -> bytes`, `write_block(index: int, data: bytes) -> None`, `sync() -> None`, `close() -> None`, and a `blocks` attribute.
- crash hook: every physical `write_block` increments a counter; when `TINYFS_CRASH_AFTER_WRITES` is set to K > 0 and the counter reaches K, the process must call `os._exit(42)` immediately after that block write returns. A `sync()` must flush to the operating system.

### tinyfs.journal - frozen surface

- `append(device, entries: list[tuple[int, bytes]], seq: int) -> None`, `commit(device) -> None`, `replay(device) -> bool`, `clear(device) -> None`, `pending(device) -> dict | None`.
- entries are `(block_index, payload)` pairs; at most 2 payload blocks fit.
- `crc` is `zlib.crc32` over the concatenated payload blocks, exactly as they are written.
- `replay` applies the payload of a *committed* record whose crc matches, then clears it; it returns whether it applied anything. A committed record whose crc does not match is refused, never applied.

### tinyfs.fs - frozen surface

- `FileSystem.format(path: str, blocks: int = 256) -> None` (classmethod)
- `FileSystem.mount(path: str) -> FileSystem` (classmethod): replays a committed journal before returning
- `create(name: str) -> None`, `write(name: str, data: bytes) -> None`, `read(name: str) -> bytes`, `unlink(name: str) -> None`, `list() -> list[tuple[str, int]]` (name, size, sorted by name), `fsync() -> None`, `unmount() -> None`
- errors: `FileExistsError` on a duplicate name, `FileNotFoundError` on a missing one, `ValueError` on a bad name or an oversized file, `OSError` when the image has no free blocks
- durability: after `fsync()` returns, every earlier mutation survives a crash; `unmount()` fsyncs
- crash consistency: metadata reaches its home block only from a committed journal record, and the commit flag is written only after the payload has been synced
- `unmount()` is idempotent; `list()` is ordered by name

### tinyfs.fsck - frozen surface

- `check(path: str) -> dict` returning `{"ok": bool, "problems": list[str]}`; read only, it must not modify the image and must not replay the journal.
- it reports: a bad magic or inconsistent geometry, an image whose length is wrong, an inode with a name longer than 31 bytes, an unknown kind, a size above the maximum, a block count that disagrees with the size, a block index outside the data area, two inodes sharing a block, a bitmap bit set for a block nobody references, a bitmap bit set outside the data area, and a committed journal header whose crc does not match its payload.

### tinyfs.cli - frozen surface

- `python3 -m tinyfs mkfs --image PATH [--blocks N]`
- `python3 -m tinyfs put --image PATH --name NAME --data-file PATH [--fsync]`
- `python3 -m tinyfs get --image PATH --name NAME [--out PATH]` (stdout when no --out)
- `python3 -m tinyfs ls --image PATH` prints `<name> <size>` per line, sorted
- `python3 -m tinyfs rm --image PATH --name NAME [--fsync]`
- `python3 -m tinyfs fsck --image PATH` prints the `check()` JSON on stdout
- `python3 -m tinyfs workload --image PATH --script PATH --progress PATH`
- exit codes: 0 success, 1 a reported failure (message on stderr), 2 usage

### workload script format

A JSON list of operations, executed in order:

- `{"op": "put", "name": str, "size": int, "fsync": bool}` writes `payload_for(name, size)` - the bytes `(name.encode() * (size // len(name) + 1))[:size]` - creating the file when needed.
- `{"op": "rm", "name": str, "fsync": bool}`
- `{"op": "get", "name": str}` re-reads the file and fails the process if the content differs.

After an operation whose `fsync` is true, and after `fsync()` has returned, the workload appends one
JSON line `{"op": ..., "name": ..., "size": ...}` to the progress file, flushes it and fsyncs that
file too. The progress file is the harness's record of what the filesystem promised.

## Meta

- id: tinyfs
- max_attempts: 3
- max_rounds: 6
- max_dispatches: 12
- max_wall_seconds: 5400
