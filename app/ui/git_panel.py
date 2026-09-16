"""Git paneli: commit agaci, degisiklik listeleri ve fark goruntuleyici.

Yerlesim
--------
    [ arac cubugu ]
    [ dal / durum seridi ]
    ------------------------------------------------------------------
    | commit agaci (serit cizimli)                                   |
    ------------------------------------------------------------------
    | HAZIRLANMIS / HAZIRLANMAMIS  |  FARK                           |
    ------------------------------------------------------------------

Butun git cagrilari ``app.core.git_backend`` uzerinden yapilir; ag islemleri
(getir / cek / gonder) arayuzu kilitlemesin diye ayri bir is parcaciginda
kosar.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import (QPointF, QRectF, QSize, Qt, QThread, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter,
                         QPainterPath, QPen, QSyntaxHighlighter,
                         QTextCharFormat)
from PyQt6.QtWidgets import (QAbstractItemView, QAbstractScrollArea,
                             QCheckBox, QDialog, QDialogButtonBox, QFrame,
                             QHBoxLayout, QInputDialog, QLabel,
                             QMessageBox,
                             QTreeWidget, QTreeWidgetItem,
                             QPlainTextEdit, QPushButton, QSplitter,
                             QToolButton, QVBoxLayout, QWidget)

from ..core import model_diff
from ..core.git_backend import (LANE_COLORS, Commit, GitError, GitFile, Repo,
                                diff_stats, git_available, lane_count)
from .contrast import apply_contrast
from .theme import C, mono_font, ui_font

ROW_H = 26
LANE_W = 15
GRAPH_LEFT = 14
NODE_R = 4.4

PATH_ROLE = Qt.ItemDataRole.UserRole + 1
UNTRACKED_ROLE = Qt.ItemDataRole.UserRole + 2
#: Klasor dugumunun tam yolu (dosya dugumlerinde bos).
FOLDER_ROLE = Qt.ItemDataRole.UserRole + 3


# ============================================================== is parcaciklari

class GitWorker(QThread):
    """Uzun suren git islemini (ag) arayuzu bloklamadan kosturur."""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, task: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:               # pragma: no cover - is parcacigi
        try:
            self.done.emit(self._task())
        except GitError as exc:
            self.failed.emit(exc.message)
        except Exception as exc:         # beklenmeyen: yine de arayuze tasi
            self.failed.emit(str(exc))


# =================================================================== fark gorunumu

class _DiffHighlighter(QSyntaxHighlighter):
    """Birlesik fark metnini satirin ilk karakterine gore renklendirir."""

    def __init__(self, doc) -> None:
        super().__init__(doc)
        self.f_add = QTextCharFormat()
        self.f_add.setForeground(QColor(C.GIT_ADD))
        self.f_add.setBackground(QColor(C.GIT_ADD_BG))
        self.f_del = QTextCharFormat()
        self.f_del.setForeground(QColor(C.GIT_DEL))
        self.f_del.setBackground(QColor(C.GIT_DEL_BG))
        self.f_hunk = QTextCharFormat()
        self.f_hunk.setForeground(QColor(C.GIT_HUNK))
        self.f_hunk.setFontWeight(QFont.Weight.Bold)
        self.f_meta = QTextCharFormat()
        self.f_meta.setForeground(QColor(C.GIT_META))

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        if text.startswith("@@"):
            self.setFormat(0, len(text), self.f_hunk)
        elif text.startswith("### "):
            # Anlamsal model farkinda dosya basligi (bkz. core/model_diff).
            self.setFormat(0, len(text), self.f_hunk)
        elif text.startswith(("diff --git", "index ", "--- ", "+++ ",
                              "new file", "deleted file", "old mode",
                              "new mode", "similarity index", "rename from",
                              "rename to", "Binary files", "* ")):
            self.setFormat(0, len(text), self.f_meta)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.f_add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.f_del)


class DiffView(QPlainTextEdit):
    """Salt-okunur, renklendirilmis fark goruntuleyici."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(mono_font(9))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._apply_theme()
        self._hl = _DiffHighlighter(self.document())

    def _apply_theme(self) -> None:
        self.setStyleSheet("QPlainTextEdit { background: %s; color: %s; "
                           "border: none; }" % (C.EDITOR_BG, C.TEXT))

    def retheme(self) -> None:
        """Renkler KURULUMDA satir-ici stile gomulur; yeniden yazilmali.

        Renklendirici de kurulum anindaki renklerle derlenir, bu yuzden
        bastan kurulur -- yoksa fark satirlari eski temanin yesil/
        kirmizisinda kalirdi.
        """
        self._apply_theme()
        self._hl = _DiffHighlighter(self.document())
        self._hl.rehighlight()

    def show_diff(self, text: str, empty_note: str = "No differences.") -> None:
        self.setPlainText(text if text.strip() else empty_note)
        self.verticalScrollBar().setValue(0)


# ================================================================ commit agaci

class CommitGraphView(QAbstractScrollArea):
    """Serit (lane) cizimli commit agaci -- GitKraken benzeri gorunum."""

    commit_selected = pyqtSignal(str)      # sha

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._commits: List[Commit] = []
        self._row_of: Dict[str, int] = {}
        self._current = -1
        self._lanes = 0
        #: Rozetlere ayrilan ORTAK genislik (bkz. _ref_zone_width).
        self._ref_zone = 0.0
        #: Imlecin ustunde durdugu satir (-1 = yok).
        self._hover_row = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Fare izleme: dugum uzerine gelince commit mesajini gostermek icin
        # dugme basili olmadan da hareket olaylari gerekir.
        self.viewport().setMouseTracking(True)
        self._apply_theme()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _apply_theme(self) -> None:
        self.viewport().setStyleSheet("background: %s;" % C.EDITOR_BG)

    def retheme(self) -> None:
        self._apply_theme()
        self.viewport().update()

    # ------------------------------------------------------------------ veri

    def set_commits(self, commits: List[Commit]) -> None:
        keep = self.current_sha()
        self._commits = list(commits)
        self._row_of = {c.sha: i for i, c in enumerate(self._commits)}
        self._lanes = lane_count(self._commits)
        self._current = self._row_of.get(keep, 0 if self._commits else -1)
        self._ref_zone = self._ref_zone_width()
        self._update_scroll()
        self.viewport().update()
        if self._current >= 0:
            self.commit_selected.emit(self._commits[self._current].sha)

    def current_sha(self) -> str:
        if 0 <= self._current < len(self._commits):
            return self._commits[self._current].sha
        return ""

    def current_commit(self) -> Optional[Commit]:
        if 0 <= self._current < len(self._commits):
            return self._commits[self._current]
        return None

    # ------------------------------------------------------------------ olcu

    def _graph_width(self) -> int:
        return GRAPH_LEFT * 2 + max(1, self._lanes) * LANE_W

    def _ref_zone_width(self) -> float:
        """Butun satirlarda ROZETLERE ayrilacak ORTAK genislik.

        En genis rozet dizisine gore olculur ve ust sinirla kirpilir; cok
        uzun bir dal adi konu sutununu ekran disina itmemeli. Rozeti
        olmayan satirlar da bu bosluktan sonra baslar, boylece konular
        hizalanir.
        """
        if not self._commits:
            return 0.0
        metrics = QFontMetricsF(ui_font(8))
        en_genis = 0.0
        for commit in self._commits:
            genislik = 0.0
            for ref in commit.refs[:4]:
                metin = ref
                for on in ("HEAD -> ", "tag: "):
                    if metin.startswith(on):
                        metin = metin[len(on):]
                genislik += metrics.horizontalAdvance(metin) + 12.0 + 5.0
            en_genis = max(en_genis, genislik)
        return min(en_genis, 240.0)

    def _update_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setSingleStep(ROW_H)
        bar.setPageStep(max(ROW_H, self.viewport().height()))
        total = len(self._commits) * ROW_H
        bar.setRange(0, max(0, total - self.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scroll()

    def sizeHint(self) -> QSize:
        return QSize(720, 320)

    # ------------------------------------------------------------------ cizim

    def paintEvent(self, event) -> None:
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.viewport().rect(), QColor(C.EDITOR_BG))

        if not self._commits:
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            p.setFont(ui_font(10))
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                       "No commits yet.")
            p.end()
            return

        offset = self.verticalScrollBar().value()
        vh = self.viewport().height()
        vw = self.viewport().width()
        first = max(0, offset // ROW_H - 1)
        last = min(len(self._commits) - 1, (offset + vh) // ROW_H + 1)

        def y_of(row: int) -> float:
            return row * ROW_H - offset + ROW_H / 2.0

        def x_of(lane: int) -> float:
            return GRAPH_LEFT + lane * LANE_W + LANE_W / 2.0

        # --- imlecin ustundeki satir (secili satirin ALTINA cizilir)
        if first <= self._hover_row <= last and self._hover_row != self._current:
            p.fillRect(QRectF(0, self._hover_row * ROW_H - offset, vw, ROW_H),
                       QColor(C.HOVER))

        # --- secili satir zemini
        if first <= self._current <= last:
            p.fillRect(QRectF(0, self._current * ROW_H - offset, vw, ROW_H),
                       QColor(C.SELECTION))

        # --- kenarlar (ebeveyn baglantilari)
        for row, commit in enumerate(self._commits):
            yc = y_of(row)
            xc = x_of(commit.lane)
            for parent in commit.parents:
                prow = self._row_of.get(parent)
                if prow is None:
                    # yuklenmemis ata: kisa bir sap ciz
                    if -ROW_H <= yc <= vh + ROW_H:
                        self._pen_for(p, commit.lane)
                        p.drawLine(QPointF(xc, yc),
                                   QPointF(xc, yc + ROW_H * 0.7))
                    continue
                yp = y_of(prow)
                if (yc < -ROW_H and yp < -ROW_H) or \
                        (yc > vh + ROW_H and yp > vh + ROW_H):
                    continue
                plane = self._commits[prow].lane
                xp = x_of(plane)
                self._pen_for(p, plane if plane != commit.lane else commit.lane)
                path = QPainterPath(QPointF(xc, yc))
                if abs(xp - xc) < 0.5:
                    path.lineTo(QPointF(xp, yp))
                else:
                    bend = yc + ROW_H * 0.95
                    path.cubicTo(QPointF(xc, yc + ROW_H * 0.45),
                                 QPointF(xp, bend - ROW_H * 0.45),
                                 QPointF(xp, min(bend, yp)))
                    path.lineTo(QPointF(xp, yp))
                p.drawPath(path)

        # --- dugumler ve metin
        gw = self._graph_width()
        for row in range(first, last + 1):
            commit = self._commits[row]
            yc = y_of(row)
            xc = x_of(commit.lane)

            colour = QColor(LANE_COLORS[commit.lane % len(LANE_COLORS)])
            p.setPen(QPen(colour, 2.0))
            p.setBrush(QBrush(QColor(C.GIT_NODE) if commit.is_head else colour))
            radius = NODE_R + (1.4 if commit.is_merge else 0.0)
            p.drawEllipse(QPointF(xc, yc), radius, radius)
            if commit.is_head:
                p.setPen(QPen(QColor(C.GIT_HEAD_RING), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(xc, yc), radius + 3.0, radius + 3.0)

            # -- sag taraf: etiketler + konu + yazar + tarih + sha
            #
            # KONU SUTUNU SABIT x'te BASLAR. Etiketler eskiden konuyu saga
            # itiyordu: iki dalin bulundugu bir gecmiste "main" rozeti olan
            # satirin konusu digerlerinden daha saga kayiyor, mesajlar
            # birbirine karisiyordu. Rozetlere ayrilan genislik BUTUN
            # satirlar icin ortaktir (bkz. _ref_zone_width), boylece konular
            # tek bir sutunda hizalanir -- GitKraken'in yaptigi da budur.
            x = gw + 6.0
            p.setFont(ui_font(8))
            for ref in commit.refs[:4]:
                x = self._draw_ref(p, x, yc, ref)

            x = gw + 6.0 + self._ref_zone

            sha_w = 62.0
            date_w = 106.0
            author_w = 118.0
            avail = vw - x - sha_w - date_w - author_w - 18.0
            if avail > 40:
                p.setFont(ui_font(9))
                p.setPen(QPen(QColor(C.TEXT_BRIGHT if row == self._current
                                     else C.TEXT)))
                p.drawText(QRectF(x, yc - ROW_H / 2, avail, ROW_H),
                           int(Qt.AlignmentFlag.AlignVCenter
                               | Qt.AlignmentFlag.AlignLeft),
                           _elide(p, commit.subject, avail))

            p.setFont(ui_font(8))
            p.setPen(QPen(QColor(C.TEXT_DIM)))
            xa = vw - sha_w - date_w - author_w - 10.0
            p.drawText(QRectF(xa, yc - ROW_H / 2, author_w, ROW_H),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft),
                       _elide(p, commit.author, author_w - 6))
            p.drawText(QRectF(vw - sha_w - date_w - 10.0, yc - ROW_H / 2,
                              date_w, ROW_H),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignLeft),
                       _short_date(commit.when))
            p.setFont(mono_font(8))
            p.drawText(QRectF(vw - sha_w - 8.0, yc - ROW_H / 2, sha_w, ROW_H),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignRight),
                       commit.short)
        p.end()

    def _pen_for(self, p: QPainter, lane: int) -> None:
        colour = QColor(LANE_COLORS[lane % len(LANE_COLORS)])
        p.setPen(QPen(colour, 2.0, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_ref(self, p: QPainter, x: float, yc: float, ref: str) -> float:
        text = ref
        bg = C.GIT_REF_BRANCH
        if text.startswith("HEAD -> "):
            text = text[len("HEAD -> "):]
            bg = C.GIT_REF_HEAD
        elif text == "HEAD":
            bg = C.GIT_REF_HEAD
        elif text.startswith("tag: "):
            text = text[len("tag: "):]
            bg = C.GIT_REF_TAG
        elif "/" in text:
            bg = C.GIT_REF_REMOTE

        metrics = p.fontMetrics()
        w = metrics.horizontalAdvance(text) + 12.0
        rect = QRectF(x, yc - 8.0, w, 16.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(bg)))
        p.drawRoundedRect(rect, 7.0, 7.0)
        p.setPen(QPen(QColor(C.TEXT_BRIGHT)))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        return x + w + 5.0

    # ------------------------------------------------------------------ girdi

    def mousePressEvent(self, event) -> None:
        if not self._commits:
            return
        row = (event.position().y() + self.verticalScrollBar().value()) // ROW_H
        self._set_current(int(row))

    def _row_at(self, y: float) -> int:
        """Ekran y'sindeki satir; alan disi ise -1."""
        if not self._commits:
            return -1
        row = int((y + self.verticalScrollBar().value()) // ROW_H)
        if 0 <= row < len(self._commits):
            return row
        return -1

    def mouseMoveEvent(self, event) -> None:
        """Dugum uzerinde commit mesajini ipucu olarak gosterir.

        Ipucu YALNIZCA daireye yakinken cikar. Butun satiri ipucu alanina
        cevirmek, konu metni zaten okunurken ekrani surekli kaplayan bir
        balonla sonuclanirdi; kullanicinin istedigi de "dairelerin ustune
        gelince" davranisidir.
        """
        row = self._row_at(event.position().y())
        if row != self._hover_row:
            self._hover_row = row
            self.viewport().update()

        if row < 0:
            self.setToolTip("")
            return

        commit = self._commits[row]
        offset = self.verticalScrollBar().value()
        yc = row * ROW_H - offset + ROW_H / 2.0
        xc = GRAPH_LEFT + commit.lane * LANE_W + LANE_W / 2.0
        dx = event.position().x() - xc
        dy = event.position().y() - yc
        if (dx * dx + dy * dy) <= (NODE_R + 7.0) ** 2:
            self.setToolTip(self._commit_tooltip(commit))
        else:
            self.setToolTip("")

    def leaveEvent(self, event) -> None:
        if self._hover_row != -1:
            self._hover_row = -1
            self.viewport().update()
        self.setToolTip("")
        super().leaveEvent(event)

    @staticmethod
    def _commit_tooltip(commit) -> str:
        """Daire ipucu: konu, govde, yazar, tarih, sha ve dallar."""
        satirlar = [commit.subject]
        govde = (getattr(commit, "body", "") or "").strip()
        if govde:
            satirlar.append("")
            satirlar.append(govde)
        satirlar.append("")
        satirlar.append("%s · %s" % (commit.author, commit.when))
        satirlar.append("commit %s" % commit.sha)
        if commit.refs:
            satirlar.append("refs: %s" % ", ".join(commit.refs))
        if len(commit.parents) > 1:
            satirlar.append("merge of %d parents" % len(commit.parents))
        return "\n".join(satirlar)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_J):
            self._set_current(self._current + 1)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_K):
            self._set_current(self._current - 1)
        elif key == Qt.Key.Key_Home:
            self._set_current(0)
        elif key == Qt.Key.Key_End:
            self._set_current(len(self._commits) - 1)
        elif key == Qt.Key.Key_PageDown:
            self._set_current(self._current
                              + max(1, self.viewport().height() // ROW_H))
        elif key == Qt.Key.Key_PageUp:
            self._set_current(self._current
                              - max(1, self.viewport().height() // ROW_H))
        else:
            super().keyPressEvent(event)

    def _set_current(self, row: int) -> None:
        if not self._commits:
            return
        row = max(0, min(len(self._commits) - 1, row))
        if row == self._current:
            return
        self._current = row
        self._ensure_visible(row)
        self.viewport().update()
        self.commit_selected.emit(self._commits[row].sha)

    def _ensure_visible(self, row: int) -> None:
        bar = self.verticalScrollBar()
        top = row * ROW_H
        if top < bar.value():
            bar.setValue(top)
        elif top + ROW_H > bar.value() + self.viewport().height():
            bar.setValue(top + ROW_H - self.viewport().height())


def _elide(p: QPainter, text: str, width: float) -> str:
    metrics = p.fontMetrics()
    if metrics.horizontalAdvance(text) <= width:
        return text
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, int(width))


def _short_date(iso: str) -> str:
    """``2026-09-10T01:22:33+03:00`` -> ``10.09.2026 01:22``."""
    try:
        date_part, rest = iso.split("T", 1)
        year, month, day = date_part.split("-")
        return "%s.%s.%s %s" % (day, month, year, rest[:5])
    except (ValueError, IndexError):
        return iso[:16]


# ============================================================== commit diyalogu

class CommitDialog(QDialog):
    """Commit mesaji + degistir (amend) secenegi."""

    def __init__(self, staged: int, can_amend: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Commit")
        self.setModal(True)
        self.resize(560, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        info = QLabel("%d files staged." % staged)
        info.setFont(ui_font(9))
        info.setStyleSheet("color: %s;" % C.TEXT_DIM)
        layout.addWidget(info)

        self.edit = QPlainTextEdit()
        self.edit.setFont(mono_font(10))
        self.edit.setPlaceholderText("Commit message")
        # Yer tutucu metin ETIKET DEGILDIR (WCAG 3.3.2): odak
        # gelince kaybolur ve ekran okuyucu her zaman okumaz.
        self.edit.setAccessibleName("Commit message")
        layout.addWidget(self.edit, 1)

        self.chk_amend = QCheckBox("Amend the last commit (--amend)")
        self.chk_amend.setFont(ui_font(9))
        self.chk_amend.setEnabled(can_amend)
        layout.addWidget(self.chk_amend)

        buttons = QDialogButtonBox()
        self.btn_ok = buttons.addButton("Commit",
                                       QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.btn_ok.setEnabled(False)
        self.edit.textChanged.connect(
            lambda: self.btn_ok.setEnabled(bool(self.edit.toPlainText().strip())))
        self.edit.setFocus()

    def message(self) -> str:
        return self.edit.toPlainText().strip()

    def amend(self) -> bool:
        return self.chk_amend.isChecked()


# ==================================================================== ana panel

class GitPanel(QWidget):
    """Calisma alaninin git durumu, gecmisi ve farklari."""

    status_changed = pyqtSignal(str)        # durum cubugu icin kisa ozet
    #: Depo panelinde bir MODEL dosyasi secildi -> tuvalde gorsel fark.
    #:
    #: Kullanici "eklenen/cikarilan seyleri modelin resmi uzerinde
    #: goster" dedi. Metinsel fark bir JSON modelinde okunmaz;
    #: diyagramin kendisi okunur. Dosya yolu ve karsilastirma
    #: tabani (HEAD ya da hazirlik alani) birlikte yayilir.
    model_diff_requested = pyqtSignal(str, str)   # (mutlak yol, taban)

    def _apply_static_styles(self) -> None:
        """Satir-ici stilleri ETKIN temadan yeniden yazar.

        Bu panelin renkleri KURULUM aninda satir-ici stile gomuluyordu ve
        tema degisiminde hicbiri yenilenmiyordu: acik temaya gecilince
        arayuzun geri kalani aciliyor, depo paneli komple KOYU kaliyordu.
        """
        self._bar.setStyleSheet("background: %s; border-bottom: 1px solid %s;"
                                % (C.PANEL_DARK, C.BORDER))
        self._sep.setStyleSheet("color: %s;" % C.BORDER_LIGHT)
        self.notice.setStyleSheet("background: %s; color: %s;"
                                  % (C.PANEL, C.WARN))
        self.lbl_branch.setStyleSheet("color: %s;" % C.TEXT_DIM)
        for lst in self._lists:
            lst.setStyleSheet("QTreeWidget { background: %s; border: none; }"
                              % C.EDITOR_BG)
        for lbl in self._headers:
            lbl.setStyleSheet(
                "background: %s; color: %s; border-bottom: 1px solid %s;"
                % (C.PANEL_DARK, C.TEXT_DIM, C.BORDER))

    def retheme(self) -> None:
        """Tema degisiminde renkleri yeniden okur.

        Bu panelin bircok rengi KURULUM aninda satir-ici stile gomulur;
        alt bilesenleri bastan kurmak yerine panelin kendi tazeleme yolu
        cagrilir -- o yol etiketleri ve stilleri zaten yeniden yazar.
        """
        self._apply_static_styles()
        for alt in self.findChildren(QWidget):
            fn = getattr(alt, "retheme", None)
            if callable(fn) and alt is not self:
                fn()
        yenile = getattr(self, "refresh", None)
        if callable(yenile):
            try:
                yenile()
            except Exception:
                # Tazeleme disaridan veri ister (git deposu gibi); tema
                # degisimi bu yuzden basarisiz OLMAMALI.
                pass
        self.update()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.repo: Optional[Repo] = None
        self._worker: Optional[GitWorker] = None
        self._mode = "worktree"            # "worktree" | "commit"
        #: Tema degisiminde yeniden boyanacak bilesenler. Renkleri
        #: satir-ici stile gomuldugu icin tek tek tutulmak zorundalar.
        self._lists = []
        self._headers = []
        self._build()
        self._apply_static_styles()
        self.set_root(None)

    # ------------------------------------------------------------------ kurulum

    def _head(self, title: str) -> QLabel:
        """Bolum basligi kurar ve temaya kaydeder."""
        lbl = _header(title)
        self._headers.append(lbl)
        return lbl

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- arac cubugu
        bar = QWidget()
        self._bar = bar
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)

        self.btn_refresh = self._tool(
            "Refresh", self.refresh,
            "Re-read the repository status and history  (F6)")
        self.btn_init = self._tool(
            "Init Repository", self._init_repo,
            "Create a new git repository in the workspace folder")
        self.btn_stage_all = self._tool(
            "Stage All", self._stage_all,
            "Move every changed file into the staging area")
        self.btn_commit = self._tool(
            "Commit", self._commit,
            "Record the staged files as a new commit")
        self.btn_branch = self._tool(
            "Branch", self._branch_menu,
            "Create a branch or switch to another one")
        self.btn_fetch = self._tool(
            "Fetch", self._fetch,
            "Download commits from the remote WITHOUT changing your files")
        self.btn_pull = self._tool(
            "Pull", self._pull,
            "Fetch from the remote and merge into the current branch")
        self.btn_push = self._tool(
            "Push", self._push,
            "Upload the commits of the current branch to the remote")
        for b in (self.btn_refresh, self.btn_init, self.btn_stage_all,
                  self.btn_commit, self.btn_branch):
            row.addWidget(b)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        self._sep = sep
        row.addWidget(sep)
        for b in (self.btn_fetch, self.btn_pull, self.btn_push):
            row.addWidget(b)
        row.addStretch(1)

        self.lbl_branch = QLabel("")
        self.lbl_branch.setFont(ui_font(9))
        row.addWidget(self.lbl_branch)
        outer.addWidget(bar)

        # -- bilgi seridi (depo yok / git yok gibi durumlar)
        self.notice = QLabel("")
        self.notice.setFont(ui_font(9))
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(10, 8, 10, 8)
        self.notice.setVisible(False)
        outer.addWidget(self.notice)

        # -- commit agaci | degisiklikler + fark
        self.graph = CommitGraphView(self)

        self.list_staged = self._list()
        self.list_unstaged = self._list()

        btn_unstage = QPushButton("Unstage")
        btn_unstage.setFont(ui_font(9))
        btn_unstage.clicked.connect(self._unstage_selected)
        btn_stage = QPushButton("Stage")
        btn_stage.setFont(ui_font(9))
        btn_stage.clicked.connect(self._stage_selected)
        btn_discard = QPushButton("Discard Changes")
        btn_discard.setFont(ui_font(9))
        btn_discard.clicked.connect(self._discard_selected)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.addWidget(self._head("STAGED"))
        lv.addWidget(self.list_staged, 1)
        r1 = QHBoxLayout()
        r1.setContentsMargins(6, 4, 6, 4)
        r1.addWidget(btn_unstage)
        r1.addStretch(1)
        lv.addLayout(r1)
        lv.addWidget(self._head("UNSTAGED"))
        lv.addWidget(self.list_unstaged, 1)
        r2 = QHBoxLayout()
        r2.setContentsMargins(6, 4, 6, 4)
        r2.addWidget(btn_stage)
        r2.addWidget(btn_discard)
        r2.addStretch(1)
        lv.addLayout(r2)

        self.diff = DiffView(self)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.diff_header = self._head("DIFF")
        rv.addWidget(self.diff_header)
        rv.addWidget(self.diff, 1)

        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(left)
        bottom.addWidget(right)
        bottom.setSizes([330, 700])
        bottom.setStretchFactor(1, 1)

        vert = QSplitter(Qt.Orientation.Vertical)
        graph_host = QWidget()
        gv = QVBoxLayout(graph_host)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.setSpacing(0)
        gv.addWidget(self._head("COMMIT GRAPH"))
        gv.addWidget(self.graph, 1)
        vert.addWidget(graph_host)
        vert.addWidget(bottom)
        vert.setSizes([320, 380])
        vert.setStretchFactor(0, 1)
        vert.setStretchFactor(1, 1)
        outer.addWidget(vert, 1)

        self.graph.commit_selected.connect(self._on_commit_selected)
        self.list_staged.currentItemChanged.connect(
            lambda cur, _prev: self._show_file_diff(cur, staged=True))
        self.list_unstaged.currentItemChanged.connect(
            lambda cur, _prev: self._show_file_diff(cur, staged=False))
        self.list_staged.itemDoubleClicked.connect(
            lambda _i, _c: self._unstage_selected())
        self.list_unstaged.itemDoubleClicked.connect(
            lambda _i, _c: self._stage_selected())

    def _tool(self, text: str, slot: Callable[[], None],
              hint: str = "") -> QToolButton:
        """Depo komut dugmesi.

        IPUCU ZORUNLUDUR. Bu dugmelerin bir kismi geri alinmasi zor ya da
        AGA CIKAN islemler (Fetch / Pull / Push); ne yaptigini yazmadan
        sunmak hem kullanilabilirlik hem erisilebilirlik acisindan kusur.
        Ipucu ayni zamanda ekran okuyucularin okudugu aciklamadir.
        """
        btn = QToolButton()
        btn.setText(text)
        btn.setFont(ui_font(9))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setToolTip(hint or text)
        btn.setAccessibleName(text)
        if hint:
            btn.setAccessibleDescription(hint)
        btn.clicked.connect(slot)
        return btn

    def _list(self) -> QTreeWidget:
        """Degisen dosyalarin KLASOR AGACI.

        Onceden duz bir listeydi ve her satir tam yolu yaziyordu
        ("generated/blinky.c", "model/blinky.usm", ...). Calisma alani
        buyudukce liste okunaksiz hale geliyordu: hangi degisikligin
        hangi alt sisteme ait oldugu ancak yolu okuyarak anlasiliyordu.
        Agac, klasor yapisini dogrudan gosterir; bir klasoru secmek
        ICINDEKI butun dosyalari secer (topluca hazirlamak icin).
        """
        agac = QTreeWidget()
        agac.setFont(mono_font(9))
        agac.setColumnCount(1)
        agac.setHeaderHidden(True)
        agac.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        agac.setUniformRowHeights(True)
        agac.setExpandsOnDoubleClick(False)   # cift tik HAZIRLAR
        self._lists.append(agac)
        apply_contrast(agac)
        return agac

    # ------------------------------------------------------------------ kok

    def set_root(self, root: Optional[str]) -> None:
        """Panelin uzerinde calisacagi klasoru degistirir."""
        self.repo = Repo(root) if root else None
        self.refresh()

    # ------------------------------------------------------------------ yenile

    def refresh(self) -> None:
        """Durumu ve gecmisi diskten yeniden okur."""
        if not git_available():
            self._set_enabled(False)
            self._notice("git was not found. This panel starts working as "
                         "soon as git is installed and on PATH.", "error")
            self._clear()
            self.status_changed.emit("no git")
            return
        if self.repo is None:
            self._set_enabled(False)
            self._notice("No workspace selected.")
            self._clear()
            self.status_changed.emit("")
            return
        if not self.repo.is_repo():
            self._set_enabled(False)
            self.btn_init.setEnabled(True)
            self._notice("This workspace is not a git repository. Use "
                         "'Init Repository' to create one.")
            self._clear()
            self.lbl_branch.setText("")
            self.status_changed.emit("no repository")
            return

        self._set_enabled(True)
        self.btn_init.setEnabled(False)
        try:
            status = self.repo.status()
            commits = self.repo.log()
        except GitError as exc:
            self._notice(exc.message, "error")
            self._clear()
            self.status_changed.emit("git error")
            return

        self._notice("")
        self._fill_lists(status.staged(), status.unstaged(),
                         status.conflicts())
        self.graph.set_commits(commits)
        self._update_branch_label(status)
        self.btn_commit.setEnabled(bool(status.staged()))

        parts = [status.branch or "(no branch)"]
        if status.ahead:
            parts.append("↑%d" % status.ahead)
        if status.behind:
            parts.append("↓%d" % status.behind)
        n = len(status.files)
        parts.append("clean" if n == 0 else "%d changes" % n)
        self.status_changed.emit(" · ".join(parts))

    def _update_branch_label(self, status) -> None:
        bits = ["⎇ %s" % (status.branch or "(no branch)")]
        if status.upstream:
            bits.append("→ %s" % status.upstream)
        if status.ahead:
            bits.append("↑%d" % status.ahead)
        if status.behind:
            bits.append("↓%d" % status.behind)
        self.lbl_branch.setText("   ".join(bits))

    def _notice(self, text: str, kind: str = "warning") -> None:
        """Bilgi seridi. SIDDETE gore renklenir.

        Serit her zaman kehribardi; "git was not found" gibi calismayi
        TAMAMEN engelleyen durumlar da bir uyariyla ayni goruntuyle
        cikiyordu. Engelleyici durum KIRMIZI yazilir.
        """
        self.notice.setText(text)
        self.notice.setVisible(bool(text))
        renk = C.RED if kind == "error" else C.WARN
        self.notice.setStyleSheet("background: %s; color: %s;%s"
                                  % (C.PANEL, renk,
                                     " font-weight: bold;"
                                     if kind == "error" else ""))

    def _clear(self) -> None:
        self.list_staged.clear()
        self.list_unstaged.clear()
        self.graph.set_commits([])
        self.diff.show_diff("", "")

    def _set_enabled(self, on: bool) -> None:
        for b in (self.btn_refresh, self.btn_stage_all, self.btn_commit,
                  self.btn_branch, self.btn_fetch, self.btn_pull,
                  self.btn_push):
            b.setEnabled(on)
        self.btn_init.setEnabled(not on)

    # ------------------------------------------------------------------ listeler

    def _fill_lists(self, staged: List[GitFile], unstaged: List[GitFile],
                    conflicts: List[GitFile]) -> None:
        keep_s = _current_path(self.list_staged)
        keep_u = _current_path(self.list_unstaged)
        acik_s = _expanded_folders(self.list_staged)
        acik_u = _expanded_folders(self.list_unstaged)
        self.list_staged.clear()
        self.list_unstaged.clear()

        _build_tree(self.list_staged, [(gf, C.GIT_STAGED) for gf in staged])
        _build_tree(self.list_unstaged,
                    [(gf, C.GIT_CONFLICT) for gf in conflicts]
                    + [(gf, C.GIT_UNSTAGED) for gf in unstaged])

        # Acik klasorler ve secili dosya YENILEMEDEN SONRA korunur;
        # aksi halde her F6 kullaniciyi kokten yeniden gezdirirdi.
        _restore_expanded(self.list_staged, acik_s)
        _restore_expanded(self.list_unstaged, acik_u)
        _restore_path(self.list_staged, keep_s)
        _restore_path(self.list_unstaged, keep_u)
        if not staged and not unstaged and not conflicts:
            self.diff.show_diff("", "Working tree is clean.")

    def _selected_paths(self, lst: QTreeWidget) -> List[str]:
        """Secili DOSYALARIN yollari.

        Bir KLASOR secildiginde icindeki butun dosyalar sayilir: kullanici
        "generated" klasorunu secip topluca hazirlayabilsin. Ayni dosya
        iki kez sayilmaz (hem kendisi hem ustu secili olabilir).
        """
        out: List[str] = []
        gorulen = set()
        for it in lst.selectedItems():
            for yol in _dosyalari(it):
                if yol not in gorulen:
                    gorulen.add(yol)
                    out.append(yol)
        return out

    # ------------------------------------------------------------------ farklar

    def _show_file_diff(self, item: Optional[QTreeWidgetItem],
                        staged: bool) -> None:
        if item is None or self.repo is None:
            return
        path = item.data(0, PATH_ROLE)
        if not path:
            # KLASOR dugumu: tek bir farki yoktur; ozet yazilir.
            dosyalar = _dosyalari(item)
            self.diff_header.setText(
                "DIFF — %s/   %d file(s)"
                % (item.data(0, FOLDER_ROLE) or "", len(dosyalar)))
            self.diff.show_diff(
                "", "Folder selected — %d changed file(s). Pick one to see "
                    "its diff, or use Stage All." % len(dosyalar))
            return
        untracked = bool(item.data(0, UNTRACKED_ROLE))
        self._mode = "worktree"

        # MODEL DOSYALARI ANLAMSAL KARSILASTIRILIR. JSON'un satir farki
        # okunamaz: bir durumu tasimak ilgisiz satirlar uretir, bir durum
        # eklemek onlarca satir olarak cikar. Uretilen C/C++ dosyalarinda
        # metin farki dogru oldugu icin onlara dokunulmaz.
        model = self._model_diff_text(path, staged=staged, untracked=untracked)
        if model is not None:
            metin, add, dele, degisen = model
            self.diff_header.setText(
                "DIFF — %s   +%d −%d ~%d   (model, %s)"
                % (path, add, dele, degisen,
                   "staged" if staged else "unstaged"))
            self.diff.show_diff(metin, "No model changes.")
            # RESIM UZERINDE de goster: metin ozeti neyin degistigini
            # soyler, diyagram NEREDE degistigini gosterir.
            if self.repo is not None:
                tam = os.path.join(self.repo.root, path)
                self.model_diff_requested.emit(
                    os.path.normpath(tam), "staged" if staged else "head")
            return

        try:
            text = self.repo.diff(path, staged=staged, untracked=untracked)
        except GitError as exc:
            text = "* could not read the diff: %s" % exc.message
        add, dele = diff_stats(text)
        self.diff_header.setText("DIFF — %s   +%d −%d   (%s)"
                                 % (path, add, dele,
                                    "staged" if staged
                                    else "unstaged"))
        self.diff.show_diff(text, "No differences.")

    def _model_diff_text(self, path: str, staged: bool = False,
                         untracked: bool = False, sha: Optional[str] = None):
        """Model dosyasi icin (metin, +, -, ~); model degilse ``None``.

        Eski surum git'ten, yeni surum calisma agacindan (yada commit'ten)
        okunur. Okuma basarisiz olursa ``None`` donulur ve cagiran taraf
        metin farkina duser -- anlamsal gorunum bir KOLAYLIKTIR, tek
        bilgi kaynagi degildir.
        """
        if not path.lower().endswith((".usm", ".ucd", ".json")):
            return None
        if self.repo is None:
            return None

        try:
            if sha:
                yeni_metin = self.repo.file_at(sha, path)
                ebeveyn = "%s^" % sha
                try:
                    eski_metin = self.repo.file_at(ebeveyn, path)
                except GitError:
                    eski_metin = ""      # ilk commit: ebeveyn yok
            elif untracked:
                eski_metin = ""
                with open(os.path.join(self.repo.root, path),
                          encoding="utf-8") as fh:
                    yeni_metin = fh.read()
            else:
                eski_metin = self.repo.file_at("HEAD", path) if not staged \
                    else self.repo.file_at("HEAD", path)
                if staged:
                    yeni_metin = self.repo.staged_text(path)
                else:
                    with open(os.path.join(self.repo.root, path),
                              encoding="utf-8") as fh:
                        yeni_metin = fh.read()
        except (GitError, OSError, UnicodeDecodeError):
            return None

        rows = model_diff.diff_for(path, eski_metin, yeni_metin)
        if rows is None:
            return None
        add, dele, degisen = model_diff.summary(rows)
        return model_diff.render(rows), add, dele, degisen

    def _on_commit_selected(self, sha: str) -> None:
        if not sha or self.repo is None:
            return
        self._mode = "commit"
        commit = self.graph.current_commit()
        try:
            files = self.repo.commit_files(sha)
            text = self.repo.diff_commit(sha)
        except GitError as exc:
            self.diff.show_diff("* %s" % exc.message, "")
            return
        add, dele = diff_stats(text)
        subject = commit.subject if commit else sha[:8]

        # Commit YALNIZCA model dosyalari tasiyorsa anlamsal gorunum
        # verilir; kod ve model karisiksa metin farki tek ve tutarli bir
        # goruntu sundugu icin ona dokunulmaz.
        model_yollari = [f.path for f in files
                         if f.path.lower().endswith((".usm", ".ucd"))]
        if model_yollari and len(model_yollari) == len(files):
            parcalar, ta, td, tm = [], 0, 0, 0
            for yol in model_yollari:
                sonuc = self._model_diff_text(yol, sha=sha)
                if sonuc is None:
                    parcalar = []
                    break
                metin, a, d, m = sonuc
                parcalar.append("### %s\n%s" % (yol, metin or "  (no model changes)"))
                ta, td, tm = ta + a, td + d, tm + m
            if parcalar:
                self.diff_header.setText(
                    "DIFF — %s  %s   %d model  +%d −%d ~%d"
                    % (commit.short if commit else sha[:8], subject,
                       len(model_yollari), ta, td, tm))
                self.diff.show_diff("\n\n".join(parcalar), "This commit is empty.")
                return

        self.diff_header.setText("DIFF — %s  %s   %d file(s)  +%d −%d"
                                 % (commit.short if commit else sha[:8],
                                    subject, len(files), add, dele))
        self.diff.show_diff(text, "This commit is empty.")

    # ------------------------------------------------------------------ islemler

    def _guard(self) -> bool:
        return self.repo is not None and self.repo.is_repo()

    def _try(self, action: Callable[[], None], label: str) -> None:
        try:
            action()
        except GitError as exc:
            QMessageBox.critical(self, label, exc.message)
            return
        self.refresh()

    def _init_repo(self) -> None:
        if self.repo is None:
            QMessageBox.information(self, "Could not initialise the repository",
                                    "Select a workspace first.")
            return
        self._try(self.repo.init, "Could not initialise the repository")

    def _stage_all(self) -> None:
        if not self._guard():
            return
        self._try(self.repo.stage_all, "Could not stage")

    def _stage_selected(self) -> None:
        if not self._guard():
            return
        paths = self._selected_paths(self.list_unstaged)
        if not paths:
            return
        self._try(lambda: self.repo.stage(paths), "Could not stage")

    def _unstage_selected(self) -> None:
        if not self._guard():
            return
        paths = self._selected_paths(self.list_staged)
        if not paths:
            return
        self._try(lambda: self.repo.unstage(paths), "Could not unstage")

    def _discard_selected(self) -> None:
        if not self._guard():
            return
        paths = self._selected_paths(self.list_unstaged)
        if not paths:
            return
        reply = QMessageBox.warning(
            self, "Discard changes",
            "Changes in these files will be lost PERMANENTLY:\n\n  %s"
            % "\n  ".join(paths[:12]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._try(lambda: self.repo.discard(paths), "Could not discard")

    def _commit(self) -> None:
        if not self._guard():
            return
        try:
            status = self.repo.status()
        except GitError as exc:
            QMessageBox.critical(self, "Commit", exc.message)
            return
        staged = status.staged()
        if not staged:
            QMessageBox.information(self, "Commit",
                                    "Nothing is staged.")
            return
        name, email = self.repo.identity()
        if not name or not email:
            QMessageBox.information(
                self, "Git identity missing",
                "A git identity is required to commit:\n\n"
                "  git config --global user.name \"Your Name\"\n"
                "  git config --global user.email \"you@example.com\"")
            return
        dlg = CommitDialog(len(staged), self.repo.has_commits(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        message, amend = dlg.message(), dlg.amend()
        self._try(lambda: self.repo.commit(message, amend=amend), "Commit")

    def _branch_menu(self) -> None:
        if not self._guard():
            return
        try:
            branches = self.repo.branches()
            current = self.repo.status().branch
        except GitError as exc:
            QMessageBox.critical(self, "Branches", exc.message)
            return
        options = ["+ Create new branch…"] + branches
        start = options.index(current) if current in options else 0
        choice, ok = QInputDialog.getItem(self, "Branch", "Pick a branch:", options,
                                          start, False)
        if not ok or not choice:
            return
        if choice.startswith("+ "):
            name, ok2 = QInputDialog.getText(self, "New branch", "Branch name:")
            if not ok2 or not name.strip():
                return
            self._try(lambda: self.repo.create_branch(name.strip()),
                      "Could not create the branch")
            return
        if choice == current:
            return
        self._try(lambda: self.repo.checkout(choice), "Could not switch branch")

    # ------------------------------------------------------------------ ag

    def _network(self, task: Callable[[], str], label: str) -> None:
        if not self._guard():
            return
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, label,
                                    "Another network operation is running.")
            return
        self._set_enabled(False)
        self._notice("%s in progress…" % label)

        worker = GitWorker(task, self)

        def finished(text: str) -> None:
            self._notice("")
            self.refresh()
            if text:
                QMessageBox.information(self, label, text[:2000])

        def failed(message: str) -> None:
            self._notice("")
            self.refresh()
            QMessageBox.critical(self, label, message[:2000])

        worker.done.connect(finished)
        worker.failed.connect(failed)
        worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker = worker
        worker.start()

    def _fetch(self) -> None:
        self._network(lambda: self.repo.fetch(), "Fetch")

    def _pull(self) -> None:
        self._network(lambda: self.repo.pull(), "Pull")

    def _push(self) -> None:
        if not self._guard():
            return
        try:
            status = self.repo.status()
            remotes = self.repo.remotes()
        except GitError as exc:
            QMessageBox.critical(self, "Push", exc.message)
            return
        if not remotes:
            QMessageBox.information(
                self, "Push",
                "No remote is configured:\n\n"
                "  git remote add origin <url>")
            return
        first_push = not status.upstream and not status.detached
        branch = status.branch if first_push else ""
        remote = remotes[0] if first_push else ""
        self._network(
            lambda: self.repo.push(remote=remote, branch=branch,
                                   set_upstream=first_push), "Push")

    # ------------------------------------------------------------------ kapanis

    def shutdown(self) -> None:
        """Pencere kapanirken bekleyen ag islemini sonlandirir."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)


# ==================================================================== yardimcilar

def _header(title: str) -> QLabel:
    lbl = QLabel(title)
    f = ui_font(8)
    f.setBold(True)
    f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.0)
    lbl.setFont(f)
    lbl.setContentsMargins(10, 6, 10, 6)
    lbl.setStyleSheet("background: %s; color: %s; border-bottom: 1px solid %s;"
                      % (C.PANEL_DARK, C.TEXT_DIM, C.BORDER))
    return lbl


def _dosyalari(item: QTreeWidgetItem) -> List[str]:
    """Dugumun (ve altindakilerin) DOSYA yollari."""
    yol = item.data(0, PATH_ROLE)
    if yol:
        return [yol]
    out: List[str] = []
    for i in range(item.childCount()):
        out += _dosyalari(item.child(i))
    return out


def _build_tree(agac: QTreeWidget, kayitlar) -> None:
    """Dosya kayitlarini KLASOR AGACI olarak yerlestirir.

    Tek dosyali ara klasorler BIRLESTIRILIR ("app/ui/" gibi): her seviye
    icin ayri satir acmak, uc dosyalik bir degisikligi on satirlik bir
    agaca cevirirdi.
    """
    kokler: dict = {}

    def klasor(parcalar) -> QTreeWidgetItem:
        anahtar = "/".join(parcalar)
        if anahtar in kokler:
            return kokler[anahtar]
        if len(parcalar) == 1:
            dugum = QTreeWidgetItem(agac, [parcalar[0] + "/"])
        else:
            dugum = QTreeWidgetItem(klasor(parcalar[:-1]), [parcalar[-1] + "/"])
        dugum.setData(0, FOLDER_ROLE, anahtar)
        dugum.setForeground(0, QBrush(QColor(C.TEXT_DIM)))
        dugum.setExpanded(True)
        kokler[anahtar] = dugum
        return dugum

    for gf, colour in kayitlar:
        parcalar = gf.path.split("/")
        ad = parcalar[-1]
        ust = klasor(parcalar[:-1]) if len(parcalar) > 1 else agac
        metin = "%s  %s" % (gf.label(), ad)
        if gf.orig_path:
            metin += "   ← %s" % gf.orig_path
        oge = QTreeWidgetItem(ust, [metin])
        oge.setData(0, PATH_ROLE, gf.path)
        oge.setData(0, UNTRACKED_ROLE, gf.untracked)
        oge.setForeground(0, QBrush(QColor(colour)))
        oge.setToolTip(0, gf.path)

    # Klasor satirinda kac dosya oldugunu yaz: kapaliyken de bilgi versin.
    for dugum in kokler.values():
        sayi = len(_dosyalari(dugum))
        dugum.setText(0, "%s   (%d)" % (dugum.text(0), sayi))


def _expanded_folders(agac: QTreeWidget) -> set:
    """Su an ACIK olan klasor anahtarlari."""
    acik = set()

    def gez(dugum):
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            anahtar = cocuk.data(0, FOLDER_ROLE)
            if anahtar and cocuk.isExpanded():
                acik.add(anahtar)
            gez(cocuk)

    gez(agac.invisibleRootItem())
    return acik


def _restore_expanded(agac: QTreeWidget, acik: set) -> None:
    if not acik:
        return

    def gez(dugum):
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            anahtar = cocuk.data(0, FOLDER_ROLE)
            if anahtar:
                cocuk.setExpanded(anahtar in acik)
            gez(cocuk)

    gez(agac.invisibleRootItem())


def _current_path(lst: QTreeWidget) -> str:
    item = lst.currentItem()
    return (item.data(0, PATH_ROLE) or "") if item else ""


def _restore_path(lst: QTreeWidget, path: str) -> None:
    """Yenilemeden sonra ayni dosyayi yeniden secer."""
    if not path:
        return

    def gez(dugum):
        for i in range(dugum.childCount()):
            cocuk = dugum.child(i)
            if cocuk.data(0, PATH_ROLE) == path:
                lst.setCurrentItem(cocuk)
                return True
            if gez(cocuk):
                return True
        return False

    gez(lst.invisibleRootItem())