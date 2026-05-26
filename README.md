# UO Patcher

A Python tool to download and update the Ultima Online Classic Client directly from EA/Broadsword's public patch servers. No dependencies beyond Python 3.7+ stdlib.

## Notice

The Ultima Online Classic Client is freely distributed by Electronic Arts / Broadsword Online Games. No paid subscription is required to download the client — only to connect to official game servers. This tool downloads the same freely available client files from the same public, unauthenticated HTTP servers that the official installer uses. No copy protection, authentication, or encryption is bypassed. This tool is intended for use with legitimate UO accounts and community-run shards such as [ServUO](https://github.com/ServUO/ServUO).

## Installation

```bash
git clone https://github.com/andrezaiats/uo-patcher.git
cd uo-patcher
```

No `pip install` needed — the script uses only Python standard library modules (`urllib`, `zlib`, `xml.etree`, `concurrent.futures`, `struct`, `threading`, `shutil`).

**Requirements:** Python 3.7+

## Usage

### Fresh Download

Download the full UO Classic Client (~1.6 GB compressed):

```bash
python3 uo_patcher.py --output ~/UO_Client
```

### Update an Existing Install

Point the patcher at an existing UO directory. It scans local files and `.uop` archives, then only downloads what changed:

```bash
python3 uo_patcher.py --output ~/UO_Client
```

Example output on an install that's already up-to-date:

```
Scanning 10 .uop archives...
Found 43669 entries in local archives
Pack entries: 43669/43669 up-to-date, 0 need download
Download needed: 0.0 MB
```

### Other Options

```bash
# Preview what would be downloaded without downloading anything
python3 uo_patcher.py --output ~/UO_Client --dry-run

# Download with 16 parallel threads (default: 8)
python3 uo_patcher.py --output ~/UO_Client --workers 16

# Only download the patcher/launcher files (UO.exe, etc.)
python3 uo_patcher.py --output ~/UO_Client --patcher-only

# Skip post-download verification
python3 uo_patcher.py --output ~/UO_Client --no-verify

# Set max retry attempts for failed downloads
python3 uo_patcher.py --output ~/UO_Client --retries 5
```

### All Options

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | `./UO_Client` | Output directory |
| `-w`, `--workers` | `8` | Number of parallel download threads |
| `--dry-run` | off | Show what would be downloaded, then exit |
| `--patcher-only` | off | Only download the patcher/launcher files |
| `--no-verify` | off | Skip post-download verification and retry |
| `--retries` | `3` | Max retry attempts for failed downloads |
| `--prod-url` | *(auto)* | Override the product manifest URL |

## Download Phases

A full run (without `--patcher-only`) executes two phases:

1. **Phase 1 — Patcher/Launcher**: Downloads the UO client executable and launcher files (UO.exe, etc.) from the patcher manifest.
2. **Phase 2 — Game Files**: Downloads all game data (art, maps, music, configs, etc.) from the main game manifest.

With `--patcher-only`, only Phase 1 runs.

## How Incremental Updates Work

| File Type | Skip Method | Details |
|-----------|-------------|---------|
| **Unpacked files** | File size check | Skipped if local file size matches the manifest's decompressed length |
| **Pack entries (.uop)** | UOP file table scan | Reads the file table from existing `.uop` archives and compares entry hashes and sizes against the remote manifest |

Scanning `.uop` archives reads only the file table blocks (a few KB per archive), not the data itself — a full scan of ~43K entries takes under a second.

## Verification and Self-Healing

By default, after downloading, the patcher verifies all files and automatically retries any failures (up to 3 attempts, configurable with `--retries`):

- **Unpacked files**: verified by checking file existence and size against the manifest.
- **Pack archives (.uop)**: verified by reading the file table and comparing every entry's hash and decompressed size against the manifest.
- **Self-healing**: If a `.uop` archive exists but lacks the official EA signature (e.g., it was built by an older version of this tool), it is automatically re-downloaded from the CDN rather than skipped.

If verification still fails after all retries, the patcher exits with a non-zero status and prints the failing entries. Use `--no-verify` to skip verification entirely.

## What Gets Downloaded

As of March 2026:

| Category | Count | Download Size |
|----------|-------|---------------|
| Unpacked files (configs, music, defs, .mul) | 480 | ~1,507 MB |
| Pack entries (art, maps in .uop archives) | 43,669 | ~136 MB |
| **Total** | **44,149** | **~1,643 MB** |

### Archive Files

| Archive | Description |
|---------|-------------|
| `artLegacyMUL.uop` | Art tiles, item graphics (~37K entries) |
| `map0LegacyMUL.uop` | Felucca map |
| `map1LegacyMUL.uop` | Trammel map |
| `map2LegacyMUL.uop` | Ilshenar map |
| `map3LegacyMUL.uop` | Malas map |
| `map4LegacyMUL.uop` | Tokuno map |
| `map5LegacyMUL.uop` | Ter Mur map |
| `map*xLegacyMUL.uop` | Extended map data |

### Unpacked File Types

| Type | Examples |
|------|----------|
| `.mul` / `.idx` | `anim.mul`, `staidx0.mul` — legacy UO data files |
| `.def` | `Body.def`, `Hues.def` — definition/mapping files |
| `.mp3` | `Music/Digital/*.mp3` — background music (~150 tracks) |
| `.cfg` | `Uo.cfg`, `Uoreg.cfg` — client configuration |
| `.enu` / `.deu` / `.jpn` / `.fra` | Localization files |
| `.dat` | `Credits.dat`, `tiledata.mul` — game data tables |

## Limitations

- No RSA signature verification (signatures are present in manifests but not checked)
- MYP archives built from pack chunks use 137-byte metadata headers with zeroed signature content (matches the official header size; ServUO only uses the header length as a skip offset, so the zeroed content is safe)
- When pack entries need updating, the affected `.uop` archive is rebuilt from all its chunks (no in-place patching of individual entries within existing archives)
- No resume for partially downloaded files (complete files with correct size are skipped)
- Files from the `notes/` manifest stage are often missing on the CDN (404) — these are non-critical and are silently ignored during verification

## Protocol Reference

The sections below document the EA Mythic Patcher v6 protocol used by the official UO Classic Client installer. This may be useful for anyone building compatible tools.

### Server Endpoints

| Role | URL |
|------|-----|
| **Manifest repo** | `http://patch.uo.broadsword.com/uopatch-sa/legacyrelease/` |
| **File CDN** | `http://patch.ak-cdn.eamythic.com/uopatch-sa/240/legacyrelease/uo/files/` |
| **Patcher files** | `http://patch.uo.broadsword.com/uopatch-sa/legacyrelease/patcher/files/` |

The `240` in the CDN path is a version/generation number embedded in the product file and may change with major updates.

### Manifest Hierarchy

```
uo-legacyrelease.prod          Product file (plain XML)
  └── base/pkg.mft             Package manifest (zlib-compressed XML)
        ├── unpacked.mft        Loose file list (480 files)
        ├── artLegacyMUL.uop.mft
        │     ├── artLegacyMUL.uop_0.mft   Pack entries (per-chunk)
        │     ├── artLegacyMUL.uop_1.mft
        │     └── ...
        ├── map0LegacyMUL.uop.mft
        └── ...
```

All `.mft` files are zlib-compressed XML wrapped in `<MythicMFT>` root elements.

### Download URL Schemes

**Unpacked files:**
```
{filerepo}/base/unpacked/{hashlittle2(lowercase(filename))}
```

The filename is lowercased, hashed with Jenkins `hashlittle2` (initval=0), and formatted as 16 hex characters:

```
hashlittle2("body.def") -> (0x00dc4cf4, 0x4fb46067)
URL: .../base/unpacked/00dc4cf44fb46067
```

**Pack entries (.uop):**
```
{filerepo}/base/{pack_rpath}/{ph:08x}{sh:08x}
```

Where `ph` and `sh` come directly from the manifest attributes.

### MYP Archive Format (.uop)

Based on the format documented by [UOFiddler](https://github.com/polserver/UOFiddler) and [Mythic-Package-Editor](https://github.com/pincoide/Mythic-Package-Editor-EC-).

**Header (28 bytes):**

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0 | 4 | char[4] | Magic: `MYP\0` |
| 4 | 4 | uint32 | Version (5) |
| 8 | 4 | uint32 | Signature/timestamp |
| 12 | 8 | int64 | Offset to first file table block |
| 20 | 4 | uint32 | File table block capacity (default: 1000) |
| 24 | 4 | uint32 | Total file count |

**File table block header (12 bytes):**

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0 | 4 | int32 | Entry count in this block |
| 4 | 8 | int64 | Offset to next block (0 = last) |

**File table entry (34 bytes):**

| Offset | Size | Type | Description |
|--------|------|------|-------------|
| 0 | 8 | int64 | Data offset in archive |
| 8 | 4 | int32 | Header/metadata length (137 bytes for official EA files) |
| 12 | 4 | uint32 | Compressed size |
| 16 | 4 | uint32 | Decompressed size |
| 20 | 8 | uint64 | File hash (Jenkins hashlittle2) |
| 28 | 4 | uint32 | Adler32 data checksum |
| 32 | 2 | int16 | Compression flag (0=none, 1=zlib) |

**Hash byte order:** On disk, the hash stores `(sh << 32) | ph`, reversed from the manifest's `ph:sh` naming. The official patcher decompresses data when writing to the archive, so on-disk entries typically have `compression_flag=0` with `compressed_size == decompressed_size`.

**Entry metadata header:** Each data entry on disk is preceded by a metadata header whose length is stored in the file table's `hdr_len` field. Official EA files use 137-byte headers: `type(2)=4 + data_len(2)=133 + signature(133)`. ServUO reads `hdr_len` to skip past the header (`offset + headerLength`) but never inspects its content, so zeroed signature bytes are safe.

## License

[MIT](LICENSE)
