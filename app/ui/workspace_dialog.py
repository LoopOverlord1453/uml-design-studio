"""The workspace selection dialog shown at start-up.

This is where the application asks for the folder the model will be built in
and the code generated into. Result: a ``Workspace`` (accept) or ``None``.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QRadioButton,
                             QVBoxLayout)

from ..core.workspace import (DEFAULT_GENERATED_DIR, DEFAULT_MODEL_DIR,
                              Workspace, WorkspaceError)
from .theme import C, mono_font, ui_font

PATH_ROLE = Qt.ItemDataRole.UserRole + 1


class WorkspaceDialog(QDialog):
    """Select / create a workspace."""

    def __init__(self, recent: List[str], parent=None,
                 allow_cancel: bool = True, on_forget=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workspace")
        self.setModal(True)
        self.resize(720, 600)
        self._result: Optional[Workspace] = None
        self._recent = list(recent)
        #: Called with the trimmed list when the user forgets an entry.
        #: The dialog does not touch the settings itself -- whoever knows
        #: where the list is stored is the one that persists it.
        self._on_forget = on_forget

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        title = QLabel("UML Design Studio")
        f = ui_font(15)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        sub = QLabel("Pick the folder that will hold your models, the "
                     "generated code and the git repository.")
        sub.setFont(ui_font(9))
        sub.setStyleSheet("color: %s;" % C.TEXT_DIM)
        sub.setWordWrap(True)
        root.addWidget(sub)

        # THE FOLDER-STRUCTURE BOX WAS REMOVED.
        #
        # We were explaining the same thing (that model/ and generated/ will be
        # created) both in a frame here and in the preview at the bottom. The
        # user said "there is a lot of text and it looks complicated"; instead of
        # an abstract template, the one-line preview showing the CHOSEN path is
        # what stays -- it is more concrete.

        # -------------------------------------------------------------- recent
        self.rb_recent = QRadioButton("Recent workspaces")
        self.rb_recent.setFont(ui_font(10))
        root.addWidget(self.rb_recent)

        self.recent_list = QListWidget()
        self.recent_list.setFont(ui_font(9))
        # All 10 entries should be visible without scrolling (workspace.MAX_RECENT).
        self.recent_list.setMaximumHeight(232)
        self.recent_list.setAlternatingRowColors(True)
        for order, path in enumerate(self._recent, start=1):
            name = os.path.basename(path.rstrip(os.sep)) or path
            item = QListWidgetItem("%2d.  %-24s %s" % (order, name, path))
            item.setData(PATH_ROLE, path)
            # Long paths are elided; the full path stays in the tooltip.
            item.setToolTip(path)
            item.setFont(mono_font(9))
            self.recent_list.addItem(item)
        root.addWidget(self.recent_list)

        # THE LIST CAN BE EMPTIED FROM HERE.
        #
        # The entries are paths the user has opened before, and one of them
        # may be a folder they would rather not see announced every time the
        # application starts. Without a way out of the interface the only
        # remedy is editing the settings store by hand, which nobody should
        # have to do to forget a folder.
        self.empty_note = QLabel("No workspace has been opened yet.")
        self.empty_note.setFont(ui_font(9))
        self.empty_note.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self.empty_note.setContentsMargins(22, 0, 0, 4)
        root.addWidget(self.empty_note)

        forget_row = QHBoxLayout()
        forget_row.setContentsMargins(22, 0, 0, 0)
        self.btn_forget = QPushButton("Forget selected")
        self.btn_forget.setFont(ui_font(9))
        self.btn_forget.setToolTip(
            "Remove the selected folder from this list. The folder itself and "
            "everything in it are left alone.")
        self.btn_forget.clicked.connect(self._forget_selected)
        self.btn_forget_all = QPushButton("Clear list")
        self.btn_forget_all.setFont(ui_font(9))
        self.btn_forget_all.setToolTip(
            "Empty the list. No folder is deleted.")
        self.btn_forget_all.clicked.connect(self._forget_all)
        forget_row.addWidget(self.btn_forget)
        forget_row.addWidget(self.btn_forget_all)
        forget_row.addStretch(1)
        root.addLayout(forget_row)
        self._sync_recent_visibility()

        # ------------------------------------------------------------ existing
        self.rb_open = QRadioButton("Open an existing folder")
        self.rb_open.setFont(ui_font(10))
        root.addWidget(self.rb_open)

        open_row = QHBoxLayout()
        open_row.setContentsMargins(22, 0, 0, 0)
        self.ed_open = QLineEdit()
        self.ed_open.setPlaceholderText("Folder path")
        self.ed_open.setFont(mono_font(9))
        btn_open = QPushButton("Browse…")
        btn_open.setFont(ui_font(9))
        btn_open.clicked.connect(self._browse_open)
        open_row.addWidget(self.ed_open, 1)
        open_row.addWidget(btn_open)
        root.addLayout(open_row)

        # ------------------------------------------------------------- new
        self.rb_new = QRadioButton("Create a new workspace")
        self.rb_new.setFont(ui_font(10))
        root.addWidget(self.rb_new)

        grid = QGridLayout()
        grid.setContentsMargins(22, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        lbl_parent = QLabel("Parent folder")
        lbl_parent.setFont(ui_font(9))
        self.ed_parent = QLineEdit(_default_parent())
        self.ed_parent.setFont(mono_font(9))
        btn_parent = QPushButton("Browse…")
        btn_parent.setFont(ui_font(9))
        btn_parent.clicked.connect(self._browse_parent)
        grid.addWidget(lbl_parent, 0, 0)
        grid.addWidget(self.ed_parent, 0, 1)
        grid.addWidget(btn_parent, 0, 2)

        lbl_name = QLabel("Name")
        lbl_name.setFont(ui_font(9))
        self.ed_name = QLineEdit("my-uml-project")
        self.ed_name.setFont(mono_font(9))
        grid.addWidget(lbl_name, 1, 0)
        grid.addWidget(self.ed_name, 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        self.chk_git = QCheckBox("Initialise a git repository (git init)")
        self.chk_git.setFont(ui_font(9))
        self.chk_git.setChecked(True)
        chk_row = QHBoxLayout()
        chk_row.setContentsMargins(22, 0, 0, 0)
        chk_row.addWidget(self.chk_git)
        root.addLayout(chk_row)

        # ------------------------------------------------------------- preview
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: %s;" % C.BORDER_LIGHT)
        root.addWidget(line)

        self.preview = QLabel("")
        self.preview.setFont(mono_font(9))
        self.preview.setStyleSheet("color: %s;" % C.TEXT_DIM)
        root.addWidget(self.preview)

        root.addStretch(1)

        # ------------------------------------------------------------- buttons
        buttons = QDialogButtonBox()
        self.btn_ok = buttons.addButton("Open", QDialogButtonBox.ButtonRole.AcceptRole)
        if allow_cancel:
            buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # ------------------------------------------------------------ connections
        for rb in (self.rb_recent, self.rb_open, self.rb_new):
            rb.toggled.connect(self._sync)
        self.recent_list.currentRowChanged.connect(lambda _i: self._sync())
        self.recent_list.itemDoubleClicked.connect(lambda _i: self._accept())
        self.ed_open.textChanged.connect(lambda _t: self._sync())
        self.ed_parent.textChanged.connect(lambda _t: self._sync())
        self.ed_name.textChanged.connect(lambda _t: self._sync())

        if self._recent:
            self.rb_recent.setChecked(True)
            self.recent_list.setCurrentRow(0)
        else:
            self.rb_new.setChecked(True)
        self._sync()

    # ----------------------------------------------------------------- result

    def workspace(self) -> Optional[Workspace]:
        return self._result

    def remaining_recent(self) -> List[str]:
        """What is left of the recent list after any forgetting."""
        return list(self._recent)

    # ------------------------------------------------------------- forgetting

    def _sync_recent_visibility(self) -> None:
        """Shows the list or the 'nothing yet' note, never both."""
        has_any = bool(self._recent)
        self.recent_list.setVisible(has_any)
        self.empty_note.setVisible(not has_any)
        self.btn_forget.setVisible(has_any)
        self.btn_forget_all.setVisible(has_any)
        if not has_any and self.rb_recent.isChecked():
            # The mode the user was in no longer exists; land somewhere valid
            # instead of leaving the dialog with nothing selectable.
            self.rb_new.setChecked(True)
        self.rb_recent.setEnabled(has_any)

    def _announce_forget(self) -> None:
        if self._on_forget is not None:
            self._on_forget(list(self._recent))
        self._sync_recent_visibility()
        self._renumber()
        self._sync()

    def _renumber(self) -> None:
        """Keeps the leading 1..N numbering correct after a removal."""
        for row in range(self.recent_list.count()):
            item = self.recent_list.item(row)
            path = item.data(PATH_ROLE)
            name = os.path.basename(path.rstrip(os.sep)) or path
            item.setText("%2d.  %-24s %s" % (row + 1, name, path))

    def _forget_selected(self) -> None:
        row = self.recent_list.currentRow()
        if row < 0:
            return
        item = self.recent_list.takeItem(row)
        if item is None:
            return
        path = item.data(PATH_ROLE)
        self._recent = [p for p in self._recent if p != path]
        self._announce_forget()

    def _forget_all(self) -> None:
        if not self._recent:
            return
        reply = QMessageBox.question(
            self, "Clear the list",
            "Remove all %d entries from the recent list?\n\n"
            "No folder is deleted -- only this list is emptied."
            % len(self._recent),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.recent_list.clear()
        self._recent = []
        self._announce_forget()

    # ----------------------------------------------------------- internals

    def _mode(self) -> str:
        if self.rb_open.isChecked():
            return "open"
        if self.rb_new.isChecked():
            return "new"
        return "recent"

    def _sync(self) -> None:
        mode = self._mode()
        self.recent_list.setEnabled(mode == "recent")
        self.ed_open.setEnabled(mode == "open")
        self.ed_parent.setEnabled(mode == "new")
        self.ed_name.setEnabled(mode == "new")
        self.chk_git.setEnabled(mode == "new")

        target = self._target_root()
        if not target:
            self.preview.setText("")
            self.btn_ok.setEnabled(False)
            return
        self.btn_ok.setEnabled(True)
        if Workspace.is_workspace(target):
            not_ = "opens this workspace"
        elif os.path.isdir(target):
            not_ = "adds %s/ and %s/ to this folder" % (
                DEFAULT_MODEL_DIR, DEFAULT_GENERATED_DIR)
        else:
            not_ = "creates the folder with %s/ and %s/" % (
                DEFAULT_MODEL_DIR, DEFAULT_GENERATED_DIR)
        self.preview.setText(target + chr(10) + not_)

    def _target_root(self) -> str:
        mode = self._mode()
        if mode == "recent":
            item = self.recent_list.currentItem()
            return item.data(PATH_ROLE) if item else ""
        if mode == "open":
            return self.ed_open.text().strip()
        parent = self.ed_parent.text().strip()
        name = self.ed_name.text().strip()
        if not parent or not name:
            return ""
        return os.path.join(parent, name)

    def _browse_open(self) -> None:
        start = self.ed_open.text().strip() or _default_parent()
        path = QFileDialog.getExistingDirectory(self, "Workspace folder",
                                                start)
        if path:
            self.rb_open.setChecked(True)
            self.ed_open.setText(path)

    def _browse_parent(self) -> None:
        start = self.ed_parent.text().strip() or _default_parent()
        path = QFileDialog.getExistingDirectory(self, "Parent folder", start)
        if path:
            self.rb_new.setChecked(True)
            self.ed_parent.setText(path)

    def _accept(self) -> None:
        target = self._target_root()
        if not target:
            return
        mode = self._mode()

        if mode == "new":
            if os.path.exists(target) and os.listdir(target):
                if Workspace.is_workspace(target):
                    pass        # opening an existing one is fine
                else:
                    reply = QMessageBox.question(
                        self, "Folder is not empty",
                        "'%s' is not empty. The workspace layout will be "
                        "added inside it. Continue?" % target,
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
        elif not os.path.isdir(target):
            QMessageBox.warning(self, "Folder not found",
                                "'%s' was not found." % target)
            return

        try:
            if Workspace.is_workspace(target):
                ws = Workspace.load(target)
                ws.ensure_layout()
            else:
                ws = Workspace.create(target)
        except WorkspaceError as exc:
            QMessageBox.critical(self, "Could not open the workspace", str(exc))
            return

        self._init_git = bool(self.chk_git.isChecked() and mode == "new")
        self._result = ws
        self.accept()

    @property
    def init_git(self) -> bool:
        return bool(getattr(self, "_init_git", False))


def _default_parent() -> str:
    for candidate in (os.path.join(os.path.expanduser("~"), "Documents"),
                      os.path.expanduser("~")):
        if os.path.isdir(candidate):
            return candidate
    return os.getcwd()


def pick_workspace(recent: List[str], parent=None,
                   allow_cancel: bool = True, on_forget=None):
    """Shows the dialog; returns ``(Workspace | None, init_git)``.

    ``on_forget`` is called with the trimmed list whenever the user removes an
    entry -- including when they then press Cancel, because forgetting a folder
    is a decision of its own and should not depend on opening another one.
    """
    dlg = WorkspaceDialog(recent, parent, allow_cancel=allow_cancel,
                          on_forget=on_forget)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None, False
    return dlg.workspace(), dlg.init_git
