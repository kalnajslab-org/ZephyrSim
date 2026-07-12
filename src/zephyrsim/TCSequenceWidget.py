#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Named TC sequence panel: a table of (TC, wait) rows run in timed order."""

import re
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtWidgets

VALID_MODES = frozenset(["SB", "FL", "LP", "SA", "EF"])


def classify_command(text: str) -> str:
    """Return 'mode', 'tc', or 'empty' for a sequencer command cell value."""
    t = text.strip()
    if not t:
        return "empty"
    if t.upper() in VALID_MODES:
        return "mode"
    return "tc"

_DUR_RE = re.compile(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$')


def parse_duration(s: str) -> float:
    """Parse '1h30m5s', '2m30s', '45s', '5' (bare int = minutes) → float seconds."""
    s = s.strip()
    if not s:
        raise ValueError("empty duration")
    if s.isdigit():
        return float(s) * 60
    m = _DUR_RE.match(s)
    if not m or not any(m.groups()):
        raise ValueError(f"unrecognized duration: {s!r}")
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def format_duration(seconds: float) -> str:
    """Format total seconds as '1h30m5s', '2m30s', '45s', '0s'."""
    total = int(round(abs(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return "".join(parts)


class TCSequenceWidget(QtWidgets.QWidget):
    """Floating tool window for managing and running named TC sequences.

    Opened/closed by the Sequences button in MainWindowQt. Sequences are
    stored as dict[name → list[{tc, wait_s}]] and persisted in ZephyrSim.ini
    under the active config set's TCSequences key.
    """

    sequences_changed = QtCore.pyqtSignal(dict)       # any edit; caller persists
    run_requested = QtCore.pyqtSignal(str, list, bool) # (name, steps, repeat)
    stop_requested = QtCore.pyqtSignal()

    def __init__(self, sequences: Optional[Dict[str, List[dict]]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TC Sequences")
        self._sequences: Dict[str, List[dict]] = dict(sequences or {})
        self._running_name: str = ""
        self._saving: bool = False
        self._build_ui()
        self._populate_combo()

    # ---- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        self._state_label = QtWidgets.QLabel("Not running")
        self._state_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._state_label.setContentsMargins(4, 2, 4, 2)
        root.addWidget(self._state_label)

        # name selector row
        name_row = QtWidgets.QHBoxLayout()
        self._name_combo = QtWidgets.QComboBox()
        self._name_combo.setMinimumWidth(120)
        self._name_combo.currentIndexChanged.connect(self._load_current)
        name_row.addWidget(self._name_combo)
        for label, slot in [("New", self._on_new), ("Rename", self._on_rename), ("Delete", self._on_delete)]:
            btn = QtWidgets.QPushButton(label)
            btn.setMaximumWidth(60)
            btn.clicked.connect(slot)
            name_row.addWidget(btn)
        name_row.addStretch(1)
        root.addLayout(name_row)

        # sequence table: columns TC | Wait
        self._table = QtWidgets.QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Command", "Wait"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 80)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setFixedHeight(280)
        self._table.itemChanged.connect(self._on_table_edited)
        root.addWidget(self._table)

        hint = QtWidgets.QLabel(
            "Command: TC params (comma-separated), or mode: SB FL LP SA EF\n"
            "Wait: 30s · 2m30s · 1h20m · 5 (bare integer = minutes)"
        )
        hint.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(hint)

        # row management + run controls
        ctrl = QtWidgets.QHBoxLayout()
        for label, slot in [("+ Row", self._on_add_row), ("- Row", self._on_del_row)]:
            btn = QtWidgets.QPushButton(label)
            btn.setMaximumWidth(55)
            btn.clicked.connect(slot)
            ctrl.addWidget(btn)
        ctrl.addStretch(1)
        self._repeat_check = QtWidgets.QCheckBox("Repeat")
        ctrl.addWidget(self._repeat_check)
        self._run_btn = QtWidgets.QPushButton("Run")
        self._run_btn.setMaximumWidth(50)
        self._run_btn.setStyleSheet("QPushButton { color: darkgreen; }")
        self._run_btn.clicked.connect(self._on_run)
        ctrl.addWidget(self._run_btn)
        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._stop_btn.setMaximumWidth(50)
        self._stop_btn.setStyleSheet("QPushButton { color: red; }")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        ctrl.addWidget(self._stop_btn)
        root.addLayout(ctrl)

        self._status_label = QtWidgets.QLabel("")
        root.addWidget(self._status_label)

    # ---- combo / table population ----------------------------------------

    def _populate_combo(self) -> None:
        current = self._name_combo.currentText()
        self._name_combo.blockSignals(True)
        self._name_combo.clear()
        for name in sorted(self._sequences):
            self._name_combo.addItem(name)
        idx = self._name_combo.findText(current)
        self._name_combo.setCurrentIndex(max(idx, 0))
        self._name_combo.blockSignals(False)
        self._load_current()

    def _current_name(self) -> Optional[str]:
        t = self._name_combo.currentText()
        return t if t else None

    def _load_current(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        name = self._current_name()
        if name and name in self._sequences:
            for step in self._sequences[name]:
                self._insert_row(step.get("tc", ""), step.get("wait_s", 0.0))
        self._table.blockSignals(False)

    def _insert_row(self, tc: str, wait_s: float, at: int = -1) -> None:
        r = at if at >= 0 else self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QtWidgets.QTableWidgetItem(tc))
        self._table.setItem(r, 1, QtWidgets.QTableWidgetItem(format_duration(wait_s)))

    # ---- editing ---------------------------------------------------------

    def _save_current(self) -> None:
        name = self._current_name()
        if not name:
            return
        self._saving = True
        rows = []
        try:
            for r in range(self._table.rowCount()):
                tc_item = self._table.item(r, 0)
                wait_item = self._table.item(r, 1)
                tc = tc_item.text().strip() if tc_item else ""
                wait_str = wait_item.text().strip() if wait_item else "0s"
                try:
                    wait_s = parse_duration(wait_str)
                except ValueError:
                    wait_s = 0.0
                if wait_s <= 0:
                    wait_s = 60.0
                normalized = format_duration(wait_s)
                if wait_item and normalized != wait_str:
                    wait_item.setText(normalized)
                rows.append({"tc": tc, "wait_s": wait_s})
        finally:
            self._saving = False
        self._sequences[name] = rows
        self.sequences_changed.emit(dict(self._sequences))

    def _on_table_edited(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._saving:
            return
        if item.column() == 0 and classify_command(item.text()) == "mode":
            upper = item.text().strip().upper()
            if upper != item.text():
                def _apply():
                    self._saving = True
                    item.setText(upper)
                    self._saving = False
                    self._save_current()
                QtCore.QTimer.singleShot(0, _apply)
                return
        self._save_current()

    def _on_add_row(self) -> None:
        selected_rows = sorted({i.row() for i in self._table.selectedIndexes()})
        at = (selected_rows[-1] + 1) if selected_rows else self._table.rowCount()
        self._table.blockSignals(True)
        self._insert_row("", 60, at=at)
        self._table.blockSignals(False)
        self._save_current()

    def _on_del_row(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self._table.blockSignals(True)
        for r in rows:
            self._table.removeRow(r)
        self._table.blockSignals(False)
        self._save_current()

    # ---- sequence CRUD ---------------------------------------------------

    def _on_new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New Sequence", "Name:")
        if not ok or not (name := name.strip()):
            return
        if name in self._sequences:
            QtWidgets.QMessageBox.warning(self, "Exists", f"'{name}' already exists.")
            return
        self._sequences[name] = []
        self._populate_combo()
        self._name_combo.setCurrentText(name)
        self.sequences_changed.emit(dict(self._sequences))

    def _on_rename(self) -> None:
        old = self._current_name()
        if not old:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename", "New name:", text=old)
        if not ok or not (name := name.strip()) or name == old:
            return
        if name in self._sequences:
            QtWidgets.QMessageBox.warning(self, "Exists", f"'{name}' already exists.")
            return
        self._sequences[name] = self._sequences.pop(old)
        self._populate_combo()
        self._name_combo.setCurrentText(name)
        self.sequences_changed.emit(dict(self._sequences))

    def _on_delete(self) -> None:
        name = self._current_name()
        if not name:
            return
        if QtWidgets.QMessageBox.question(
            self, "Delete", f"Delete '{name}'?"
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        del self._sequences[name]
        self._populate_combo()
        self.sequences_changed.emit(dict(self._sequences))

    # ---- run / stop ------------------------------------------------------

    def _on_run(self) -> None:
        name = self._current_name()
        if not name or not self._sequences.get(name):
            return
        self.run_requested.emit(name, list(self._sequences[name]), self._repeat_check.isChecked())

    def _on_stop(self) -> None:
        self.stop_requested.emit()

    def set_running_state(self, running: bool, status: str = "", name: str = "") -> None:
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._status_label.setText(status)
        if name:
            self._running_name = name
        if running:
            self._state_label.setText(f"Running: {self._running_name}")
            self._state_label.setStyleSheet(
                "QLabel { background-color: green; color: white; font-weight: bold; padding: 2px; }"
            )
        else:
            self._state_label.setText("Not running")
            self._state_label.setStyleSheet("")
            self._running_name = ""

    def load_sequences(self, sequences: dict) -> None:
        self._sequences = dict(sequences or {})
        self._populate_combo()
