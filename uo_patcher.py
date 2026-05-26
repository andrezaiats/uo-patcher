#!/usr/bin/env python3
"""
UO Classic Client Patcher - Downloads Ultima Online game files from EA/Broadsword patch servers.

The UO Classic Client is freely distributed by EA/Broadsword. This tool downloads the same
freely available files from the same public, unauthenticated HTTP servers used by the official
installer. No copy protection, authentication, or encryption is bypassed. Intended for use
with legitimate UO accounts and community-run shards (e.g. ServUO).

Protocol documented from UOClassicSetup_7_0_24_0.exe (EA Mythic Patcher v6).

Architecture:
  1. Fetch product file (.prod) from manifest repo - XML describing stages/packages
  2. Fetch package manifests (pkg.mft) - zlib-compressed XML listing sub-manifests
  3. Fetch sub-manifests - list individual files with hashes, sizes, compression info
  4. Download files from file repo using hash-based URLs
  5. Decompress zlib-compressed files and write to disk

File addressing:
  - Pack files (*.uop): URL = filerepos/base/<packname>/<ph_08x><sh_08x>
  - Unpacked files: URL = filerepos/base/unpacked/<hashlittle2(lowercase_filename)>
  - Hash function: Jenkins hashlittle2 with initval=0
"""

import argparse
import os
import sys
import struct
import zlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
import shutil
import time
import threading

# ---------------------------------------------------------------------------
# Jenkins hashlittle2 - used by Mythic patcher to hash filenames into 64-bit IDs
# ---------------------------------------------------------------------------

def _rot(x, k):
    return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF

def _mix(a, b, c):
    a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 4);  c = (c + b) & 0xFFFFFFFF
    b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 6);  a = (a + c) & 0xFFFFFFFF
    c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 8);  b = (b + a) & 0xFFFFFFFF
    a = (a - c) & 0xFFFFFFFF; a ^= _rot(c, 16); c = (c + b) & 0xFFFFFFFF
    b = (b - a) & 0xFFFFFFFF; b ^= _rot(a, 19); a = (a + c) & 0xFFFFFFFF
    c = (c - b) & 0xFFFFFFFF; c ^= _rot(b, 4);  b = (b + a) & 0xFFFFFFFF
    return a, b, c

def _final(a, b, c):
    c ^= b; c = (c - _rot(b, 14)) & 0xFFFFFFFF
    a ^= c; a = (a - _rot(c, 11)) & 0xFFFFFFFF
    b ^= a; b = (b - _rot(a, 25)) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 16)) & 0xFFFFFFFF
    a ^= c; a = (a - _rot(c, 4))  & 0xFFFFFFFF
    b ^= a; b = (b - _rot(a, 14)) & 0xFFFFFFFF
    c ^= b; c = (c - _rot(b, 24)) & 0xFFFFFFFF
    return a, b, c

def hashlittle2(data, initval=0, initval2=0):
    """Jenkins hashlittle2 hash. Returns (primary_hash, secondary_hash) as 32-bit ints."""
    if isinstance(data, str):
        data = data.encode('ascii')
    length = len(data)
    a = b = c = (0xdeadbeef + length + initval) & 0xFFFFFFFF
    c = (c + initval2) & 0xFFFFFFFF

    pos = 0
    while length > 12:
        a = (a + (data[pos]   | (data[pos+1]<<8) | (data[pos+2]<<16)  | (data[pos+3]<<24)))  & 0xFFFFFFFF
        b = (b + (data[pos+4] | (data[pos+5]<<8) | (data[pos+6]<<16)  | (data[pos+7]<<24)))  & 0xFFFFFFFF
        c = (c + (data[pos+8] | (data[pos+9]<<8) | (data[pos+10]<<16) | (data[pos+11]<<24))) & 0xFFFFFFFF
        a, b, c = _mix(a, b, c)
        pos += 12
        length -= 12

    if length > 0:
        rem = data[pos:pos+length]
        if length >= 1:  a = (a + rem[0])          & 0xFFFFFFFF
        if length >= 2:  a = (a + (rem[1] << 8))   & 0xFFFFFFFF
        if length >= 3:  a = (a + (rem[2] << 16))  & 0xFFFFFFFF
        if length >= 4:  a = (a + (rem[3] << 24))  & 0xFFFFFFFF
        if length >= 5:  b = (b + rem[4])           & 0xFFFFFFFF
        if length >= 6:  b = (b + (rem[5] << 8))    & 0xFFFFFFFF
        if length >= 7:  b = (b + (rem[6] << 16))   & 0xFFFFFFFF
        if length >= 8:  b = (b + (rem[7] << 24))   & 0xFFFFFFFF
        if length >= 9:  c = (c + rem[8])            & 0xFFFFFFFF
        if length >= 10: c = (c + (rem[9] << 8))     & 0xFFFFFFFF
        if length >= 11: c = (c + (rem[10] << 16))   & 0xFFFFFFFF
        if length >= 12: c = (c + (rem[11] << 24))   & 0xFFFFFFFF
        a, b, c = _final(a, b, c)

    return c, b


def filename_hash(name):
    """Compute the 16-char hex hash used in download URLs for a filename."""
    h1, h2 = hashlittle2(name.lower())
    return f"{h1:08x}{h2:08x}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_print_lock = threading.Lock()

def log(msg):
    with _print_lock:
        print(msg, flush=True)

def fetch_bytes(url, retries=3):
    """Download raw bytes from a URL with retries."""
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "UO Patcher/1.0"})
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(1 * (attempt + 1))
    return b''

def fetch_manifest_xml(url):
    """Download a zlib-compressed manifest and parse it as XML."""
    raw = fetch_bytes(url)
    try:
        xml_bytes = zlib.decompress(raw)
    except zlib.error:
        xml_bytes = raw  # Not compressed
    return ET.fromstring(xml_bytes.decode('utf-8', errors='replace'))


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def parse_prod(url):
    """Fetch and parse a .prod product file. Returns dict with repos and stages."""
    log(f"[*] Fetching product file: {url}")
    raw = fetch_bytes(url)
    root = ET.fromstring(raw.decode('utf-8'))
    product = root.find('product')

    manifest_repos = [r.get('url') for r in product.findall('.//manifestrepos/repo')]
    file_repos = [r.get('url') for r in product.findall('.//filerepos/repo')]

    stages = []
    for stage_el in product.findall('.//stages/stage'):
        packages = []
        for pkg_el in stage_el.findall('.//packages/package'):
            packages.append({
                'name': pkg_el.get('name'),
                'rpath': pkg_el.get('rpath'),
                'manifest_name': pkg_el.find('manifest').get('n'),
            })
        stages.append({
            'name': stage_el.get('name'),
            'packages': packages,
        })

    return {
        'manifest_repos': manifest_repos,
        'file_repos': file_repos,
        'stages': stages,
        'launchfile': product.get('launchfile'),
    }


def collect_files(manifest_repo, pkg_rpath, manifest_name):
    """Recursively fetch manifests and collect all file entries.

    Returns two lists: (unpacked_files, pack_files)
    - unpacked_files: list of dicts with 'name', 'ul', 'ct', 'cl' keys
    - pack_files: list of dicts with 'pack_name', 'pack_rpath', 'ph', 'sh', 'ul', 'ct', 'cl' keys
    """
    url = f"{manifest_repo}{pkg_rpath}/{manifest_name}"
    log(f"  Fetching manifest: {url}")
    root = fetch_manifest_xml(url)
    manifest = root.find('manifest')

    unpacked = []
    packs = []

    # Direct file entries (unpacked files) - only from <manifest><files>, not <packs><p><files>
    files_el = manifest.find('files')
    if files_el is not None:
        for f in files_el.findall('f'):
            entry = {
                'name': f.get('n'),
                'ul': int(f.get('ul', '0'), 16),
                'ct': int(f.get('ct', '0')),
                'cl': int(f.get('cl', '0'), 16),
            }
            unpacked.append(entry)

    # Pack file entries
    for p in manifest.findall('.//packs/p'):
        pack_name = p.get('name')
        pack_rpath = p.get('rpath')
        for f in p.findall('.//files/f'):
            entry = {
                'pack_name': pack_name,
                'pack_rpath': pack_rpath,
                'ph': f.get('ph'),
                'sh': f.get('sh'),
                'ul': int(f.get('ul', '0'), 16),
                'ct': int(f.get('ct', '0')),
                'cl': int(f.get('cl', '0'), 16),
            }
            packs.append(entry)

    # Sub-manifests (recursive)
    for sub in manifest.findall('.//manifests/manifest'):
        sub_name = sub.get('n')
        sub_unpacked, sub_packs = collect_files(manifest_repo, pkg_rpath, sub_name)
        unpacked.extend(sub_unpacked)
        packs.extend(sub_packs)

    return unpacked, packs


# ---------------------------------------------------------------------------
EA_MYP_SIGNATURE = 0xFD23EC43


def is_official_uop(path):
    """Check if a .uop file has the official EA MYP signature.

    Our builder writes sig=0; official EA files have sig=0xFD23EC43.
    """
    try:
        with open(path, 'rb') as f:
            magic = f.read(4)
            if magic != b'MYP\x00':
                return False
            f.read(4)  # version
            sig = struct.unpack('<I', f.read(4))[0]
            return sig == EA_MYP_SIGNATURE
    except (OSError, struct.error):
        return False


# UOP (MYP) archive index reader - for incremental updates
# ---------------------------------------------------------------------------

def read_uop_index(path):
    """Read a .uop archive's file table and return a set of (ph, sh, decompressed_size) tuples.

    UOP on-disk format (from UOFiddler/Mythic-Package-Editor):
      Header (28 bytes): magic(4) + version(4) + signature(4) + first_block(8) + capacity(4) + count(4)
      Block header (12 bytes): entry_count(4) + next_block(8)
      Entry (34 bytes): offset(8) + hdr_len(4) + comp_size(4) + decomp_size(4) + file_hash(8) + data_hash(4) + flag(2)

    The file_hash stores (sh << 32 | ph) — reversed from the manifest's ph:sh naming.
    The official patcher decompresses data when writing to the archive, so on-disk
    comp_size == decomp_size and flag == 0 for most entries.
    """
    try:
        fsize = os.path.getsize(path)
        if fsize < 28:
            return {}
    except OSError:
        return {}

    index = {}
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != b'MYP\x00':
            return {}
        f.read(4)  # version
        f.read(4)  # signature
        first_block = struct.unpack('<q', f.read(8))[0]
        f.read(4)  # capacity
        f.read(4)  # count

        block_offset = first_block
        while 0 < block_offset < fsize:
            f.seek(block_offset)
            bc_data = f.read(4)
            nb_data = f.read(8)
            if len(bc_data) < 4 or len(nb_data) < 8:
                break
            block_count = struct.unpack('<I', bc_data)[0]
            next_block = struct.unpack('<q', nb_data)[0]

            for _ in range(block_count):
                raw = f.read(34)
                if len(raw) < 34:
                    break
                data_offset = int.from_bytes(raw[0:8], 'little')
                decomp_size = int.from_bytes(raw[16:20], 'little')
                file_hash = int.from_bytes(raw[20:28], 'little')
                if data_offset == 0 and file_hash == 0:
                    continue
                # Convert on-disk hash to manifest (ph, sh) order
                ph = file_hash & 0xFFFFFFFF
                sh = (file_hash >> 32) & 0xFFFFFFFF
                index[(ph, sh)] = decomp_size

            block_offset = next_block

    return index


def build_pack_index(output_dir, pack_names):
    """Read all existing .uop files and build a combined index for skip checking.

    Returns dict: {(pack_name, ph_hex, sh_hex): decompressed_size}
    """
    combined = {}
    for pack_name in pack_names:
        uop_path = Path(output_dir) / pack_name
        if not uop_path.exists():
            continue
        index = read_uop_index(str(uop_path))
        for (ph, sh), decomp_size in index.items():
            combined[(pack_name, f"{ph:08x}", f"{sh:08x}")] = decomp_size
            # Also store without padding since manifest ph/sh may vary
            combined[(pack_name, f"{ph:x}", f"{sh:x}")] = decomp_size
    return combined


# ---------------------------------------------------------------------------
# File downloading
# ---------------------------------------------------------------------------

class ProgressTracker:
    def __init__(self, total_files, total_bytes):
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.done_files = 0
        self.done_bytes = 0
        self.failed = 0
        self.skipped = 0
        self._lock = threading.Lock()

    def update(self, nbytes, skipped=False, failed=False):
        with self._lock:
            self.done_files += 1
            self.done_bytes += nbytes
            if skipped:
                self.skipped += 1
            if failed:
                self.failed += 1

    def status(self):
        with self._lock:
            pct = (self.done_bytes / self.total_bytes * 100) if self.total_bytes else 0
            return (f"  [{self.done_files}/{self.total_files} files] "
                    f"[{self.done_bytes/1024/1024:.1f}/{self.total_bytes/1024/1024:.1f} MB] "
                    f"[{pct:.1f}%] "
                    f"[{self.skipped} skipped, {self.failed} failed]")


def _download_one_unpacked(file_repo, entry, output_dir):
    """Download a single unpacked file. Returns (entry, error_or_None)."""
    name = entry['name']
    ct = entry['ct']
    ul = entry['ul']

    out_path = Path(output_dir) / name
    if out_path.exists() and out_path.stat().st_size == ul:
        # Self-healing: force re-download .uop files that lack the EA signature
        # (produced by our old builder instead of downloaded from CDN)
        if out_path.suffix == '.uop' and not is_official_uop(str(out_path)):
            pass  # fall through to re-download
        else:
            return (entry, None, True)  # (entry, error, was_skipped)

    h = filename_hash(name)
    url = f"{file_repo}base/unpacked/{h}"

    try:
        data = fetch_bytes(url)
        if ct == 1:
            data = zlib.decompress(data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + '.tmp')
        tmp_path.write_bytes(data)
        tmp_path.rename(out_path)
        return (entry, None, False)
    except Exception as e:
        return (entry, str(e), False)


def _download_one_pack_entry(file_repo, entry, output_dir):
    """Download a single pack entry chunk. Returns (entry, error_or_None)."""
    ph = int(entry['ph'], 16)
    sh = int(entry['sh'], 16)
    rpath = entry['pack_rpath']

    url = f"{file_repo}base/{rpath}/{ph:08x}{sh:08x}"
    chunk_dir = Path(output_dir) / '.pack_staging' / entry['pack_name']
    chunk_dir.mkdir(parents=True, exist_ok=True)
    final_file = chunk_dir / f"{ph:08x}{sh:08x}"
    tmp_file = chunk_dir / f"{ph:08x}{sh:08x}.tmp"

    try:
        data = fetch_bytes(url)
        tmp_file.write_bytes(data)
        tmp_file.rename(final_file)
        return (entry, None)
    except Exception as e:
        tmp_file.unlink(missing_ok=True)
        return (entry, str(e))


def download_unpacked_parallel(file_repo, entries, output_dir, workers):
    """Download unpacked files in parallel. Returns list of failed entries."""
    if not entries:
        return []

    total_bytes = sum(e['cl'] if e['ct'] == 1 else e['ul'] for e in entries)
    progress = ProgressTracker(len(entries), total_bytes)
    failed = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_one_unpacked, file_repo, e, output_dir): e for e in entries}
        for i, fut in enumerate(as_completed(futures)):
            entry, error, skipped = fut.result()
            dl_size = entry['cl'] if entry['ct'] == 1 else entry['ul']
            if error:
                log(f"  FAIL: {entry['name']} - {error}")
                progress.update(dl_size, failed=True)
                failed.append(entry)
            else:
                progress.update(dl_size, skipped=skipped)
            if (i + 1) % 50 == 0 or (i + 1) == len(futures):
                log(progress.status())

    return failed


def download_packs_parallel(file_repo, entries, output_dir, workers, pack_index=None):
    """Download pack entries in parallel. Returns list of failed entries.

    Args:
        pack_index: dict from build_pack_index() for skip checking.
    """
    if not entries:
        return []

    # Determine which entries need downloading
    need_download = []
    skipped_count = 0
    for entry in entries:
        if pack_index is not None:
            key = (entry['pack_name'], entry['ph'], entry['sh'])
            if key in pack_index and pack_index[key] == entry['ul']:
                skipped_count += 1
                continue
        need_download.append(entry)

    if skipped_count:
        log(f"  {skipped_count} pack entries already up-to-date, {len(need_download)} need download")
    if not need_download:
        return []

    total_bytes = sum(e['cl'] if e['ct'] == 1 else e['ul'] for e in need_download)
    progress = ProgressTracker(len(need_download), total_bytes)
    failed = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download_one_pack_entry, file_repo, e, output_dir): e for e in need_download}
        for i, fut in enumerate(as_completed(futures)):
            entry, error = fut.result()
            dl_size = entry['cl'] if entry['ct'] == 1 else entry['ul']
            if error:
                log(f"  FAIL: {entry['pack_name']}/{entry['ph']}{entry['sh']} - {error}")
                progress.update(dl_size, failed=True)
                failed.append(entry)
            else:
                progress.update(dl_size)
            if (i + 1) % 500 == 0 or (i + 1) == len(futures):
                log(progress.status())

    return failed


def build_myp_archive(output_dir, pack_name, entries):
    """Build a MYP v5 archive from downloaded chunks.

    Replicates the official EA patcher layout:
      Header (28 bytes) + padding to offset 512
      File table block at offset 512 (capacity entries + block header)
      Data entries after file table

    Entry format (34 bytes in file table):
      data_offset(8) + hdr_len(4) + comp_size(4) + decomp_size(4)
      + file_hash(8) + adler32(4) + compression_flag(2)
    """
    chunk_dir = Path(output_dir) / '.pack_staging' / pack_name
    if not chunk_dir.exists():
        return

    out_path = Path(output_dir) / pack_name
    log(f"  Building MYP archive: {pack_name}")

    sorted_entries = sorted(entries, key=lambda e: int(e['ph'], 16))

    TABLE_ENTRY_SIZE = 34
    TABLE_BLOCK_SIZE = 1000
    DATA_START = 0x200       # 512 — DefaultStartAddress from Mythic Package Editor
    BLOCK_HEADER_SIZE = 12   # entry_count(4) + next_block(8)
    TABLE_BLOCK_BYTES = BLOCK_HEADER_SIZE + TABLE_BLOCK_SIZE * TABLE_ENTRY_SIZE  # 34012
    # Metadata header prepended to each data entry (v5 format).
    # Official EA headers are 137 bytes: type(2)=4 + data_len(2)=133 + signature(133).
    # ServUO/Ultima only reads hdr_len to skip past the header (offset + headerLength),
    # never inspects the content, so we zero-fill the signature portion.
    ENTRY_HEADER_LEN = 137
    ENTRY_HEADER = struct.pack('<HH', 4, 133) + b'\x00' * 133  # type=4, len=133, zeroed data

    # First pass: read chunks, decompress if needed
    file_records = []
    skipped = 0

    for entry in sorted_entries:
        ph = int(entry['ph'], 16)
        sh = int(entry['sh'], 16)
        ct = entry['ct']
        ul = entry['ul']

        chunk_file = chunk_dir / f"{ph:08x}{sh:08x}"
        if not chunk_file.exists():
            log(f"    WARN: missing chunk {ph:08x}{sh:08x} for {pack_name}, skipping entry")
            skipped += 1
            continue

        raw_data = chunk_file.read_bytes()
        if ct == 1:
            file_data = zlib.decompress(raw_data)
        else:
            file_data = raw_data

        data_size = len(file_data)
        if data_size != ul:
            log(f"    WARN: {ph:08x}{sh:08x} size mismatch: got {data_size}, manifest says {ul}")

        file_records.append({
            'ph': ph,
            'sh': sh,
            'data_size': data_size,
            'file_data': file_data,
        })

    if skipped:
        log(f"    {skipped} entries skipped due to missing chunks")

    num_entries = len(file_records)
    num_table_blocks = (num_entries + TABLE_BLOCK_SIZE - 1) // TABLE_BLOCK_SIZE if num_entries > 0 else 0

    if num_entries == 0:
        log(f"    ERROR: no valid entries for {pack_name}, skipping archive creation")
        return

    # Layout (matches official EA patcher):
    #   header(28) + pad(484) + entry0(hdr+data) + file_table + entry1..N(hdr+data)
    # First data entry at offset 512, file table right after it.
    first_entry_offset = DATA_START
    entry_with_header = ENTRY_HEADER_LEN + file_records[0]['data_size']
    first_table_offset = first_entry_offset + entry_with_header
    remaining_data_offset = first_table_offset + num_table_blocks * TABLE_BLOCK_BYTES

    # Assign offsets: entry 0 goes before file table, rest go after
    file_records[0]['offset'] = first_entry_offset
    current_offset = remaining_data_offset
    for rec in file_records[1:]:
        rec['offset'] = current_offset
        current_offset += ENTRY_HEADER_LEN + rec['data_size']

    with open(out_path, 'wb') as f:
        # Header (28 bytes)
        f.write(b'MYP\x00')
        f.write(struct.pack('<I', 5))                      # version
        f.write(struct.pack('<I', EA_MYP_SIGNATURE))       # signature (0xFD23EC43)
        f.write(struct.pack('<q', first_table_offset))     # first table block offset
        f.write(struct.pack('<I', TABLE_BLOCK_SIZE))       # capacity
        f.write(struct.pack('<I', num_entries))             # total file count

        # Pad to DATA_START (512)
        f.write(b'\x00' * (DATA_START - 28))

        # First data entry (before file table, like official layout)
        f.write(ENTRY_HEADER)
        f.write(file_records[0]['file_data'])

        # File table blocks
        idx = 0
        for block_num in range(num_table_blocks):
            block_start = idx
            block_end = min(idx + TABLE_BLOCK_SIZE, num_entries)
            block_entries = file_records[block_start:block_end]

            if block_num < num_table_blocks - 1:
                next_block_offset = first_table_offset + (block_num + 1) * TABLE_BLOCK_BYTES
            else:
                next_block_offset = 0

            # Block header — write capacity as entry count (matches official format)
            f.write(struct.pack('<I', TABLE_BLOCK_SIZE))
            f.write(struct.pack('<q', next_block_offset))

            # Entries (34 bytes each)
            for rec in block_entries:
                file_hash = (rec['sh'] << 32) | rec['ph']
                adler = zlib.adler32(rec['file_data']) & 0xFFFFFFFF if rec['file_data'] else 0
                f.write(struct.pack('<q', rec['offset']))          # data offset
                f.write(struct.pack('<I', ENTRY_HEADER_LEN))       # header length
                f.write(struct.pack('<I', rec['data_size']))       # compressed size
                f.write(struct.pack('<I', rec['data_size']))       # decompressed size
                f.write(struct.pack('<Q', file_hash))              # file hash
                f.write(struct.pack('<I', adler))                  # adler32
                f.write(struct.pack('<h', 0))                      # compression flag

            # Pad remaining slots in block with zeroes
            remaining = TABLE_BLOCK_SIZE - len(block_entries)
            f.write(b'\x00' * (remaining * TABLE_ENTRY_SIZE))

            idx = block_end

        # Remaining data entries (after file table)
        for rec in file_records[1:]:
            f.write(ENTRY_HEADER)
            f.write(rec['file_data'])

    log(f"  Built {pack_name}: {out_path.stat().st_size / 1024 / 1024:.1f} MB, {num_entries} files")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_archives(output_dir, pack_entries):
    """Verify built .uop archives against manifest entries.

    Returns list of entries that failed verification (missing or size mismatch).
    """
    output_dir = Path(output_dir)
    packs = {}
    for e in pack_entries:
        packs.setdefault(e['pack_name'], []).append(e)

    total_ok = 0
    failed_entries = []

    for pack_name, entries in packs.items():
        uop_path = output_dir / pack_name
        if not uop_path.exists():
            log(f"  VERIFY FAIL: {pack_name} not found")
            failed_entries.extend(entries)
            continue

        index = read_uop_index(str(uop_path))

        for entry in entries:
            ph = int(entry['ph'], 16)
            sh = int(entry['sh'], 16)
            ul = entry['ul']
            if (ph, sh) not in index or index[(ph, sh)] != ul:
                failed_entries.append(entry)
            else:
                total_ok += 1

    if failed_entries:
        by_pack = {}
        for e in failed_entries:
            by_pack.setdefault(e['pack_name'], []).append(e)
        for pname, ents in by_pack.items():
            log(f"  VERIFY {pname}: {ents[0]['ph']}{ents[0]['sh']}... ({len(ents)} entries failed)")
    log(f"  Pack verification: {total_ok} ok, {len(failed_entries)} failed")
    return failed_entries


def verify_unpacked(output_dir, unpacked_entries):
    """Verify unpacked files. Returns list of entries that need re-download."""
    output_dir = Path(output_dir)
    failed = []
    ok = 0

    for entry in unpacked_entries:
        path = output_dir / entry['name']
        ul = entry['ul']
        if not path.exists() or path.stat().st_size != ul:
            failed.append(entry)
        else:
            ok += 1

    log(f"  Unpacked verification: {ok} ok, {len(failed)} failed")
    return failed


# ---------------------------------------------------------------------------
# Main patcher logic
# ---------------------------------------------------------------------------

DEFAULT_PROD_URL = "http://patch.uo.eamythic.com/uopatch-sa/legacyrelease/uo/manifest/uo-legacyrelease.prod"
MAX_RETRIES = 3


def run_patch(output_dir, prod_url=None, workers=8, dry_run=False, no_verify=False, retries=MAX_RETRIES):
    """Main entry point: fetch manifests, download all game files, verify, and retry failures."""
    prod_url = prod_url or DEFAULT_PROD_URL
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch product file
    prod = parse_prod(prod_url)
    manifest_repo = prod['manifest_repos'][0]
    file_repo = prod['file_repos'][0]

    log(f"[*] Manifest repo: {manifest_repo}")
    log(f"[*] File repo:     {file_repo}")
    log(f"[*] Launch file:   {prod['launchfile']}")
    log(f"[*] Stages:        {[s['name'] for s in prod['stages']]}")

    # 2. Collect all files from all stages
    all_unpacked = []
    all_packs = []

    for stage in prod['stages']:
        log(f"\n[*] Processing stage: {stage['name']}")
        for pkg in stage['packages']:
            log(f"  Package: {pkg['name']} (rpath={pkg['rpath']})")
            unpacked, packs = collect_files(manifest_repo, pkg['rpath'], pkg['manifest_name'])
            all_unpacked.extend(unpacked)
            all_packs.extend(packs)

    # Deduplicate pack files by (pack_name, ph, sh)
    seen_pack = set()
    unique_packs = []
    for e in all_packs:
        key = (e['pack_name'], e['ph'], e['sh'])
        if key not in seen_pack:
            seen_pack.add(key)
            unique_packs.append(e)
    all_packs = unique_packs

    total_unpacked_bytes = sum(e['cl'] if e['ct'] == 1 else e['ul'] for e in all_unpacked)
    total_pack_bytes = sum(e['cl'] if e['ct'] == 1 else e['ul'] for e in all_packs)

    log(f"\n{'='*60}")
    log(f"[*] Total unpacked files: {len(all_unpacked)}")
    log(f"[*] Total pack entries:   {len(all_packs)}")
    log(f"[*] Total download size:  {(total_unpacked_bytes + total_pack_bytes)/1024/1024:.1f} MB")
    log(f"{'='*60}")

    if dry_run:
        log("\n[DRY RUN] Would download the above files. Exiting.")
        return

    # 3. Download unpacked files with retry loop
    log(f"\n[*] Downloading {len(all_unpacked)} unpacked files...")
    failed_unpacked = download_unpacked_parallel(file_repo, all_unpacked, output_dir, workers)

    if not no_verify:
        for attempt in range(1, retries + 1):
            if not failed_unpacked:
                break
            time.sleep(2)
            log(f"\n[*] Retry {attempt}/{retries}: re-downloading {len(failed_unpacked)} unpacked files...")
            failed_unpacked = download_unpacked_parallel(file_repo, failed_unpacked, output_dir, workers)

    # 4. Filter pack entries: skip packs whose .uop was already downloaded as a valid EA archive
    official_uops = set()
    for e in all_unpacked:
        if e['name'].endswith('.uop'):
            p = output_dir / e['name']
            if p.exists() and p.stat().st_size == e['ul'] and is_official_uop(str(p)):
                official_uops.add(e['name'])

    if official_uops:
        before = len(all_packs)
        all_packs = [e for e in all_packs if e['pack_name'] not in official_uops]
        if all_packs:
            log(f"\n[*] Skipped {before - len(all_packs)} pack entries ({len(official_uops)} valid EA archives)")
        else:
            log(f"\n[*] All {len(official_uops)} pack archives already present as valid EA downloads")

    # 5. Download remaining pack files with retry loop
    if all_packs:
        pack_names = set(e['pack_name'] for e in all_packs)
        log(f"\n[*] Scanning {len(pack_names)} .uop archives for incremental update...")
        pack_index = build_pack_index(output_dir, pack_names)
        if pack_index:
            log(f"  Found {len(pack_index)} existing entries in local archives")

        log(f"[*] Downloading {len(all_packs)} pack entries ({workers} threads)...")
        failed_packs = download_packs_parallel(file_repo, all_packs, output_dir, workers, pack_index)

        # Retry failed pack downloads
        for attempt in range(1, retries + 1):
            if not failed_packs:
                break
            time.sleep(2)
            log(f"\n[*] Retry {attempt}/{retries}: re-downloading {len(failed_packs)} pack entries...")
            failed_packs = download_packs_parallel(file_repo, failed_packs, output_dir, workers)

        # 6. Build MYP archives (only for packs without valid EA unpacked downloads)
        staging_dir = Path(output_dir) / '.pack_staging'
        if staging_dir.exists():
            updated_packs = set(d.name for d in staging_dir.iterdir() if d.is_dir())
        else:
            updated_packs = set()

        # Don't rebuild archives that exist as valid EA downloads
        for pack_name in list(updated_packs):
            if pack_name in official_uops:
                updated_packs.discard(pack_name)
                # Clean staging for this pack
                pack_staging = staging_dir / pack_name
                if pack_staging.exists():
                    shutil.rmtree(pack_staging, ignore_errors=True)

        if updated_packs:
            log(f"\n[*] Building {len(updated_packs)} MYP archives...")
            pack_groups = {}
            for e in all_packs:
                if e['pack_name'] in updated_packs:
                    pack_groups.setdefault(e['pack_name'], []).append(e)

            for pack_name, entries in pack_groups.items():
                if pack_name not in official_uops:
                    log(f"  WARNING: {pack_name} has no EA unpacked download, building from chunks")
                build_myp_archive(output_dir, pack_name, entries)

            shutil.rmtree(staging_dir, ignore_errors=True)
        else:
            log(f"\n[*] All pack archives up-to-date, no rebuild needed")
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        # 6. Verify archives and retry if needed
        if not no_verify:
            log(f"\n[*] Verifying pack archives...")
            failed_verify = verify_archives(output_dir, all_packs)

            for attempt in range(1, retries + 1):
                if not failed_verify:
                    break
                time.sleep(2)
                log(f"\n[*] Verify retry {attempt}/{retries}: re-downloading {len(failed_verify)} entries...")
                # Re-download only the failed entries
                still_failed = download_packs_parallel(file_repo, failed_verify, output_dir, workers)
                if still_failed:
                    log(f"  {len(still_failed)} entries still failing after download retry")

                # Rebuild only the affected archives
                affected_packs = set(e['pack_name'] for e in failed_verify)
                for pack_name in affected_packs:
                    pack_entries_for_rebuild = [e for e in all_packs if e['pack_name'] == pack_name]
                    build_myp_archive(output_dir, pack_name, pack_entries_for_rebuild)

                # Clean staging for affected packs
                staging_dir = Path(output_dir) / '.pack_staging'
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)

                # Re-verify only affected
                affected_entries = [e for e in all_packs if e['pack_name'] in affected_packs]
                failed_verify = verify_archives(output_dir, affected_entries)

            if failed_verify:
                log(f"\n{'='*60}")
                log(f"  ERROR: {len(failed_verify)} entries failed after all retries:")
                for e in failed_verify[:20]:
                    log(f"    {e['pack_name']}/{e['ph']}{e['sh']}")
                if len(failed_verify) > 20:
                    log(f"    ... and {len(failed_verify) - 20} more")
                log(f"{'='*60}")
                sys.exit(1)

    # 7. Verify unpacked files
    if not no_verify and all_unpacked:
        log(f"\n[*] Verifying unpacked files...")
        still_failed = verify_unpacked(output_dir, all_unpacked)
        if still_failed:
            # Filter out known CDN-missing files (notes/ stage)
            real_failures = [e for e in still_failed if not e['name'].startswith('notes/')]
            if real_failures:
                log(f"  WARNING: {len(real_failures)} unpacked files failed verification")
                for e in real_failures[:10]:
                    log(f"    {e['name']}")
            if still_failed and not real_failures:
                log(f"  ({len(still_failed)} missing files are all from 'notes/' stage - non-critical)")

    log(f"\n[*] Patch complete! Files written to: {output_dir}")


# ---------------------------------------------------------------------------
# Also download the patcher/client itself
# ---------------------------------------------------------------------------

PATCHER_PROD_URL = "http://patch.uo.eamythic.com/uopatch-sa/legacyrelease/patcher/manifest/patcher.prod"


def run_patcher_download(output_dir, workers=4, retries=MAX_RETRIES):
    """Download the patcher/launcher files (UO.exe, etc.)."""
    prod = parse_prod(PATCHER_PROD_URL)
    manifest_repo = prod['manifest_repos'][0]
    file_repo = prod['file_repos'][0]

    log(f"[*] Patcher manifest repo: {manifest_repo}")
    log(f"[*] Patcher file repo:     {file_repo}")

    all_unpacked = []
    all_packs = []

    for stage in prod['stages']:
        for pkg in stage['packages']:
            unpacked, packs = collect_files(manifest_repo, pkg['rpath'], pkg['manifest_name'])
            all_unpacked.extend(unpacked)
            all_packs.extend(packs)

    log(f"[*] Patcher files: {len(all_unpacked)} unpacked, {len(all_packs)} packed")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if all_unpacked:
        failed = download_unpacked_parallel(file_repo, all_unpacked, output_dir, workers)
        for attempt in range(1, retries + 1):
            if not failed:
                break
            time.sleep(2)
            log(f"  Patcher retry {attempt}/{retries}: {len(failed)} files...")
            failed = download_unpacked_parallel(file_repo, failed, output_dir, workers)

    if all_packs:
        # Filter out packs whose .uop was already downloaded as valid EA archive
        official_uops = set()
        for e in all_unpacked:
            if e['name'].endswith('.uop') or e['name'].endswith('.myp'):
                p = output_dir / e['name']
                if p.exists() and p.stat().st_size == e['ul'] and is_official_uop(str(p)):
                    official_uops.add(e['name'])
        if official_uops:
            all_packs = [e for e in all_packs if e['pack_name'] not in official_uops]

        if all_packs:
            failed = download_packs_parallel(file_repo, all_packs, output_dir, workers)
            for attempt in range(1, retries + 1):
                if not failed:
                    break
                time.sleep(2)
                failed = download_packs_parallel(file_repo, failed, output_dir, workers)

        # Build archives (only non-official)
        staging_dir = output_dir / '.pack_staging'
        if staging_dir.exists():
            pack_groups = {}
            for e in all_packs:
                if e['pack_name'] not in official_uops:
                    pack_groups.setdefault(e['pack_name'], []).append(e)
            for pack_name, entries in pack_groups.items():
                build_myp_archive(output_dir, pack_name, entries)
            shutil.rmtree(staging_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="UO Classic Client Patcher - Download Ultima Online game files from EA patch servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --output ~/UO                    # Download full game to ~/UO
  %(prog)s --output ~/UO --dry-run           # Show what would be downloaded
  %(prog)s --output ~/UO --workers 16        # Download with 16 threads
  %(prog)s --output ~/UO --patcher-only      # Only download the patcher/launcher
  %(prog)s --output ~/UO --no-verify         # Skip post-download verification
        """)
    parser.add_argument('-o', '--output', default='./UO_Client',
                        help='Output directory (default: ./UO_Client)')
    parser.add_argument('-w', '--workers', type=int, default=8,
                        help='Number of parallel download threads (default: 8)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Only show what would be downloaded')
    parser.add_argument('--patcher-only', action='store_true',
                        help='Only download patcher/launcher files')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip post-download verification and retry')
    parser.add_argument('--retries', type=int, default=MAX_RETRIES,
                        help=f'Max retry attempts for failed downloads (default: {MAX_RETRIES})')
    parser.add_argument('--prod-url',
                        help='Override product manifest URL')
    args = parser.parse_args()

    log("=" * 60)
    log("  UO Classic Client Patcher")
    log("  Downloading from EA/Broadsword patch servers")
    log("=" * 60)

    if args.patcher_only:
        run_patcher_download(args.output, args.workers, args.retries)
    else:
        log("\n[Phase 1] Downloading patcher/launcher...")
        run_patcher_download(args.output, args.workers, args.retries)

        log("\n[Phase 2] Downloading game files...")
        run_patch(args.output, args.prod_url, args.workers, args.dry_run,
                  no_verify=args.no_verify, retries=args.retries)


if __name__ == '__main__':
    main()
