"""A PDF reader that shows the OMG UML 2.5.1 specification IN A SEPARATE WINDOW.

The tool already writes the clause and page number of the specification next
to every validation finding (see app/core/uml_spec.py). This window makes
those references FOLLOWABLE: the user can go from the finding to the document.

WHY THE PDF IS NOT EMBEDDED IN THE PACKAGE
------------------------------------------
The document is under OMG's copyright; it may be DOWNLOADED freely but not
redistributed. Embedded in the application's EXE, every copy delivered to a
customer would be a redistribution. The file is also 18 MB and most users
never look at it.

Instead: the file is opened when it exists LOCALLY; otherwise the user is
asked and, WITH THEIR CONSENT, it is downloaded from the official address to
their own machine. The downloaded copy lives in the application data folder,
"""

from __future__ import annotations

import bisect
import os
import urllib.request
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (QEventLoop, QMutex, QRectF, QSize, QSizeF,
                          QStandardPaths, Qt, QThread, QTimer, QUrl,
                          QWaitCondition, pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QGuiApplication,
                         QImage, QPainter)
from PyQt6.QtWidgets import (QAbstractScrollArea, QHBoxLayout, QLabel,
                             QMainWindow, QMessageBox, QProgressDialog,
                             QSpinBox, QToolBar, QVBoxLayout, QWidget)

from .theme import C, ui_font

#: The official download address (OMG). As of 2026-09: application/pdf, ~18 MB.
SPEC_URL = "https://www.omg.org/spec/UML/2.5.1/PDF"
#: The human-readable address of the document (opens in a browser).
SPEC_PAGE = "https://www.omg.org/spec/UML/"
#: The file name of the local copy.
SPEC_FILE = "OMG-UML-2.5.1.pdf"

#: The identifiers the interface uses (see main_window.APP_NAME / SETTINGS_ORG).
#:
#: REPEATED HERE, because `main_window` imports this module; importing the
#: other way round would be a circular dependency. A regression test verifies
#: that the two stay the same, so the values cannot drift apart.
GUI_ORG = "UmlDesignStudio"
GUI_APP = "UML Design Studio"


def spec_pdf_path() -> str:
    """The full path where the local copy is (or will be).

    The application DATA folder is used; the installation folder may be
    read-only and writing next to the EXE may require administrator rights.
    """
    from PyQt6.QtCore import QCoreApplication

    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not root:
        root = os.path.expanduser("~")
    # AppDataLocation already includes the application name WHEN IT IS SET
    # (see main_window.run -> setApplicationName). When it is not set (tests,
    # running a script directly) the path is built from the name of the
    # interpreter; in that case we append our own folder.
    if not QCoreApplication.applicationName():
        root = os.path.join(root, "UML-Design-Studio")
    return os.path.join(root, SPEC_FILE)


def bundled_spec_path() -> Optional[str]:
    """Finds the copy the user put next to the application, if any."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for aday in (os.path.join(root, "docs", SPEC_FILE),
                 os.path.join(root, SPEC_FILE)):
        if os.path.isfile(aday):
            return aday
    return None


def spec_pdf_candidates() -> List[str]:
    """EVERY path the PDF may be found at, in order of priority.

    THE INTERFACE AND THE SCRIPTS MUST SEE THE SAME FILE.

    THE BUG WE HIT: `QStandardPaths.AppDataLocation` includes the application
    name WHEN IT IS SET. The interface sets it in `main_window.run()` and the
    file lands under `.../Roaming/UmlDesignStudio/UML Design Studio/`. A script
    (the tests under tools/, the release tools) does not set that name, so the
    same call returned ANOTHER folder and a PDF the interface opened fine
    counted as "not found". Both spellings are tried.
    """
    adaylar: List[str] = []
    yanindaki = bundled_spec_path()
    if yanindaki:
        adaylar.append(yanindaki)
    adaylar.append(spec_pdf_path())

    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if root:
        adaylar.append(os.path.join(root, GUI_ORG, GUI_APP, SPEC_FILE))
        adaylar.append(os.path.join(root, "UML-Design-Studio", SPEC_FILE))

    benzersiz: List[str] = []
    for yol in adaylar:
        normal = os.path.normpath(yol)
        if normal not in benzersiz:
            benzersiz.append(normal)
    return benzersiz


def find_spec_pdf() -> Optional[str]:
    """The path of the local PDF, if there is one."""
    for yol in spec_pdf_candidates():
        if os.path.isfile(yol):
            return yol
    return None


class _Downloader(QThread):
    """Downloads the PDF ON A SEPARATE THREAD so the interface does not freeze.

    THE RESULT TRAVELS IN FIELDS, NOT THROUGH A SIGNAL.

    THE BUG WE HIT: the result was sent with a `finished_ok` signal. A signal is
    delivered from the thread to the main thread THROUGH A QUEUE, while the
    waiting side left its loop as soon as `isRunning()` went false. There was a
    race between the two events, and when the main window was busy (timers, the
    git thread) the race WAS LOST: the file downloaded completely, but the
    caller saw "no result" and returned None -- the PDF arrived and the window
    did not open. Reading plain fields has no race.
    """

    progress = pyqtSignal(int, int)          # (bytes downloaded, total bytes)

    def __init__(self, target: str, parent=None) -> None:
        super().__init__(parent)
        self.target = target
        #: Filled in when run() finishes; THE MAIN THREAD reads these.
        self.result_path = ""
        self.result_error = ""
        self.cancelled = False
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:                   # pragma: no cover - thread
        temp = self.target + ".part"
        try:
            os.makedirs(os.path.dirname(self.target), exist_ok=True)
            istek = urllib.request.Request(
                SPEC_URL, headers={"User-Agent": "UML-Design-Studio"})
            with urllib.request.urlopen(istek, timeout=30) as answer:
                total = int(answer.headers.get("Content-Length") or 0)
                inen = 0
                # Progress is reported SPARSELY. Emitting a signal on every 64 KB chunk
                # meant more than 280 GUI updates for 18 MB; and because a modal
                # QProgressDialog.setValue() processes events itself, that produced deep
                # and pointless re-entrancy.
                # uretiyordu.
                step = max(64 * 1024, (total // 100) if total else 0)
                last_report = 0
                with open(temp, "wb") as fh:
                    while True:
                        if self._cancel:
                            raise InterruptedError("cancelled")
                        part = answer.read(64 * 1024)
                        if not part:
                            break
                        fh.write(part)
                        inen += len(part)
                        if inen - last_report >= step:
                            last_report = inen
                            self.progress.emit(inen, total)
                self.progress.emit(inen, total)
                if total and inen != total:
                    raise IOError(
                        "incomplete download: %d of %d bytes" % (inen, total))
            # A half-written file is NEVER put at the destination: the next start-up
            # must not take it for valid and show a corrupt PDF.
            os.replace(temp, self.target)
            self.result_path = self.target
        except InterruptedError:
            self.cancelled = True
            self._clear(temp)
        except Exception as exc:             # noqa: BLE001
            self.result_error = str(exc)
            self._clear(temp)

    @staticmethod
    def _clear(yol: str) -> None:
        try:
            if os.path.exists(yol):
                os.remove(yol)
        except OSError:
            pass


def ensure_spec_pdf(parent: QWidget) -> Optional[str]:
    """Returns the local PDF; downloads it AFTER ASKING THE USER if absent.

    The download DOES NOT START by itself. A desktop application pulling 18 MB
    off the internet without asking is not acceptable; and on a corporate
    network, outbound access may be blocked.
    """
    var = find_spec_pdf()
    if var:
        return var

    target = spec_pdf_path()
    box = QMessageBox(parent)
    box.setWindowTitle("UML 2.5.1 specification")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText("The specification PDF is not on this computer yet.")
    box.setInformativeText(
        "It can be downloaded from the official OMG address:\n\n"
        "    %s\n\n"
        "About 18 MB. It is stored only on this computer:\n\n"
        "    %s\n\n"
        "The document is copyrighted by OMG and is therefore not shipped "
        "with this application." % (SPEC_URL, target))
    download = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
    tarayici = box.addButton("Open in browser",
                              QMessageBox.ButtonRole.ActionRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.exec()

    if box.clickedButton() is tarayici:
        QDesktopServices.openUrl(QUrl(SPEC_PAGE))
        return None
    if box.clickedButton() is not download:
        return None

    ilerleme = QProgressDialog("Downloading the UML 2.5.1 specification…",
                               "Cancel", 0, 100, parent)
    ilerleme.setWindowTitle("Download")
    ilerleme.setWindowModality(Qt.WindowModality.WindowModal)
    ilerleme.setMinimumDuration(0)
    ilerleme.setValue(0)

    worker = _Downloader(target, parent)

    def step(inen: int, total: int) -> None:
        if total > 0:
            ilerleme.setMaximum(total)
            ilerleme.setValue(inen)
        ilerleme.setLabelText(
            "Downloading the UML 2.5.1 specification…  %.1f MB"
            % (inen / (1024.0 * 1024.0)))

    worker.progress.connect(step)
    ilerleme.canceled.connect(worker.cancel)

    # WAITING: a local event loop INSTEAD OF a manual `processEvents()` loop.
    # Spinning by hand made the whole application re-entrant for the duration
    # of the download (timers, redraws, the git thread), and the exit
    # condition of the loop RACED with the signal delivery.
    dongu = QEventLoop()
    worker.finished.connect(dongu.quit)
    worker.start()
    if worker.isRunning():
        dongu.exec()
    # Make sure the thread has REALLY finished; only then are the fields safe
    # to read.
    worker.wait()
    ilerleme.close()

    if worker.cancelled:
        return None
    if worker.result_error:
        QMessageBox.critical(
            parent, "Download failed",
            "The specification could not be downloaded:\n\n%s\n\n"
            "You can fetch it manually from %s and place it next to the "
            "application as docs/%s."
            % (worker.result_error, SPEC_PAGE, SPEC_FILE))
        return None
    if worker.result_path and os.path.isfile(worker.result_path):
        return worker.result_path
    # Falling through here is not expected; still, say so rather than stay silent.
    QMessageBox.critical(
        parent, "Download failed",
        "The download finished but the file could not be found at:\n\n%s"
        % target)
    return None


class _PageRenderer(QThread):
    """Renders the pages ON A SEPARATE THREAD.

    Why our own thread: pdfium has to open a page ON FIRST ACCESS and in this
    document that takes 30-40 ms per page. Done on the main thread, the
    interface would stutter that much every time a new page came into view.
    The measurements:

        render(page)          7-38 ms
        pagePointSize(page)   shares the same load

    Measuring and rendering happen TOGETHER here, so a page is loaded once.

    QPdfDocument is safe for this use: Qt's own QPdfPageRenderer class also
    uses the document from a worker thread. It was measured as well -- 120 page
    measurements were taken in the background while 40 pages were rendered on
    the main thread, with no crash and correct values.
    """

    #: (page, pixel width, image, width_pt, height_pt)
    ready = pyqtSignal(int, int, QImage, float, float)

    #: At most this many requests are held in the queue.
    QUEUE_LIMIT = 24

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._kilit = QMutex()
        self._uyandir = QWaitCondition()
        self._queue: List[Tuple[int, int, int]] = []
        self._dur = False

    def request_page(self, page: int, genislik: int, yukseklik: int) -> List[int]:
        """Queues a page.

        Returns the page numbers of the requests that were DROPPED. The caller
        MUST remove those from its own "pending" list: otherwise a request that
        hit the queue limit and was dropped would count as pending forever and
        that page would never be drawn again.
        """
        self._kilit.lock()
        try:
            self._queue = [x for x in self._queue if x[0] != page]
            self._queue.append((page, genislik, yukseklik))
            dusen: List[int] = []
            if len(self._queue) > self.QUEUE_LIMIT:
                fazla = self._queue[:-self.QUEUE_LIMIT]
                dusen = [x[0] for x in fazla]
                del self._queue[:-self.QUEUE_LIMIT]
            self._uyandir.wakeOne()
            return dusen
        finally:
            self._kilit.unlock()

    def durdur(self) -> None:
        """Ends the thread. It must be called BEFORE the document is destroyed."""
        self._kilit.lock()
        try:
            self._dur = True
            self._queue = []
            self._uyandir.wakeAll()
        finally:
            self._kilit.unlock()
        self.wait(5000)

    def run(self) -> None:                     # pragma: no cover - thread
        while True:
            self._kilit.lock()
            while not self._queue and not self._dur:
                self._uyandir.wait(self._kilit)
            if self._dur:
                self._kilit.unlock()
                return
            # LIFO: the page requested LAST is rendered first. When the user scrolls
            # quickly, the page being looked at now takes priority over the older ones
            # waiting in the queue.
            page, genislik, yukseklik = self._queue.pop()
            self._kilit.unlock()
            try:
                measure = self._belge.pagePointSize(page)
                if measure.width() <= 0.0 or measure.height() <= 0.0:
                    continue
                image = self._belge.render(
                    page, QSize(max(1, genislik), max(1, yukseklik)))
                self.ready.emit(page, genislik, image,
                                measure.width(), measure.height())
            except Exception:                  # noqa: BLE001
                continue


class ContinuousPdfView(QAbstractScrollArea):
    """A view that shows the WHOLE document as one continuous strip.

    WHY QPdfView IS NOT USED
    -----------------------
    Qt's ready-made view offers two options and neither fits this document:
    uymuyor:

      * SinglePage  -- the scroll bar covers ONLY the current page; navigating
                       796 pages is impossible.
      * MultiPage   -- it computes the layout of the whole document UP FRONT,
                       calling pagePointSize() for every page. Measured: 47 804
                       ms from file and 22 848 ms even when loaded from memory
                       -- all on the main thread, so the application FREEZES
                       for more than half a minute.

    This view does neither: it estimates the layout from the size of the FIRST
    page (a single cheap call), spreads the scroll bar over the whole document,
    and renders ONLY the visible pages, and those on a separate thread. The real
    size of a page is learned when it is rendered; when it differs from the
    estimate the layout corrects itself.

    Zooming lives here too: the scale is kept as "pixels per point" and the
    pages are re-rendered at every scale (a resampled image is never scaled up),
    so the text stays sharp at every zoom level.
    """

    #: When the current page changes (0-based).
    page_changed = pyqtSignal(int)
    #: When the zoom ratio changes (1.0 = 100%).
    scale_changed = pyqtSignal(float)

    GAP = 14                                # the gap between pages
    MARGIN = 12                                 # the margin of the strip
    ZOOM_MIN = 0.20
    ZOOM_MAX = 4.00
    ZOOM_ADIM = 1.25
    #: The upper pixel bound of a single page image (edge and total).
    #: At 400% zoom an A4 is 3173x4224 pixels, that is 53 MB; the area bound
    #: brings it down to 32 MB. The loss shows only at the most extreme zoom,
    #: and even there it stays above 300 dots per inch per page.
    PIKSEL_SINIRI = 4000
    AREA_LIMIT = 8_000_000
    #: The target upper bound of the rendered page cache. An A4 fitted to the
    #: width takes about 4.7 MB.
    ONBELLEK_BAYT = 48 * 1024 * 1024
    #: How long to wait after scrolling stops before requesting a render (ms).
    REQUEST_DELAY = 90
    #: One mouse wheel notch (Qt units).
    CENT = 120

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._count = belge.pageCount()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.viewport().setAutoFillBackground(False)

        # Size information: only the FIRST page is read up front (a single call).
        # The others are learned as they are rendered; until then this estimate holds.
        first = belge.pagePointSize(0) if self._count else QSizeF(595.0, 792.0)
        if first.width() <= 0.0 or first.height() <= 0.0:
            first = QSizeF(595.0, 792.0)
        self._default = (first.width(), first.height())
        self._pt: Dict[int, Tuple[float, float]] = {}

        self._temel = max(1.0, float(self.logicalDpiX())) / 72.0
        self._zoom = 1.0
        self._scale = self._temel
        self._mode = "genislik"

        self._w: List[float] = []
        self._h: List[float] = []
        self._y: List[float] = []
        self._bottom: List[float] = []
        self._total = 0.0
        self._current = 0
        self._tekerlek = 0

        self._onbellek: "OrderedDict[int, Tuple[int, int, QImage]]" = \
            OrderedDict()
        self._pending: Dict[int, Tuple[int, int]] = {}

        # While scrolling fast NO render is requested; see _delayed_request.
        self._hizli = False
        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(self.REQUEST_DELAY)
        self._request_timer.timeout.connect(self._delayed_request)

        self._worker = _PageRenderer(belge, self)
        self._worker.ready.connect(self._cizildi)
        self._worker.start()

        self._layout()
        self._update_bars()

    # life cycle

    def shutdown(self) -> None:
        """Stops the worker thread and releases the memory.

        It must be called BEFORE the document is destroyed: the worker may still
        be inside pdfium. The render cache is emptied here too; images holding
        tens of megabytes must not linger behind a closed window.
        """
        self._request_timer.stop()
        if self._worker is not None:
            self._worker.durdur()
            self._worker = None
        self._onbellek.clear()
        self._pending.clear()

    # layout

    def _measure(self, page: int) -> Tuple[float, float]:
        return self._pt.get(page, self._default)

    def _layout(self) -> None:
        """Recomputes the page rectangles (796 items: microseconds).

        The sizes are rounded TO WHOLE PIXELS. A fractional target rectangle
        takes the rendered image out of Qt's 1:1 blit path and into resampling;
        measured: edge contrast dropped to 65% and the text looked permanently
        soft. Whole pixels are the precondition of "the text stays sharp at
        every zoom level".
        """
        self._w = []
        self._h = []
        self._y = []
        self._bottom = []
        y = float(self.MARGIN)
        for i in range(self._count):
            wpt, hpt = self._measure(i)
            w = float(max(1, int(round(wpt * self._scale))))
            h = float(max(1, int(round(hpt * self._scale))))
            self._w.append(w)
            self._h.append(h)
            self._y.append(y)
            self._bottom.append(y + h)
            y += h + self.GAP
        self._total = (y - self.GAP + self.MARGIN) if self._count else 0.0

    def _update_bars(self) -> None:
        measure = self.viewport().size()
        dikey = max(0, int(round(self._total - measure.height())))
        cubuk = self.verticalScrollBar()
        cubuk.setRange(0, dikey)
        cubuk.setPageStep(max(1, measure.height() - 24))
        cubuk.setSingleStep(max(1, measure.height() // 12))

        content = (max(self._w) if self._w else 0.0) + 2 * self.MARGIN
        yatay = max(0, int(round(content - measure.width())))
        ycubuk = self.horizontalScrollBar()
        ycubuk.setRange(0, yatay)
        ycubuk.setPageStep(max(1, measure.width()))
        ycubuk.setSingleStep(max(1, measure.width() // 12))

    def _page_x(self, genislik: float) -> float:
        visible = self.viewport().width()
        if genislik + 2 * self.MARGIN <= visible:
            return float(int((visible - genislik) / 2.0))
        return float(self.MARGIN - self.horizontalScrollBar().value())

    # anchor

    def _capa_al(self) -> Tuple[int, float]:
        """Records the point being looked at, relative to the page, before a scale change."""
        deger = float(self.verticalScrollBar().value())
        page = self._page_at(deger)
        yukseklik = self._h[page] if self._h else 1.0
        if yukseklik <= 0.0:
            return (page, 0.0)
        return (page, (deger - self._y[page]) / yukseklik)

    def _capa_uygula(self, capa: Tuple[int, float]) -> None:
        page, oran = capa
        if not self._y:
            return
        page = max(0, min(page, self._count - 1))
        target = self._y[page] + oran * self._h[page]
        self.verticalScrollBar().setValue(int(round(target)))

    def _page_at(self, y: float) -> int:
        if not self._bottom:
            return 0
        i = bisect.bisect_right(self._bottom, y)
        return max(0, min(i, self._count - 1))

    # navigation

    def current_page(self) -> int:
        return self._current

    def page_count(self) -> int:
        return self._count

    def goto(self, page: int) -> None:
        """Goes to the requested page and makes it the CURRENT page.

        The "page covering the most area", derived from the scroll position, IS
        NOT ENOUGH here. When a page near the end of the document is requested,
        the scroll bar cannot go further down; the requested page comes into
        view but cannot reach the top. The derived value then points at another
        page and the user types 9 into the page box and sees 12. When the
        requested page IS REALLY VISIBLE, the request always wins.
        """
        if not self._y:
            return
        page = max(0, min(page, self._count - 1))
        self.verticalScrollBar().setValue(
            int(round(self._y[page] - self.MARGIN)))
        first, sonu = self._visible_range()
        if first <= page <= sonu and self._current != page:
            self._current = page
            self.page_changed.emit(page)

    def _update_current(self) -> None:
        """The current page: the page COVERING THE MOST AREA in the view.

        A fixed probe -- "the page in the top third of the view", say -- cannot
        be used. Zoomed far out, five or six pages fit in the view and that
        point falls not on the page being looked at but a few below it.
        MEASURED: in a 1453 pixel high window at 20% zoom, going to page 16 made
        the counter say 18; the user typed 16 into the page box and saw 18.

        A TIE normally goes to the TOPMOST page, so that goto(n) returns n at
        every zoom. The one exception is the END of the document: there the
        scroll bar can go no further, so the last page can never reach the top,
        and breaking the tie in favour of the upper page would make THE LAST
        PAGE unselectable (measured: on a 400 page document the counter said 399
        while the last page filled the screen). With the bar at the end, a tie
        goes to the BOTTOMMOST fully visible page.

        """
        if not self._bottom:
            return
        cubuk = self.verticalScrollBar()
        top = float(cubuk.value())
        bottom = top + self.viewport().height()
        sonda = cubuk.value() >= cubuk.maximum() and cubuk.maximum() > 0
        first, sonu = self._visible_range()
        # The scan is bounded: at the smallest scale many pages can fit in the
        # view, but they are all fully visible and therefore already tied.
        sonu = min(sonu, first + 64)
        en_iyi = first
        en_cok = -1.0
        for i in range(first, sonu + 1):
            share = min(self._bottom[i], bottom) - max(self._y[i], top)
            if sonda:
                if share >= en_cok - 0.5:
                    en_cok = max(en_cok, share)
                    en_iyi = i
            elif share > en_cok + 0.5:
                en_cok = share
                en_iyi = i
        if en_iyi != self._current:
            self._current = en_iyi
            self.page_changed.emit(en_iyi)

    # zoom

    def zoom_factor(self) -> float:
        return self._zoom

    def fit_mode(self) -> str:
        return self._mode

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * self.ZOOM_ADIM)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / self.ZOOM_ADIM)

    def set_zoom(self, zoom: float, mode: str = "serbest") -> None:
        zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, zoom))
        if abs(zoom - self._zoom) < 1e-6 and mode == self._mode:
            return
        capa = self._capa_al()
        self._mode = mode
        self._zoom = zoom
        self._scale = self._temel * zoom
        self._layout()
        self._update_bars()
        self._capa_uygula(capa)
        # Applying the anchor produces a scroll; but that is not a scroll the user
        # dragged. The pages at the new scale must be requested IMMEDIATELY.
        self._hizli = False
        self._update_current()
        self.scale_changed.emit(self._zoom)
        self.viewport().update()

    def fit_width(self) -> None:
        self._fit("genislik")

    def fit_page(self) -> None:
        self._fit("sayfa")

    def _fit(self, mode: str) -> None:
        wpt, hpt = self._measure(self._current)
        if wpt <= 0.0 or hpt <= 0.0:
            return
        # A 1 pixel margin: rounding must not bring up a horizontal bar and change
        # the view width, or fitting would oscillate.
        field_w = max(1.0, self.viewport().width() - 2.0 * self.MARGIN - 1.0)
        scale = field_w / wpt
        if mode == "sayfa":
            field_y = max(1.0, self.viewport().height() - 2.0 * self.MARGIN)
            scale = min(scale, field_y / hpt)
        self.set_zoom(scale / self._temel, mode)

    # painting

    def paintEvent(self, event) -> None:        # noqa: N802 - Qt naming
        boyaci = QPainter(self.viewport())
        boyaci.fillRect(self.viewport().rect(), QColor(C.PANEL_DARK))
        if not self._count:
            return
        boyaci.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        top = float(self.verticalScrollBar().value())
        first, sonu = self._visible_range()

        kagit = QColor("#FFFFFF")
        cerceve = QColor(C.BORDER_LIGHT)
        soluk = QColor(C.TEXT_DIM)
        for i in range(first, sonu + 1):
            target = QRectF(self._page_x(self._w[i]), self._y[i] - top,
                           self._w[i], self._h[i])
            boyaci.fillRect(target, kagit)
            girdi = self._onbellek.get(i)
            if girdi is not None:
                boyaci.drawImage(target, girdi[2])
                self._onbellek.move_to_end(i)
            else:
                boyaci.setPen(soluk)
                boyaci.setFont(ui_font(9))
                boyaci.drawText(target, Qt.AlignmentFlag.AlignCenter,
                                "Page %d" % (i + 1))
            boyaci.setPen(cerceve)
            boyaci.drawRect(target)
        boyaci.end()

        # NO RENDER IS REQUESTED WHILE SCROLLING FAST.
        #
        # As long as the user drags the scroll bar through the document, requesting
        # a page for every intermediate position produces dozens of renders none of
        # which will be displayed. Measured: during such a drag a single scroll step
        # reached 225 ms, and almost all of the cost was in producing the requests.
        # The requests are issued AFTER the scrolling STOPS; until then the pages
        # show as placeholders.
        if self._hizli:
            self._request_timer.start()
        else:
            ilk2, sonu2 = self._request_range()
            self._refresh_requests(ilk2, sonu2)

    def _visible_range(self) -> Tuple[int, int]:
        """The first and last page (even partly) present in the view."""
        if not self._bottom:
            return (0, 0)
        top = float(self.verticalScrollBar().value())
        bottom = top + self.viewport().height()
        first = max(0, bisect.bisect_right(self._bottom, top))
        first = min(first, self._count - 1)
        sonu = min(self._count - 1, bisect.bisect_left(self._y, bottom) - 1)
        if sonu < first:
            sonu = first
        return (first, sonu)

    def _request_range(self) -> Tuple[int, int]:
        """The range of pages to request for rendering.

        One before and one after the visible ones are requested too, so a page
        does not show as an empty frame during slow scrolling. But this
        PREFETCH only happens when the budget allows: zoomed far in, a single
        page image takes tens of megabytes, and requesting its neighbours too
        would blow the cache and start an evict-and-redraw loop.
        """
        first, sonu = self._visible_range()
        tahmin = self._tahmini_bayt(self._current)
        if tahmin * (sonu - first + 3) <= self.ONBELLEK_BAYT:
            first = max(0, first - 1)
            sonu = min(self._count - 1, sonu + 1)
        return (first, sonu)

    def _pixel_size(self, page: int) -> Tuple[int, int]:
        """The REAL pixel size the page will be requested at."""
        oran = self.devicePixelRatioF() or 1.0
        g = max(1, int(round(self._w[page] * oran)))
        y = max(1, int(round(self._h[page] * oran)))
        if g > self.PIKSEL_SINIRI:
            y = max(1, int(round(y * self.PIKSEL_SINIRI / float(g))))
            g = self.PIKSEL_SINIRI
        if g * y > self.AREA_LIMIT:
            kucult = (self.AREA_LIMIT / float(g * y)) ** 0.5
            g = max(1, int(g * kucult))
            y = max(1, int(y * kucult))
        return (g, y)

    def _tahmini_bayt(self, page: int) -> int:
        if not self._w:
            return 0
        page = max(0, min(page, self._count - 1))
        g, y = self._pixel_size(page)
        return max(1, g * y * 4)

    def _refresh_requests(self, first: int, sonu: int) -> None:
        # Pending requests that have left the view are cleared.
        for page in list(self._pending):
            if page < first or page > sonu:
                del self._pending[page]
        for page in range(first, sonu + 1):
            gerek = self._pixel_size(page)
            girdi = self._onbellek.get(page)
            if girdi is not None and (girdi[0], girdi[1]) == gerek:
                continue
            if self._pending.get(page) == gerek:
                continue
            self._pending[page] = gerek
            if self._worker is not None:
                for dusen in self._worker.request_page(page, gerek[0], gerek[1]):
                    # A request DROPPED from the queue never comes back; without removing
                    # the pending mark, that page would stay a placeholder forever.
                    # tutucu olarak kalirdi.
                    self._pending.pop(dusen, None)

    def _cizildi(self, page: int, genislik: int, image: QImage,
                 wpt: float, hpt: float) -> None:
        pending = self._pending.get(page)
        if pending is not None and pending[0] == genislik:
            del self._pending[page]
        if image.isNull():
            return
        self._onbellege_koy(page, genislik, image.height(), image)

        # When the real size differs from the estimate the layout is corrected.
        # Because every page in this document is the same size it normally never
        # runs; on a document with mixed sizes the view pulls itself together.
        # toparlar.
        previous = self._pt.get(page)
        self._pt[page] = (wpt, hpt)
        if previous is None and (abs(wpt - self._default[0]) > 0.5
                               or abs(hpt - self._default[1]) > 0.5):
            capa = self._capa_al()
            self._layout()
            self._update_bars()
            self._capa_uygula(capa)
        self.viewport().update()

    def _onbellege_koy(self, page: int, genislik: int, yukseklik: int,
                       image: QImage) -> None:
        """Stores a render; pages IN THE REQUESTED RANGE are NEVER evicted.

        THE BUG WE HIT: the eviction loop said "keep at least two entries", while
        every paint requested the visible pages plus one neighbour each -- at
        least THREE pages. Once three images exceeded the budget, caching N
        evicted N-1, the next paint requested N-1 again and the loop never
        ended. MEASURED: with NO input at all, the window repainted 118 times in
        3 seconds at 200% zoom, and at 250% the page being read fell back to a
        placeholder 29 times -- one CPU core was busy for nothing.
        cekirdegi bosuna doluydu.

        The fix: the requested range is protected. The budget may be exceeded,
        but the excess is that range and is bounded; an endless loop was not.
        """
        self._onbellek.pop(page, None)
        self._onbellek[page] = (genislik, yukseklik, image)
        first, sonu = self._request_range()
        total = sum(g.sizeInBytes() for _, _, g in self._onbellek.values())
        for anahtar in list(self._onbellek):
            if total <= self.ONBELLEK_BAYT:
                break
            if first <= anahtar <= sonu:
                continue
            total -= self._onbellek.pop(anahtar)[2].sizeInBytes()

    def _delayed_request(self) -> None:
        """Scrolling stopped: now the pages really being looked at are requested."""
        self._hizli = False
        if not self._count:
            return
        first, sonu = self._request_range()
        self._refresh_requests(first, sonu)
        self.viewport().update()

    # events

    def scrollContentsBy(self, dx: int, dy: int) -> None:   # noqa: N802
        super().scrollContentsBy(dx, dy)
        # Scrolling is assumed to continue; the timer restarts on every step, so a
        # render is requested only once the scrolling STOPS.
        self._hizli = True
        self._request_timer.start()
        self._update_current()
        self.viewport().update()

    def resizeEvent(self, event) -> None:       # noqa: N802 - Qt naming
        super().resizeEvent(event)
        capa = self._capa_al()
        if self._mode in ("genislik", "sayfa"):
            self._fit(self._mode)
        # The scroll range depends ON THE VIEW HEIGHT and has to be refreshed even
        # when the scale does not change at all.
        #
        # THE BUG WE HIT: on a resize that changed only the height, fitting to the
        # width produced the same scale, set_zoom returned early and the bar kept
        # its old range. MEASURED: on a 796 page document, shortening the window
        # from 900 to 480 pixels made the last 408 pixels of the document
        # unreachable by any means, and because pageStep stayed stale every
        # PageDown skipped 408 pixels of text.
        # atliyordu.
        self._update_bars()
        self._capa_uygula(capa)
        self._hizli = False
        self._update_current()
        self.viewport().update()

    def wheelEvent(self, event) -> None:        # noqa: N802 - Qt naming
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # THE NOTCHES ARE ACCUMULATED.
            #
            # THE BUG WE HIT: only the sign was read and every event applied a full
            # 1.25 factor. A Windows precision touchpad sends DOZENS of events of a few
            # units for a single gesture: measured, a gesture worth 40% of one notch
            # made the zoom jump from 108% to 400%.
            # %108'den %400'e firliyordu.
            if event.phase() == Qt.ScrollPhase.ScrollBegin:
                self._tekerlek = 0
            self._tekerlek += event.angleDelta().y()
            while self._tekerlek >= self.CENT:
                self._tekerlek -= self.CENT
                self.zoom_in()
            while self._tekerlek <= -self.CENT:
                self._tekerlek += self.CENT
                self.zoom_out()
            event.accept()
            return
        self._tekerlek = 0
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:     # noqa: N802 - Qt naming
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() == Qt.Key.Key_Home:
                self.verticalScrollBar().setValue(0)
                event.accept()
                return
            if event.key() == Qt.Key.Key_End:
                cubuk = self.verticalScrollBar()
                cubuk.setValue(cubuk.maximum())
                event.accept()
                return
        super().keyPressEvent(event)


class SpecWindow(QMainWindow):
    """A SEPARATE window that shows the UML 2.5.1 PDF.

    It is independent of the main window: the user can keep the document open on
    a second screen while drawing the model. The document scrolls continuously
    through all of its pages and can be zoomed (see ContinuousPdfView).
    """

    #: The identity of the document; it stays permanently in the status bar.
    SURUM = "OMG UML 2.5.1 (formal/2017-12-05)"

    def __init__(self, pdf_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OMG UML 2.5.1 Specification — %s"
                            % os.path.basename(pdf_path))
        self.resize(980, 900)

        from PyQt6.QtPdf import QPdfDocument

        # The document is DELIBERATELY without a parent object: its lifetime then
        # depends on the Python references, and the render worker holds one too.
        # With a parent, the document could be pulled OUT FROM UNDER the worker
        # while the window was being destroyed, and the worker would touch freed
        # memory inside pdfium.
        self.doc = QPdfDocument(None)
        state = self.doc.load(pdf_path)
        # On a corrupt or truncated file an error is raised rather than opening an
        # empty window SILENTLY; the caller reports it to the user.
        if state != QPdfDocument.Error.None_:
            raise RuntimeError("the PDF could not be opened (%s)"
                               % state.name.rstrip("_"))
        if self.doc.pageCount() <= 0:
            raise RuntimeError("the PDF contains no pages")

        self.view = ContinuousPdfView(self.doc, self)
        # A thread has been running FROM THE MOMENT the view was built. Any error
        # thrown from here on that leaves without stopping it destroys a running
        # QThread and the process dies silently.
        try:
            self._kur()
        except Exception:                      # noqa: BLE001
            self.view.shutdown()
            raise

    def _kur(self) -> None:
        self.view.setAccessibleName("Specification pages")
        self.view.setToolTip(
            "Scroll through the whole document.  Ctrl+wheel zooms.")

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(body)

        self._build_toolbar()

        # The page indicator has to be a PERMANENT widget, not a temporary message.
        # The status tips of the toolbar actions (setStatusTip) use the temporary
        # message area: passing over a button overwrote the indicator, and moving
        # away sent an empty tip and ERASED it entirely.
        self.lbl_page = QLabel()
        self.lbl_page.setFont(ui_font(9))
        self.lbl_page.setAccessibleName("Current page")
        self.statusBar().addPermanentWidget(self.lbl_page)
        permanent = QLabel(" %s " % self.SURUM)
        permanent.setFont(ui_font(9))
        self.statusBar().addPermanentWidget(permanent)

        self.view.page_changed.connect(self._page_changed)
        self.view.scale_changed.connect(self._scale_changed)

        self._pencereyi_olc()
        self.view.fit_width()
        self._write_state()
        self._write_scale()
        self.view.setFocus()

    def _pencereyi_olc(self) -> None:
        """Opens the window wide enough for the toolbar TO FIT.

        At a fixed width of 980 pixels the actions at the end of the bar fell
        into Qt's ">>" overflow menu. They all have a keyboard shortcut, but an
        invisible button cannot be discovered.
        """
        gerek = self.centralWidget().minimumSizeHint().width()
        for bar in self.findChildren(QToolBar):
            gerek = max(gerek, bar.sizeHint().width() + 16)
        genislik = max(980, gerek)
        yukseklik = 900
        ekran = QGuiApplication.primaryScreen()
        if ekran is not None:
            field = ekran.availableGeometry()
            genislik = min(genislik, int(field.width() * 0.92))
            yukseklik = min(yukseklik, int(field.height() * 0.92))
        self.resize(genislik, yukseklik)

    def closeEvent(self, event) -> None:        # noqa: N802 - Qt naming
        # The worker thread must be stopped BEFORE the document is destroyed.
        self.view.shutdown()
        # pdfium's own page cache is released as well. A closed window must not go
        # on holding tens of megabytes; measured: opening and closing the window
        # eight times took the process from 131 MB to 463 MB. The window is never
        # shown again -- the main window builds a new one on every request (see
        # show_spec).
        try:
            self.doc.close()
        except Exception:                      # noqa: BLE001
            pass
        super().closeEvent(event)

    # toolbar

    def _action(self, bar: QToolBar, text: str, kisayol: str, geri) -> QAction:
        action = QAction(text, self)
        action.setShortcut(kisayol)
        # It is VISIBLE in the shortcut tooltip: the toolbar carries text rather
        # than icons, and the key assignment cannot be discovered otherwise.
        action.setToolTip("%s  (%s)" % (text, kisayol))
        action.setStatusTip(action.toolTip())
        action.triggered.connect(geri)
        bar.addAction(action)
        return action

    def _build_toolbar(self) -> None:
        bar = QToolBar("Navigation", self)
        bar.setObjectName("SpecNavigation")
        bar.setMovable(False)
        self.addToolBar(bar)

        last = self.doc.pageCount() - 1
        # Bare PageUp/PageDown were DELIBERATELY not used: those keys have to
        # scroll the document. Bound to actions they would never reach the view and
        # scrolling would break completely.
        self.a_first = self._action(bar, "First page", "Ctrl+Home",
                                   lambda: self.view.goto(0))
        self.a_prev = self._action(
            bar, "Previous page", "Ctrl+PgUp",
            lambda: self.view.goto(self.view.current_page() - 1))
        self.a_next = self._action(
            bar, "Next page", "Ctrl+PgDown",
            lambda: self.view.goto(self.view.current_page() + 1))
        self.a_last = self._action(bar, "Last page", "Ctrl+End",
                                  lambda: self.view.goto(last))
        bar.addSeparator()

        sarmal = QWidget()
        row = QHBoxLayout(sarmal)
        row.setContentsMargins(8, 0, 8, 0)
        label = QLabel("Page")
        label.setFont(ui_font(9))
        row.addWidget(label)
        self.spin = QSpinBox()
        self.spin.setRange(1, max(1, self.doc.pageCount()))
        self.spin.setAccessibleName("Page number")
        self.spin.setToolTip("Jump to a page number")
        self.spin.valueChanged.connect(self._spin_degisti)
        label.setBuddy(self.spin)
        row.addWidget(self.spin)
        self.lbl_total = QLabel(" / %d" % self.doc.pageCount())
        self.lbl_total.setFont(ui_font(9))
        row.addWidget(self.lbl_total)
        bar.addWidget(sarmal)
        bar.addSeparator()

        self.a_zoom_out = self._action(bar, "Zoom out", "Ctrl+-",
                                      self.view.zoom_out)
        self.lbl_zoom = QLabel()
        self.lbl_zoom.setFont(ui_font(9))
        self.lbl_zoom.setAccessibleName("Zoom level")
        self.lbl_zoom.setToolTip("Current zoom level")
        self.lbl_zoom.setMinimumWidth(52)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.lbl_zoom)
        self.a_zoom_in = self._action(bar, "Zoom in", "Ctrl++",
                                     self.view.zoom_in)
        self.a_fit_width = self._action(bar, "Fit width", "Ctrl+0",
                                       self.view.fit_width)
        self.a_fit_page = self._action(bar, "Fit page", "Ctrl+9",
                                      self.view.fit_page)
        bar.addSeparator()

        # This action is built through _action() as well. Built by hand, it was the
        # only action without a shortcut; once the toolbar was narrow and it fell
        # into the overflow menu, it became UNREACHABLE from the keyboard (toolbar
        # buttons do not take focus and the window has no menu bar).
        self.a_web = self._action(
            bar, "Open the spec page", "Ctrl+Shift+O",
            lambda: QDesktopServices.openUrl(QUrl(SPEC_PAGE)))
        self.a_web.setToolTip("Open %s in the browser  (Ctrl+Shift+O)"
                              % SPEC_PAGE)
        self.a_web.setStatusTip(self.a_web.toolTip())

        # The toolbar DELIBERATELY gets no inline style sheet. The colour would be
        # embedded at set-up, and because an inline sheet overrides the application
        # sheet, the strip would keep the old background on a theme change and the
        # text would drop to 1.05:1 contrast and become UNREADABLE. The application
        # sheet already styles QToolBar (see theme.stylesheet).

    # navigation

    def _page(self) -> int:
        return self.view.current_page()

    def _spin_degisti(self, deger: int) -> None:
        if deger - 1 != self.view.current_page():
            self.view.goto(deger - 1)

    def _page_changed(self, page: int) -> None:
        if self.spin.value() != page + 1:
            self.spin.blockSignals(True)
            self.spin.setValue(page + 1)
            self.spin.blockSignals(False)
        self._write_state()

    def _scale_changed(self, _zoom: float) -> None:
        self._write_scale()

    def _write_state(self) -> None:
        page = self.view.current_page()
        last = self.doc.pageCount() - 1
        self.lbl_page.setText(" Page %d of %d " % (page + 1, last + 1))
        self.a_first.setEnabled(page > 0)
        self.a_prev.setEnabled(page > 0)
        self.a_next.setEnabled(page < last)
        self.a_last.setEnabled(page < last)

    def _write_scale(self) -> None:
        zoom = self.view.zoom_factor()
        self.lbl_zoom.setText("%d%%" % int(round(zoom * 100.0)))
        self.a_zoom_in.setEnabled(zoom < self.view.ZOOM_MAX - 1e-6)
        self.a_zoom_out.setEnabled(zoom > self.view.ZOOM_MIN + 1e-6)
