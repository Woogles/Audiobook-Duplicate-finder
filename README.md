# Audiobook Duplicate Finder

A Windows-first desktop tool for scanning audiobook libraries locally, finding likely duplicate editions, moving only high-confidence duplicates into a review area, and reviewing uncertain matches before moving anything.

## Run

On Windows, double-click `Install Audiobook Duplicate Finder.bat` for a guided setup. It validates the project and optional library folder, finds Python 3.11 or newer or offers a per-user Python install through winget, creates `.venv`, installs the application dependencies, runs the test suite, saves the selected library as the app default, and can launch the app. You can also provide the library path directly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -LibraryPath "D:\Audiobooks"
```

To check Python and validate a path without installing or changing settings, use `-ValidateOnly`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -ValidateOnly -LibraryPath "D:\Audiobooks"
```

The installer works without administrator access: Python is installed per-user if needed, and packages go into the project virtual environment. It never elevates the terminal or changes the system execution policy. Installing app dependencies does not require the optional `fpcalc` utility; without it, fingerprint matching remains disabled and ordinary scanning still works.

For a manual install, use Python 3.11 or newer. From this folder, create and activate a virtual environment, then install the app:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m audiobook_finder.app
```

To run the test suite, install the test extra and run `python -m unittest discover -s tests -v`.

Audio fingerprinting is optional and off by default. Enable the checkbox in the app after making the local Chromaprint `fpcalc` executable available on `PATH`; alternatively, set the `FPCALC` environment variable to its full path. This pass reads the audio again and can take a long time for a multi-terabyte library. Nothing is sent over the network.

When fingerprinting is checked, the app verifies that `fpcalc` can be launched before scanning. If it is unavailable, the app shows Windows setup instructions and lets you continue without fingerprinting. Scan activity is shown while folders are enumerated and files are inspected. The application log is written to `%LOCALAPPDATA%\AudiobookDuplicateFinder\audiobook-finder.log` with up to three rotated backups; move and undo operations also retain their per-library reversible transaction log.

Mounted SMB and NFS folders can be selected through a mapped drive or a UNC path, provided Windows can read the share. Remote scans can be slow, especially during folder enumeration or hashing, and the activity indicator remains active during that time. The share must remain mounted and writable for review moves and undo; SMB/NFS server permissions and rename behavior apply. Unavailable or unreadable folders and files are recorded as scan issues and in the application log. The app does not connect to shares itself or transfer audio elsewhere.

## Review Flow

The scan reads supported audio files recursively and does not modify them. It records SHA-256 hashes, available tags, duration, bitrate, lossless format, chapter information, and cover presence. The `_Audiobook Review` folder is excluded from later scans.

Chapter files with shared album tags are grouped into editions; otherwise files in a book folder are treated as one edition. Immediate `CD 1`, `Disc 2`, `Disk 1`, and `Part 2` directories are combined with their parent. When files are stored directly in the selected library root, each untagged file remains its own edition.

Strong groups are processed only after the full scan completes. Identical file-hash sets are strong matches. Metadata-based automatic matches require very similar titles, matching authors, and durations within 3%. When enabled, high fingerprint agreement can also establish a strong match if edition durations are within 20%. The best-ranked edition stays in place; other files are moved to `_Audiobook Review/<book title>/<source folder>/`. Weaker candidates stay in the review queue for an explicit keep-and-move or keep-all decision.

The preferred-edition ranking is lossless format, higher average bitrate, longer matching duration, chapter data, multiple files, richer metadata, then embedded cover art. These are ranking signals, not a guarantee that the chosen recording is the best artistic or mastering version.

Moves are logged in `_Audiobook Review/_move-log.jsonl` and can be reversed with **Undo last move**. Undo restores only paths that are free; it never overwrites an existing file. The app does not delete audio files.

## Formats

The scanner recognizes AAC, AAX, AIFF, APE, FLAC, M4A, M4B, M4P, MKA, MKV, MP3, MPC, OGA, OGG, Opus, WAV, WMA, and WV extensions. Metadata or duration may be unavailable for encrypted or unsupported media; those files are still hashed where possible and reported if inspection fails.