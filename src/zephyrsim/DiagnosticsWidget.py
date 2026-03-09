#!/usr/bin/env python3
"""DiagnosticsWidget: compact message display with a scrollable history popup.

Priority levels (module-level constants):
    INFO    = 0  — displayed in default color
    WARNING = 1  — displayed in orange
    ERROR   = 2  — displayed in red

Usage::

    widget = DiagnosticsWidget("Status")
    some_signal.connect(widget.receive_message)  # signal must be (int, str)

    # Or call directly:
    widget.receive_message(WARNING, "Something looks off")
"""

import datetime
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------

INFO = 0
WARNING = 1
ERROR = 2

_COLORS: dict[int, Optional[str]] = {
    INFO: None,
    WARNING: "darkorange",
    ERROR: "red",
}

_LABELS: dict[int, str] = {
    INFO: "INFO",
    WARNING: "WARN",
    ERROR: "ERR",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append_colored(edit: QtWidgets.QTextEdit, text: str, color: Optional[str]) -> None:
    cursor = edit.textCursor()
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
    fmt = QtGui.QTextCharFormat()
    if color:
        fmt.setForeground(QtGui.QBrush(QtGui.QColor(color)))
    else:
        fmt.setForeground(QtGui.QBrush(edit.palette().color(QtGui.QPalette.ColorRole.Text)))
    cursor.insertText(text, fmt)
    edit.setTextCursor(cursor)
    edit.ensureCursorVisible()


class _HistoryDialog(QtWidgets.QDialog):
    """Non-modal scrollable history window. Hides rather than closes."""

    def __init__(self, title: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(640, 400)

        layout = QtWidgets.QVBoxLayout(self)

        self._text = QtWidgets.QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._text)

        btn_row = QtWidgets.QHBoxLayout()
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._text.clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def append(self, text: str, color: Optional[str]) -> None:
        _append_colored(self._text, text, color)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # Hide instead of destroying so the window can be re-opened.
        event.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# Public widget
# ---------------------------------------------------------------------------

class DiagnosticsWidget(QtWidgets.QWidget):
    """Compact widget: single-line latest-message display + history button.

    The single-line box always shows the most recent message, coloured by
    priority.  The History button opens a non-modal popup containing every
    message received since the widget was created (or last cleared).

    Connect an external ``(int, str)`` signal to :meth:`receive_message`, or
    call it directly.

    :param title: Window title for the history popup and the group label.
    :param parent: Optional Qt parent widget.
    """

    def __init__(self, title: str = "Diagnostics", parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._history = _HistoryDialog(title, parent=self)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._latest = QtWidgets.QLabel("No messages yet")
        self._latest.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self._latest, 1)

        self._btn = QtWidgets.QPushButton("History")
        self._btn.setToolTip("Show message history")
        self._btn.clicked.connect(self._show_history)
        layout.addWidget(self._btn)

    # ------------------------------------------------------------------
    # Slot
    # ------------------------------------------------------------------

    @QtCore.pyqtSlot(int, str, str)
    def receive_message(self, priority: int, summary: str, details: str = "") -> None:
        """Display a message at the given *priority* level.

        :param priority: One of :data:`INFO`, :data:`WARNING`, :data:`ERROR`.
        :param summary: Short summary shown in the main label.
        :param details: Optional detail line shown only in history.
        """
        color = _COLORS.get(priority)
        label = _LABELS.get(priority, "???")

        # Update the single-line display with summary only.
        self._latest.setText(f"[{label}] {summary}")
        if color:
            self._latest.setStyleSheet(f"color: {color};")
        else:
            self._latest.setStyleSheet("")

        # Alert the history button for warnings and errors.
        if priority >= WARNING:
            self._btn.setStyleSheet("color: red;")

        # Append timestamped summary + optional details to history.
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._history.append(f"[{ts}] [{label}] {summary}\n", color)
        if details:
            self._history.append(f"    {details}\n", color)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _show_history(self) -> None:
        self._latest.setText("")
        self._latest.setStyleSheet("")
        self._btn.setStyleSheet("")
        self._history.show()
        self._history.raise_()
        self._history.activateWindow()


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("DiagnosticsWidget — standalone test")

    central = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(central)

    diag = DiagnosticsWidget("Event History")

    group = QtWidgets.QGroupBox("Diagnostics")
    group_layout = QtWidgets.QVBoxLayout(group)
    group_layout.addWidget(diag)
    layout.addWidget(group)

    # Test-emission buttons
    counter = [0]

    def _send(priority: int, label: str) -> None:
        counter[0] += 1
        diag.receive_message(priority, f"{label} message #{counter[0]}: the quick brown fox")

    btn_group = QtWidgets.QGroupBox("Send test messages")
    btn_layout = QtWidgets.QHBoxLayout(btn_group)
    for pri, lbl, color in [(INFO, "INFO", "black"), (WARNING, "WARNING", "darkorange"), (ERROR, "ERROR", "red")]:
        btn = QtWidgets.QPushButton(lbl)
        btn.setStyleSheet(f"color: {color};")
        btn.clicked.connect(lambda _=False, p=pri, l=lbl: _send(p, l))
        btn_layout.addWidget(btn)
    btn_layout.addStretch()
    layout.addWidget(btn_group)

    layout.addStretch()
    win.setCentralWidget(central)
    win.resize(520, 160)
    win.show()

    sys.exit(app.exec())
