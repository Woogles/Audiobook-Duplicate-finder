# Project Guidance

- Keep all scan and matching operations local; do not add network calls or upload audio data.
- Scanning must not modify source audio. Only approved duplicate editions may be moved, and all moves must be reversible from the transaction log.
- Keep automatic matching conservative. Ambiguous groups belong in the review queue.
- Run `python -m unittest discover -s tests -v` after changing matching, scanning, or move behavior.
- Keep optional audio fingerprinting opt-in and functional when `pyacoustid` or `fpcalc` is unavailable.