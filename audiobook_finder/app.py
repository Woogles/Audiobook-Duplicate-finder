"""PySide6 desktop interface for local audiobook duplicate review."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .matching import Confidence, Edition, preference_key
from .operations import move_edition, undo_last_move
from .scanner import _fpcalc_available
from .settings import load_library_path, save_library_path, settings_path, validate_library_path
from .workflow import WorkflowResult, scan_and_process


LOGGER = logging.getLogger(__name__)


def _configure_logging() -> Path:
    log_path = settings_path().parent / "audiobook-finder.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if not any(getattr(handler, "_audiobook_finder_log", False) for handler in root_logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler._audiobook_finder_log = True
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    return log_path


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes}:{seconds_part:02d}"


def _edition_summary(edition: Edition) -> str:
    author = ", ".join(edition.authors) if edition.authors else "Author not tagged"
    location = Path(edition.paths[0]).parent if edition.paths else Path("Unknown location")
    quality = "Lossless" if edition.lossless else (
        f"{edition.average_bitrate // 1000} kbps" if edition.average_bitrate else "Bitrate unknown"
    )
    structure = f"{edition.file_count} files"
    if edition.chapter_count:
        structure += f", {edition.chapter_count} chapters"
    return (
        f"{edition.title}\n{author}\n{_duration(edition.duration_seconds)} · {structure} · "
        f"{quality}\n{location}"
    )


class ScanWorker(QObject):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, cancellation: threading.Event, include_fingerprints: bool) -> None:
        super().__init__()
        self.root = root
        self.cancellation = cancellation
        self.include_fingerprints = include_fingerprints

    def run(self) -> None:
        try:
            result = scan_and_process(
                self.root,
                on_progress=lambda count, path: self.progress.emit(count, path),
                should_cancel=self.cancellation.is_set,
                include_fingerprints=self.include_fingerprints,
            )
            self.completed.emit(result)
        except Exception as error:
            LOGGER.exception("Scan failed for %s", self.root)
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Audiobook Duplicate Finder")
        self.setMinimumSize(940, 650)
        self.resize(1120, 760)
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._cancellation: threading.Event | None = None
        self._result: WorkflowResult | None = None
        self._review_groups = []
        self._include_fingerprints = False

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        heading = QLabel("Audiobook Duplicate Finder")
        heading.setObjectName("heading")
        heading.setFont(QFont("Segoe UI Variable Display", 21, QFont.Weight.DemiBold))
        layout.addWidget(heading)

        source_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setText(load_library_path())
        self.path_edit.setPlaceholderText("Select your audiobook library folder")
        self.browse_button = QPushButton("Browse")
        self.browse_button.clicked.connect(self._choose_folder)
        source_row.addWidget(self.path_edit, 1)
        source_row.addWidget(self.browse_button)
        layout.addLayout(source_row)

        actions = QHBoxLayout()
        self.scan_button = QPushButton("Scan library")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self._start_scan)
        self.fingerprint_checkbox = QCheckBox("Audio fingerprints (slower)")
        self.fingerprint_checkbox.setToolTip(
            "Use local Chromaprint via fpcalc to compare audio content across different names and encodings."
        )
        self.cancel_button = QPushButton("Cancel scan")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_scan)
        self.undo_button = QPushButton("Undo last move")
        self.undo_button.clicked.connect(self._undo_move)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.fingerprint_checkbox)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        actions.addWidget(self.undo_button)
        layout.addLayout(actions)

        self.status = QLabel("Choose a library folder to begin.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("scanProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider)

        review_heading = QHBoxLayout()
        self.review_title = QLabel("Review queue")
        self.review_title.setObjectName("sectionTitle")
        self.queue_count = QLabel("0 remaining")
        self.queue_count.setObjectName("muted")
        review_heading.addWidget(self.review_title)
        review_heading.addStretch(1)
        review_heading.addWidget(self.queue_count)
        layout.addLayout(review_heading)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.group_list = QListWidget()
        self.group_list.setMinimumWidth(230)
        self.group_list.currentRowChanged.connect(self._show_group)
        splitter.addWidget(self.group_list)

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(18, 0, 0, 0)
        self.group_detail = QLabel("No uncertain matches to review.")
        self.group_detail.setObjectName("groupDetail")
        self.group_detail.setWordWrap(True)
        detail_layout.addWidget(self.group_detail)

        self.edition_list = QListWidget()
        self.edition_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.edition_list.setMinimumHeight(225)
        detail_layout.addWidget(self.edition_list, 1)

        review_actions = QHBoxLayout()
        self.move_button = QPushButton("Keep selected, move others")
        self.move_button.setObjectName("primary")
        self.move_button.clicked.connect(self._move_review_copies)
        self.keep_button = QPushButton("Keep all copies")
        self.keep_button.clicked.connect(self._keep_review_group)
        review_actions.addWidget(self.move_button)
        review_actions.addWidget(self.keep_button)
        detail_layout.addLayout(review_actions)
        splitter.addWidget(details)
        splitter.setSizes([300, 700])
        layout.addWidget(splitter, 1)

        self.setCentralWidget(content)
        self._apply_style()
        self._set_review_enabled(False)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7f5; color: #202a28; }
            QLabel#heading { color: #163b34; }
            QLabel#sectionTitle { color: #163b34; font-size: 15px; font-weight: 650; }
            QLabel#status { background: #e9efec; border-left: 3px solid #267866; padding: 10px 12px; }
            QProgressBar#scanProgress { background: #dce5e0; border: 0; border-radius: 2px; }
            QProgressBar#scanProgress::chunk { background: #267866; border-radius: 2px; }
            QLabel#muted { color: #63726d; }
            QLabel#groupDetail { color: #364541; padding-bottom: 8px; }
            QLineEdit, QListWidget { background: #ffffff; border: 1px solid #cbd5d0; border-radius: 4px; padding: 8px; }
            QListWidget::item { padding: 9px; border-bottom: 1px solid #edf0ee; }
            QListWidget::item:selected { background: #d9eee7; color: #163b34; }
            QPushButton { background: #ffffff; border: 1px solid #b9c8c1; border-radius: 4px; padding: 9px 14px; }
            QPushButton:hover { background: #edf4f1; }
            QPushButton:disabled { color: #8a9691; background: #edf0ee; }
            QPushButton#primary { background: #176b58; color: #ffffff; border-color: #176b58; font-weight: 600; }
            QPushButton#primary:hover { background: #105443; }
            QFrame#divider { color: #d7dfdb; }
            QSplitter::handle { background: #e0e7e3; width: 1px; }
        """)

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select audiobook library")
        if chosen:
            self.path_edit.setText(chosen)

    def _start_scan(self) -> None:
        selected = self.path_edit.text().strip()
        try:
            library_path = validate_library_path(selected)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Library folder unavailable", str(error))
            return
        self.path_edit.setText(str(library_path))
        try:
            save_library_path(library_path)
        except OSError as error:
            QMessageBox.warning(self, "Could not save default folder", str(error))
        self._include_fingerprints = self.fingerprint_checkbox.isChecked()
        if self._include_fingerprints and not _fpcalc_available():
            answer = QMessageBox.warning(
                self,
                "fpcalc is not available",
                "Audio fingerprinting is checked, but fpcalc could not be launched.\n\n"
                "To enable it, install the Windows Chromaprint fpcalc utility from "
                "https://acoustid.org/chromaprint, add the folder containing fpcalc.exe "
                "to PATH, or set FPCALC to the full executable path, then restart the app.\n\n"
                "Continue this scan without audio fingerprinting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                LOGGER.info("Scan cancelled before start because fpcalc is unavailable")
                return
            self.fingerprint_checkbox.setChecked(False)
            self._include_fingerprints = False
        self.group_list.clear()
        self.edition_list.clear()
        self.group_detail.setText("Scanning files and comparing audiobook editions…")
        self.status.setText("Scanning library: discovering files and checking metadata…")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        LOGGER.info(
            "Scan started for %s (fingerprinting=%s)",
            library_path,
            self._include_fingerprints,
        )
        self._set_running(True)
        self._cancellation = threading.Event()
        self._thread = QThread(self)
        self._worker = ScanWorker(
            library_path, self._cancellation, self._include_fingerprints
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._scan_progress)
        self._worker.completed.connect(self._scan_completed)
        self._worker.failed.connect(self._scan_failed)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def _scan_progress(self, count: int, path: str) -> None:
        self.status.setText(f"Scanned {count:,} files · {path}")

    def _scan_completed(self, result: WorkflowResult) -> None:
        self._result = result
        self._review_groups = list(result.review_groups)
        moved_files = sum(len(mappings) for _, mappings in result.moved)
        moved_editions = len(result.moved)
        if result.cancelled:
            message = "Scan cancelled. No automatic moves were made."
            LOGGER.info("Scan cancelled for %s", self.path_edit.text())
        else:
            message = (
                f"Scan complete · {result.edition_count:,} editions · "
                f"moved {moved_files:,} files from {moved_editions:,} strong duplicate editions · "
                f"{len(result.review_groups):,} groups need review · {len(result.issues):,} unreadable files"
            )
            LOGGER.info(
                "Scan completed for %s: editions=%d groups_for_review=%d moved_editions=%d "
                "moved_files=%d issues=%d",
                self.path_edit.text(), result.edition_count, len(result.review_groups),
                moved_editions, moved_files, len(result.issues),
            )
        self.status.setText(message)
        self._populate_review_queue()

    def _scan_failed(self, message: str) -> None:
        self.status.setText("Scan stopped because an error occurred.")
        QMessageBox.critical(self, "Scan failed", message)

    def _thread_finished(self) -> None:
        self._set_running(False)
        self.progress_bar.hide()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._worker = None
        self._thread = None

    def _cancel_scan(self) -> None:
        if self._cancellation:
            self._cancellation.set()
            self.status.setText("Cancellation requested; finishing the current file…")

    def _set_running(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.path_edit.setEnabled(not running)
        self.fingerprint_checkbox.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.undo_button.setEnabled(not running)

    def _populate_review_queue(self) -> None:
        self.group_list.clear()
        for group in self._review_groups:
            title = group.editions[0].title or "Unknown title"
            item = QListWidgetItem(f"{title} · {len(group.editions)} editions")
            self.group_list.addItem(item)
        self.queue_count.setText(f"{len(self._review_groups)} remaining")
        if self._review_groups:
            self.group_list.setCurrentRow(0)
            self._set_review_enabled(True)
        else:
            self.group_detail.setText("No uncertain matches to review.")
            self.edition_list.clear()
            self._set_review_enabled(False)

    def _show_group(self, row: int) -> None:
        if row < 0 or row >= len(self._review_groups):
            return
        group = self._review_groups[row]
        result_lines = [
            f"Candidate match · {len(group.editions)} editions",
            "Fuzzy matches are never moved automatically.",
        ]
        for comparison in group.comparisons:
            if comparison.reasons:
                result_lines.append("Evidence: " + "; ".join(comparison.reasons))
        self.group_detail.setText("\n".join(result_lines))
        self.edition_list.clear()
        ordered = sorted(group.editions, key=preference_key, reverse=True)
        for edition in ordered:
            item = QListWidgetItem(_edition_summary(edition))
            item.setData(Qt.ItemDataRole.UserRole, edition)
            self.edition_list.addItem(item)
        self.edition_list.setCurrentRow(0)

    def _move_review_copies(self) -> None:
        row = self.group_list.currentRow()
        preferred_item = self.edition_list.currentItem()
        if row < 0 or preferred_item is None:
            return
        group = self._review_groups[row]
        preferred = preferred_item.data(Qt.ItemDataRole.UserRole)
        moving = [edition for edition in group.editions if edition is not preferred]
        file_count = sum(len(edition.paths) for edition in moving)
        answer = QMessageBox.question(
            self,
            "Move selected duplicates?",
            f"Keep {preferred.title} from {Path(preferred.paths[0]).parent if preferred.paths else 'unknown location'} "
            f"and move {file_count} files from {len(moving)} other edition(s) into the review folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            root = Path(self.path_edit.text().strip())
            for edition in moving:
                move_edition(edition, root)
        except Exception as error:
            LOGGER.exception("Manual duplicate move failed")
            QMessageBox.critical(self, "Move failed", str(error))
            return
        self._resolve_current_group(f"Moved {file_count} files to review.")

    def _keep_review_group(self) -> None:
        row = self.group_list.currentRow()
        if row >= 0:
            LOGGER.info("Kept all editions in review group %r", self._review_groups[row].editions[0].title)
        self._resolve_current_group("Kept all editions in place.")

    def _resolve_current_group(self, message: str) -> None:
        row = self.group_list.currentRow()
        if row < 0:
            return
        self._review_groups.pop(row)
        self._populate_review_queue()
        self.status.setText(message)

    def _set_review_enabled(self, enabled: bool) -> None:
        self.move_button.setEnabled(enabled)
        self.keep_button.setEnabled(enabled)
        self.edition_list.setEnabled(enabled)

    def _undo_move(self) -> None:
        selected = self.path_edit.text().strip()
        if not selected or not Path(selected).is_dir():
            QMessageBox.warning(self, "Library folder required", "Choose the library used for the move.")
            return
        answer = QMessageBox.question(
            self,
            "Undo last move?",
            "Restore the most recently moved edition to its original folder? Existing files will not be overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = undo_last_move(Path(selected))
            self.status.setText(f"Restored {len(restored)} files to their original locations.")
        except Exception as error:
            LOGGER.exception("Undo move failed")
            QMessageBox.warning(self, "Undo unavailable", str(error))


def main() -> int:
    log_path = _configure_logging()
    LOGGER.info("Audiobook Duplicate Finder started; log file: %s", log_path)
    application = QApplication(sys.argv)
    application.setApplicationName("Audiobook Duplicate Finder")
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())