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

    kok = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not kok:
        kok = os.path.expanduser("~")
    # AppDataLocation already includes the application name WHEN IT IS SET
    # (see main_window.run -> setApplicationName). When it is not set (tests,
    # running a script directly) the path is built from the name of the
    # interpreter; in that case we append our own folder.
    if not QCoreApplication.applicationName():
        kok = os.path.join(kok, "UML-Design-Studio")
    return os.path.join(kok, SPEC_FILE)


def bundled_spec_path() -> Optional[str]:
    """Finds the copy the user put next to the application, if any."""
    kok = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for aday in (os.path.join(kok, "docs", SPEC_FILE),
                 os.path.join(kok, SPEC_FILE)):
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

    kok = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if kok:
        adaylar.append(os.path.join(kok, GUI_ORG, GUI_APP, SPEC_FILE))
        adaylar.append(os.path.join(kok, "UML-Design-Studio", SPEC_FILE))

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

    def __init__(self, hedef: str, parent=None) -> None:
        super().__init__(parent)
        self.hedef = hedef
        #: Filled in when run() finishes; THE MAIN THREAD reads these.
        self.sonuc_yol = ""
        self.sonuc_hata = ""
        self.iptal_edildi = False
        self._iptal = False

    def cancel(self) -> None:
        self._iptal = True

    def run(self) -> None:                   # pragma: no cover - thread
        gecici = self.hedef + ".part"
        try:
            os.makedirs(os.path.dirname(self.hedef), exist_ok=True)
            istek = urllib.request.Request(
                SPEC_URL, headers={"User-Agent": "UML-Design-Studio"})
            with urllib.request.urlopen(istek, timeout=30) as cevap:
                toplam = int(cevap.headers.get("Content-Length") or 0)
                inen = 0
                # Progress is reported SPARSELY. Emitting a signal on every 64 KB chunk
                # meant more than 280 GUI updates for 18 MB; and because a modal
                # QProgressDialog.setValue() processes events itself, that produced deep
                # and pointless re-entrancy.
                # uretiyordu.
                adim = max(64 * 1024, (toplam // 100) if toplam else 0)
                son_bildirim = 0
                with open(gecici, "wb") as fh:
                    while True:
                        if self._iptal:
                            raise InterruptedError("cancelled")
                        parca = cevap.read(64 * 1024)
                        if not parca:
                            break
                        fh.write(parca)
                        inen += len(parca)
                        if inen - son_bildirim >= adim:
                            son_bildirim = inen
                            self.progress.emit(inen, toplam)
                self.progress.emit(inen, toplam)
                if toplam and inen != toplam:
                    raise IOError(
                        "incomplete download: %d of %d bytes" % (inen, toplam))
            # A half-written file is NEVER put at the destination: the next start-up
            # must not take it for valid and show a corrupt PDF.
            os.replace(gecici, self.hedef)
            self.sonuc_yol = self.hedef
        except InterruptedError:
            self.iptal_edildi = True
            self._temizle(gecici)
        except Exception as exc:             # noqa: BLE001
            self.sonuc_hata = str(exc)
            self._temizle(gecici)

    @staticmethod
    def _temizle(yol: str) -> None:
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

    hedef = spec_pdf_path()
    kutu = QMessageBox(parent)
    kutu.setWindowTitle("UML 2.5.1 specification")
    kutu.setIcon(QMessageBox.Icon.Question)
    kutu.setText("The specification PDF is not on this computer yet.")
    kutu.setInformativeText(
        "It can be downloaded from the official OMG address:\n\n"
        "    %s\n\n"
        "About 18 MB. It is stored only on this computer:\n\n"
        "    %s\n\n"
        "The document is copyrighted by OMG and is therefore not shipped "
        "with this application." % (SPEC_URL, hedef))
    indir = kutu.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
    tarayici = kutu.addButton("Open in browser",
                              QMessageBox.ButtonRole.ActionRole)
    kutu.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    kutu.exec()

    if kutu.clickedButton() is tarayici:
        QDesktopServices.openUrl(QUrl(SPEC_PAGE))
        return None
    if kutu.clickedButton() is not indir:
        return None

    ilerleme = QProgressDialog("Downloading the UML 2.5.1 specification…",
                               "Cancel", 0, 100, parent)
    ilerleme.setWindowTitle("Download")
    ilerleme.setWindowModality(Qt.WindowModality.WindowModal)
    ilerleme.setMinimumDuration(0)
    ilerleme.setValue(0)

    isci = _Downloader(hedef, parent)

    def adim(inen: int, toplam: int) -> None:
        if toplam > 0:
            ilerleme.setMaximum(toplam)
            ilerleme.setValue(inen)
        ilerleme.setLabelText(
            "Downloading the UML 2.5.1 specification…  %.1f MB"
            % (inen / (1024.0 * 1024.0)))

    isci.progress.connect(adim)
    ilerleme.canceled.connect(isci.cancel)

    # WAITING: a local event loop INSTEAD OF a manual `processEvents()` loop.
    # Spinning by hand made the whole application re-entrant for the duration
    # of the download (timers, redraws, the git thread), and the exit
    # condition of the loop RACED with the signal delivery.
    dongu = QEventLoop()
    isci.finished.connect(dongu.quit)
    isci.start()
    if isci.isRunning():
        dongu.exec()
    # Make sure the thread has REALLY finished; only then are the fields safe
    # to read.
    isci.wait()
    ilerleme.close()

    if isci.iptal_edildi:
        return None
    if isci.sonuc_hata:
        QMessageBox.critical(
            parent, "Download failed",
            "The specification could not be downloaded:\n\n%s\n\n"
            "You can fetch it manually from %s and place it next to the "
            "application as docs/%s."
            % (isci.sonuc_hata, SPEC_PAGE, SPEC_FILE))
        return None
    if isci.sonuc_yol and os.path.isfile(isci.sonuc_yol):
        return isci.sonuc_yol
    # Falling through here is not expected; still, say so rather than stay silent.
    QMessageBox.critical(
        parent, "Download failed",
        "The download finished but the file could not be found at:\n\n%s"
        % hedef)
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
    hazir = pyqtSignal(int, int, QImage, float, float)

    #: At most this many requests are held in the queue.
    KUYRUK_SINIRI = 24

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._kilit = QMutex()
        self._uyandir = QWaitCondition()
        self._kuyruk: List[Tuple[int, int, int]] = []
        self._dur = False

    def iste(self, sayfa: int, genislik: int, yukseklik: int) -> List[int]:
        """Queues a page.

        Returns the page numbers of the requests that were DROPPED. The caller
        MUST remove those from its own "pending" list: otherwise a request that
        hit the queue limit and was dropped would count as pending forever and
        that page would never be drawn again.
        """
        self._kilit.lock()
        try:
            self._kuyruk = [x for x in self._kuyruk if x[0] != sayfa]
            self._kuyruk.append((sayfa, genislik, yukseklik))
            dusen: List[int] = []
            if len(self._kuyruk) > self.KUYRUK_SINIRI:
                fazla = self._kuyruk[:-self.KUYRUK_SINIRI]
                dusen = [x[0] for x in fazla]
                del self._kuyruk[:-self.KUYRUK_SINIRI]
            self._uyandir.wakeOne()
            return dusen
        finally:
            self._kilit.unlock()

    def durdur(self) -> None:
        """Ends the thread. It must be called BEFORE the document is destroyed."""
        self._kilit.lock()
        try:
            self._dur = True
            self._kuyruk = []
            self._uyandir.wakeAll()
        finally:
            self._kilit.unlock()
        self.wait(5000)

    def run(self) -> None:                     # pragma: no cover - thread
        while True:
            self._kilit.lock()
            while not self._kuyruk and not self._dur:
                self._uyandir.wait(self._kilit)
            if self._dur:
                self._kilit.unlock()
                return
            # LIFO: the page requested LAST is rendered first. When the user scrolls
            # quickly, the page being looked at now takes priority over the older ones
            # waiting in the queue.
            sayfa, genislik, yukseklik = self._kuyruk.pop()
            self._kilit.unlock()
            try:
                olcu = self._belge.pagePointSize(sayfa)
                if olcu.width() <= 0.0 or olcu.height() <= 0.0:
                    continue
                goruntu = self._belge.render(
                    sayfa, QSize(max(1, genislik), max(1, yukseklik)))
                self.hazir.emit(sayfa, genislik, goruntu,
                                olcu.width(), olcu.height())
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
    sayfa_degisti = pyqtSignal(int)
    #: When the zoom ratio changes (1.0 = 100%).
    olcek_degisti = pyqtSignal(float)

    BOSLUK = 14                                # the gap between pages
    KENAR = 12                                 # the margin of the strip
    ZOOM_MIN = 0.20
    ZOOM_MAX = 4.00
    ZOOM_ADIM = 1.25
    #: The upper pixel bound of a single page image (edge and total).
    #: At 400% zoom an A4 is 3173x4224 pixels, that is 53 MB; the area bound
    #: brings it down to 32 MB. The loss shows only at the most extreme zoom,
    #: and even there it stays above 300 dots per inch per page.
    PIKSEL_SINIRI = 4000
    ALAN_SINIRI = 8_000_000
    #: The target upper bound of the rendered page cache. An A4 fitted to the
    #: width takes about 4.7 MB.
    ONBELLEK_BAYT = 48 * 1024 * 1024
    #: How long to wait after scrolling stops before requesting a render (ms).
    ISTEK_GECIKMESI = 90
    #: One mouse wheel notch (Qt units).
    CENT = 120

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._sayi = belge.pageCount()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.viewport().setAutoFillBackground(False)

        # Size information: only the FIRST page is read up front (a single call).
        # The others are learned as they are rendered; until then this estimate holds.
        ilk = belge.pagePointSize(0) if self._sayi else QSizeF(595.0, 792.0)
        if ilk.width() <= 0.0 or ilk.height() <= 0.0:
            ilk = QSizeF(595.0, 792.0)
        self._varsayilan = (ilk.width(), ilk.height())
        self._pt: Dict[int, Tuple[float, float]] = {}

        self._temel = max(1.0, float(self.logicalDpiX())) / 72.0
        self._zoom = 1.0
        self._olcek = self._temel
        self._kip = "genislik"

        self._w: List[float] = []
        self._h: List[float] = []
        self._y: List[float] = []
        self._alt: List[float] = []
        self._toplam = 0.0
        self._gecerli = 0
        self._tekerlek = 0

        self._onbellek: "OrderedDict[int, Tuple[int, int, QImage]]" = \
            OrderedDict()
        self._bekleyen: Dict[int, Tuple[int, int]] = {}

        # While scrolling fast NO render is requested; see _delayed_request.
        self._hizli = False
        self._istek_zamanlayici = QTimer(self)
        self._istek_zamanlayici.setSingleShot(True)
        self._istek_zamanlayici.setInterval(self.ISTEK_GECIKMESI)
        self._istek_zamanlayici.timeout.connect(self._gecikmeli_istek)

        self._isci = _PageRenderer(belge, self)
        self._isci.hazir.connect(self._cizildi)
        self._isci.start()

        self._yerlesim()
        self._cubuklari_guncelle()

    # life cycle

    def shutdown(self) -> None:
        """Stops the worker thread and releases the memory.

        It must be called BEFORE the document is destroyed: the worker may still
        be inside pdfium. The render cache is emptied here too; images holding
        tens of megabytes must not linger behind a closed window.
        """
        self._istek_zamanlayici.stop()
        if self._isci is not None:
            self._isci.durdur()
            self._isci = None
        self._onbellek.clear()
        self._bekleyen.clear()

    # layout

    def _olcu(self, sayfa: int) -> Tuple[float, float]:
        return self._pt.get(sayfa, self._varsayilan)

    def _yerlesim(self) -> None:
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
        self._alt = []
        y = float(self.KENAR)
        for i in range(self._sayi):
            wpt, hpt = self._olcu(i)
            w = float(max(1, int(round(wpt * self._olcek))))
            h = float(max(1, int(round(hpt * self._olcek))))
            self._w.append(w)
            self._h.append(h)
            self._y.append(y)
            self._alt.append(y + h)
            y += h + self.BOSLUK
        self._toplam = (y - self.BOSLUK + self.KENAR) if self._sayi else 0.0

    def _cubuklari_guncelle(self) -> None:
        olcu = self.viewport().size()
        dikey = max(0, int(round(self._toplam - olcu.height())))
        cubuk = self.verticalScrollBar()
        cubuk.setRange(0, dikey)
        cubuk.setPageStep(max(1, olcu.height() - 24))
        cubuk.setSingleStep(max(1, olcu.height() // 12))

        icerik = (max(self._w) if self._w else 0.0) + 2 * self.KENAR
        yatay = max(0, int(round(icerik - olcu.width())))
        ycubuk = self.horizontalScrollBar()
        ycubuk.setRange(0, yatay)
        ycubuk.setPageStep(max(1, olcu.width()))
        ycubuk.setSingleStep(max(1, olcu.width() // 12))

    def _sayfa_x(self, genislik: float) -> float:
        gorunur = self.viewport().width()
        if genislik + 2 * self.KENAR <= gorunur:
            return float(int((gorunur - genislik) / 2.0))
        return float(self.KENAR - self.horizontalScrollBar().value())

    # anchor

    def _capa_al(self) -> Tuple[int, float]:
        """Records the point being looked at, relative to the page, before a scale change."""
        deger = float(self.verticalScrollBar().value())
        sayfa = self._sayfa_at(deger)
        yukseklik = self._h[sayfa] if self._h else 1.0
        if yukseklik <= 0.0:
            return (sayfa, 0.0)
        return (sayfa, (deger - self._y[sayfa]) / yukseklik)

    def _capa_uygula(self, capa: Tuple[int, float]) -> None:
        sayfa, oran = capa
        if not self._y:
            return
        sayfa = max(0, min(sayfa, self._sayi - 1))
        hedef = self._y[sayfa] + oran * self._h[sayfa]
        self.verticalScrollBar().setValue(int(round(hedef)))

    def _sayfa_at(self, y: float) -> int:
        if not self._alt:
            return 0
        i = bisect.bisect_right(self._alt, y)
        return max(0, min(i, self._sayi - 1))

    # navigation

    def current_page(self) -> int:
        return self._gecerli

    def page_count(self) -> int:
        return self._sayi

    def goto(self, sayfa: int) -> None:
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
        sayfa = max(0, min(sayfa, self._sayi - 1))
        self.verticalScrollBar().setValue(
            int(round(self._y[sayfa] - self.KENAR)))
        ilk, sonu = self._gorunur_aralik()
        if ilk <= sayfa <= sonu and self._gecerli != sayfa:
            self._gecerli = sayfa
            self.sayfa_degisti.emit(sayfa)

    def _gecerli_guncelle(self) -> None:
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
        if not self._alt:
            return
        cubuk = self.verticalScrollBar()
        ust = float(cubuk.value())
        alt = ust + self.viewport().height()
        sonda = cubuk.value() >= cubuk.maximum() and cubuk.maximum() > 0
        ilk, sonu = self._gorunur_aralik()
        # The scan is bounded: at the smallest scale many pages can fit in the
        # view, but they are all fully visible and therefore already tied.
        sonu = min(sonu, ilk + 64)
        en_iyi = ilk
        en_cok = -1.0
        for i in range(ilk, sonu + 1):
            pay = min(self._alt[i], alt) - max(self._y[i], ust)
            if sonda:
                if pay >= en_cok - 0.5:
                    en_cok = max(en_cok, pay)
                    en_iyi = i
            elif pay > en_cok + 0.5:
                en_cok = pay
                en_iyi = i
        if en_iyi != self._gecerli:
            self._gecerli = en_iyi
            self.sayfa_degisti.emit(en_iyi)

    # zoom

    def zoom_factor(self) -> float:
        return self._zoom

    def fit_mode(self) -> str:
        return self._kip

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * self.ZOOM_ADIM)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / self.ZOOM_ADIM)

    def set_zoom(self, zoom: float, kip: str = "serbest") -> None:
        zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, zoom))
        if abs(zoom - self._zoom) < 1e-6 and kip == self._kip:
            return
        capa = self._capa_al()
        self._kip = kip
        self._zoom = zoom
        self._olcek = self._temel * zoom
        self._yerlesim()
        self._cubuklari_guncelle()
        self._capa_uygula(capa)
        # Applying the anchor produces a scroll; but that is not a scroll the user
        # dragged. The pages at the new scale must be requested IMMEDIATELY.
        self._hizli = False
        self._gecerli_guncelle()
        self.olcek_degisti.emit(self._zoom)
        self.viewport().update()

    def fit_width(self) -> None:
        self._sigdir("genislik")

    def fit_page(self) -> None:
        self._sigdir("sayfa")

    def _sigdir(self, kip: str) -> None:
        wpt, hpt = self._olcu(self._gecerli)
        if wpt <= 0.0 or hpt <= 0.0:
            return
        # A 1 pixel margin: rounding must not bring up a horizontal bar and change
        # the view width, or fitting would oscillate.
        alan_g = max(1.0, self.viewport().width() - 2.0 * self.KENAR - 1.0)
        olcek = alan_g / wpt
        if kip == "sayfa":
            alan_y = max(1.0, self.viewport().height() - 2.0 * self.KENAR)
            olcek = min(olcek, alan_y / hpt)
        self.set_zoom(olcek / self._temel, kip)

    # painting

    def paintEvent(self, olay) -> None:        # noqa: N802 - Qt naming
        boyaci = QPainter(self.viewport())
        boyaci.fillRect(self.viewport().rect(), QColor(C.PANEL_DARK))
        if not self._sayi:
            return
        boyaci.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        ust = float(self.verticalScrollBar().value())
        ilk, sonu = self._gorunur_aralik()

        kagit = QColor("#FFFFFF")
        cerceve = QColor(C.BORDER_LIGHT)
        soluk = QColor(C.TEXT_DIM)
        for i in range(ilk, sonu + 1):
            hedef = QRectF(self._sayfa_x(self._w[i]), self._y[i] - ust,
                           self._w[i], self._h[i])
            boyaci.fillRect(hedef, kagit)
            girdi = self._onbellek.get(i)
            if girdi is not None:
                boyaci.drawImage(hedef, girdi[2])
                self._onbellek.move_to_end(i)
            else:
                boyaci.setPen(soluk)
                boyaci.setFont(ui_font(9))
                boyaci.drawText(hedef, Qt.AlignmentFlag.AlignCenter,
                                "Page %d" % (i + 1))
            boyaci.setPen(cerceve)
            boyaci.drawRect(hedef)
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
            self._istek_zamanlayici.start()
        else:
            ilk2, sonu2 = self._istek_araligi()
            self._istekleri_yenile(ilk2, sonu2)

    def _gorunur_aralik(self) -> Tuple[int, int]:
        """The first and last page (even partly) present in the view."""
        if not self._alt:
            return (0, 0)
        ust = float(self.verticalScrollBar().value())
        alt = ust + self.viewport().height()
        ilk = max(0, bisect.bisect_right(self._alt, ust))
        ilk = min(ilk, self._sayi - 1)
        sonu = min(self._sayi - 1, bisect.bisect_left(self._y, alt) - 1)
        if sonu < ilk:
            sonu = ilk
        return (ilk, sonu)

    def _istek_araligi(self) -> Tuple[int, int]:
        """The range of pages to request for rendering.

        One before and one after the visible ones are requested too, so a page
        does not show as an empty frame during slow scrolling. But this
        PREFETCH only happens when the budget allows: zoomed far in, a single
        page image takes tens of megabytes, and requesting its neighbours too
        would blow the cache and start an evict-and-redraw loop.
        """
        ilk, sonu = self._gorunur_aralik()
        tahmin = self._tahmini_bayt(self._gecerli)
        if tahmin * (sonu - ilk + 3) <= self.ONBELLEK_BAYT:
            ilk = max(0, ilk - 1)
            sonu = min(self._sayi - 1, sonu + 1)
        return (ilk, sonu)

    def _piksel_olcu(self, sayfa: int) -> Tuple[int, int]:
        """The REAL pixel size the page will be requested at."""
        oran = self.devicePixelRatioF() or 1.0
        g = max(1, int(round(self._w[sayfa] * oran)))
        y = max(1, int(round(self._h[sayfa] * oran)))
        if g > self.PIKSEL_SINIRI:
            y = max(1, int(round(y * self.PIKSEL_SINIRI / float(g))))
            g = self.PIKSEL_SINIRI
        if g * y > self.ALAN_SINIRI:
            kucult = (self.ALAN_SINIRI / float(g * y)) ** 0.5
            g = max(1, int(g * kucult))
            y = max(1, int(y * kucult))
        return (g, y)

    def _tahmini_bayt(self, sayfa: int) -> int:
        if not self._w:
            return 0
        sayfa = max(0, min(sayfa, self._sayi - 1))
        g, y = self._piksel_olcu(sayfa)
        return max(1, g * y * 4)

    def _istekleri_yenile(self, ilk: int, sonu: int) -> None:
        # Pending requests that have left the view are cleared.
        for sayfa in list(self._bekleyen):
            if sayfa < ilk or sayfa > sonu:
                del self._bekleyen[sayfa]
        for sayfa in range(ilk, sonu + 1):
            gerek = self._piksel_olcu(sayfa)
            girdi = self._onbellek.get(sayfa)
            if girdi is not None and (girdi[0], girdi[1]) == gerek:
                continue
            if self._bekleyen.get(sayfa) == gerek:
                continue
            self._bekleyen[sayfa] = gerek
            if self._isci is not None:
                for dusen in self._isci.iste(sayfa, gerek[0], gerek[1]):
                    # A request DROPPED from the queue never comes back; without removing
                    # the pending mark, that page would stay a placeholder forever.
                    # tutucu olarak kalirdi.
                    self._bekleyen.pop(dusen, None)

    def _cizildi(self, sayfa: int, genislik: int, goruntu: QImage,
                 wpt: float, hpt: float) -> None:
        bekleyen = self._bekleyen.get(sayfa)
        if bekleyen is not None and bekleyen[0] == genislik:
            del self._bekleyen[sayfa]
        if goruntu.isNull():
            return
        self._onbellege_koy(sayfa, genislik, goruntu.height(), goruntu)

        # When the real size differs from the estimate the layout is corrected.
        # Because every page in this document is the same size it normally never
        # runs; on a document with mixed sizes the view pulls itself together.
        # toparlar.
        onceki = self._pt.get(sayfa)
        self._pt[sayfa] = (wpt, hpt)
        if onceki is None and (abs(wpt - self._varsayilan[0]) > 0.5
                               or abs(hpt - self._varsayilan[1]) > 0.5):
            capa = self._capa_al()
            self._yerlesim()
            self._cubuklari_guncelle()
            self._capa_uygula(capa)
        self.viewport().update()

    def _onbellege_koy(self, sayfa: int, genislik: int, yukseklik: int,
                       goruntu: QImage) -> None:
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
        self._onbellek.pop(sayfa, None)
        self._onbellek[sayfa] = (genislik, yukseklik, goruntu)
        ilk, sonu = self._istek_araligi()
        toplam = sum(g.sizeInBytes() for _, _, g in self._onbellek.values())
        for anahtar in list(self._onbellek):
            if toplam <= self.ONBELLEK_BAYT:
                break
            if ilk <= anahtar <= sonu:
                continue
            toplam -= self._onbellek.pop(anahtar)[2].sizeInBytes()

    def _gecikmeli_istek(self) -> None:
        """Scrolling stopped: now the pages really being looked at are requested."""
        self._hizli = False
        if not self._sayi:
            return
        ilk, sonu = self._istek_araligi()
        self._istekleri_yenile(ilk, sonu)
        self.viewport().update()

    # events

    def scrollContentsBy(self, dx: int, dy: int) -> None:   # noqa: N802
        super().scrollContentsBy(dx, dy)
        # Scrolling is assumed to continue; the timer restarts on every step, so a
        # render is requested only once the scrolling STOPS.
        self._hizli = True
        self._istek_zamanlayici.start()
        self._gecerli_guncelle()
        self.viewport().update()

    def resizeEvent(self, olay) -> None:       # noqa: N802 - Qt naming
        super().resizeEvent(olay)
        capa = self._capa_al()
        if self._kip in ("genislik", "sayfa"):
            self._sigdir(self._kip)
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
        self._cubuklari_guncelle()
        self._capa_uygula(capa)
        self._hizli = False
        self._gecerli_guncelle()
        self.viewport().update()

    def wheelEvent(self, olay) -> None:        # noqa: N802 - Qt naming
        if olay.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # THE NOTCHES ARE ACCUMULATED.
            #
            # THE BUG WE HIT: only the sign was read and every event applied a full
            # 1.25 factor. A Windows precision touchpad sends DOZENS of events of a few
            # units for a single gesture: measured, a gesture worth 40% of one notch
            # made the zoom jump from 108% to 400%.
            # %108'den %400'e firliyordu.
            if olay.phase() == Qt.ScrollPhase.ScrollBegin:
                self._tekerlek = 0
            self._tekerlek += olay.angleDelta().y()
            while self._tekerlek >= self.CENT:
                self._tekerlek -= self.CENT
                self.zoom_in()
            while self._tekerlek <= -self.CENT:
                self._tekerlek += self.CENT
                self.zoom_out()
            olay.accept()
            return
        self._tekerlek = 0
        super().wheelEvent(olay)

    def keyPressEvent(self, olay) -> None:     # noqa: N802 - Qt naming
        if olay.modifiers() == Qt.KeyboardModifier.NoModifier:
            if olay.key() == Qt.Key.Key_Home:
                self.verticalScrollBar().setValue(0)
                olay.accept()
                return
            if olay.key() == Qt.Key.Key_End:
                cubuk = self.verticalScrollBar()
                cubuk.setValue(cubuk.maximum())
                olay.accept()
                return
        super().keyPressEvent(olay)


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
        durum = self.doc.load(pdf_path)
        # On a corrupt or truncated file an error is raised rather than opening an
        # empty window SILENTLY; the caller reports it to the user.
        if durum != QPdfDocument.Error.None_:
            raise RuntimeError("the PDF could not be opened (%s)"
                               % durum.name.rstrip("_"))
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

        govde = QWidget()
        duzen = QVBoxLayout(govde)
        duzen.setContentsMargins(0, 0, 0, 0)
        duzen.setSpacing(0)
        duzen.addWidget(self.view, 1)
        self.setCentralWidget(govde)

        self._build_toolbar()

        # The page indicator has to be a PERMANENT widget, not a temporary message.
        # The status tips of the toolbar actions (setStatusTip) use the temporary
        # message area: passing over a button overwrote the indicator, and moving
        # away sent an empty tip and ERASED it entirely.
        self.lbl_page = QLabel()
        self.lbl_page.setFont(ui_font(9))
        self.lbl_page.setAccessibleName("Current page")
        self.statusBar().addPermanentWidget(self.lbl_page)
        kalici = QLabel(" %s " % self.SURUM)
        kalici.setFont(ui_font(9))
        self.statusBar().addPermanentWidget(kalici)

        self.view.sayfa_degisti.connect(self._sayfa_degisti)
        self.view.olcek_degisti.connect(self._olcek_degisti)

        self._pencereyi_olc()
        self.view.fit_width()
        self._durum_yaz()
        self._olcek_yaz()
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
            alan = ekran.availableGeometry()
            genislik = min(genislik, int(alan.width() * 0.92))
            yukseklik = min(yukseklik, int(alan.height() * 0.92))
        self.resize(genislik, yukseklik)

    def closeEvent(self, olay) -> None:        # noqa: N802 - Qt naming
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
        super().closeEvent(olay)

    # toolbar

    def _eylem(self, bar: QToolBar, metin: str, kisayol: str, geri) -> QAction:
        eylem = QAction(metin, self)
        eylem.setShortcut(kisayol)
        # It is VISIBLE in the shortcut tooltip: the toolbar carries text rather
        # than icons, and the key assignment cannot be discovered otherwise.
        eylem.setToolTip("%s  (%s)" % (metin, kisayol))
        eylem.setStatusTip(eylem.toolTip())
        eylem.triggered.connect(geri)
        bar.addAction(eylem)
        return eylem

    def _build_toolbar(self) -> None:
        bar = QToolBar("Navigation", self)
        bar.setObjectName("SpecNavigation")
        bar.setMovable(False)
        self.addToolBar(bar)

        son = self.doc.pageCount() - 1
        # Bare PageUp/PageDown were DELIBERATELY not used: those keys have to
        # scroll the document. Bound to actions they would never reach the view and
        # scrolling would break completely.
        self.a_first = self._eylem(bar, "First page", "Ctrl+Home",
                                   lambda: self.view.goto(0))
        self.a_prev = self._eylem(
            bar, "Previous page", "Ctrl+PgUp",
            lambda: self.view.goto(self.view.current_page() - 1))
        self.a_next = self._eylem(
            bar, "Next page", "Ctrl+PgDown",
            lambda: self.view.goto(self.view.current_page() + 1))
        self.a_last = self._eylem(bar, "Last page", "Ctrl+End",
                                  lambda: self.view.goto(son))
        bar.addSeparator()

        sarmal = QWidget()
        satir = QHBoxLayout(sarmal)
        satir.setContentsMargins(8, 0, 8, 0)
        etiket = QLabel("Page")
        etiket.setFont(ui_font(9))
        satir.addWidget(etiket)
        self.spin = QSpinBox()
        self.spin.setRange(1, max(1, self.doc.pageCount()))
        self.spin.setAccessibleName("Page number")
        self.spin.setToolTip("Jump to a page number")
        self.spin.valueChanged.connect(self._spin_degisti)
        etiket.setBuddy(self.spin)
        satir.addWidget(self.spin)
        self.lbl_total = QLabel(" / %d" % self.doc.pageCount())
        self.lbl_total.setFont(ui_font(9))
        satir.addWidget(self.lbl_total)
        bar.addWidget(sarmal)
        bar.addSeparator()

        self.a_zoom_out = self._eylem(bar, "Zoom out", "Ctrl+-",
                                      self.view.zoom_out)
        self.lbl_zoom = QLabel()
        self.lbl_zoom.setFont(ui_font(9))
        self.lbl_zoom.setAccessibleName("Zoom level")
        self.lbl_zoom.setToolTip("Current zoom level")
        self.lbl_zoom.setMinimumWidth(52)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.lbl_zoom)
        self.a_zoom_in = self._eylem(bar, "Zoom in", "Ctrl++",
                                     self.view.zoom_in)
        self.a_fit_width = self._eylem(bar, "Fit width", "Ctrl+0",
                                       self.view.fit_width)
        self.a_fit_page = self._eylem(bar, "Fit page", "Ctrl+9",
                                      self.view.fit_page)
        bar.addSeparator()

        # This action is built through _action() as well. Built by hand, it was the
        # only action without a shortcut; once the toolbar was narrow and it fell
        # into the overflow menu, it became UNREACHABLE from the keyboard (toolbar
        # buttons do not take focus and the window has no menu bar).
        self.a_web = self._eylem(
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

    def _sayfa_degisti(self, sayfa: int) -> None:
        if self.spin.value() != sayfa + 1:
            self.spin.blockSignals(True)
            self.spin.setValue(sayfa + 1)
            self.spin.blockSignals(False)
        self._durum_yaz()

    def _olcek_degisti(self, _zoom: float) -> None:
        self._olcek_yaz()

    def _durum_yaz(self) -> None:
        sayfa = self.view.current_page()
        son = self.doc.pageCount() - 1
        self.lbl_page.setText(" Page %d of %d " % (sayfa + 1, son + 1))
        self.a_first.setEnabled(sayfa > 0)
        self.a_prev.setEnabled(sayfa > 0)
        self.a_next.setEnabled(sayfa < son)
        self.a_last.setEnabled(sayfa < son)

    def _olcek_yaz(self) -> None:
        zoom = self.view.zoom_factor()
        self.lbl_zoom.setText("%d%%" % int(round(zoom * 100.0)))
        self.a_zoom_in.setEnabled(zoom < self.view.ZOOM_MAX - 1e-6)
        self.a_zoom_out.setEnabled(zoom > self.view.ZOOM_MIN + 1e-6)
