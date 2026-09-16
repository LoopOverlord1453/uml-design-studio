"""OMG UML 2.5.1 spesifikasyonunu AYRI BIR PENCEREDE gosteren PDF okuyucu.

Arac, dogrulama bulgularinda spesifikasyonun bolum ve sayfa numarasini
zaten yaziyor (bkz. app/core/uml_spec.py). Bu pencere o atiflari
IZLENEBILIR kilar: kullanici bulguyu gordugu yerden belgeye gecebilir.

PDF NEDEN PAKETE GOMULMEZ
-------------------------
Belge OMG'nin telif hakki altindadir; serbestce INDIRILEBILIR ama
yeniden dagitilamaz. Uygulamanin EXE'sine gomulseydi musteriye teslim
edilen her kopya bir yeniden dagitim olurdu. Ayrica dosya 18 MB'dir ve
kullanicilarin cogu ona hic bakmaz.

Bunun yerine: dosya YERELDE varsa acilir; yoksa kullaniciya sorulur ve
ONUN ONAYIYLA resmi adresten kendi makinesine indirilir. Indirilen kopya
uygulama veri klasorunde durur, depoda degil.
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

#: Resmi indirme adresi (OMG). 2026-09 itibariyla application/pdf, ~18 MB.
SPEC_URL = "https://www.omg.org/spec/UML/2.5.1/PDF"
#: Belgenin insan tarafindan okunabilir adresi (tarayicida acilir).
SPEC_PAGE = "https://www.omg.org/spec/UML/"
#: Yerel kopyanin dosya adi.
SPEC_FILE = "OMG-UML-2.5.1.pdf"

#: Arayuzun kullandigi kimlikler (bkz. main_window.APP_NAME / SETTINGS_ORG).
#:
#: BURADA TEKRARLANIR, cunku `main_window` bu modulu iceri aktarir; ters
#: yonde bir alim dairesel bagimlilik olurdu. Deger kaymasin diye bir
#: regresyon ikisinin ayni oldugunu dogrular.
GUI_ORG = "UmlDesignStudio"
GUI_APP = "UML Design Studio"


def spec_pdf_path() -> str:
    """Yerel kopyanin durdugu (ya da duracagi) tam yol.

    Uygulama VERI klasoru kullanilir; kurulum klasoru salt-okunur
    olabilir ve EXE'nin yanina yazmak yonetici hakki isteyebilir.
    """
    from PyQt6.QtCore import QCoreApplication

    kok = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    if not kok:
        kok = os.path.expanduser("~")
    # AppDataLocation, uygulama adi AYARLIYSA zaten onu icerir
    # (bkz. main_window.run -> setApplicationName). Ayarli degilse
    # (testler, dogrudan betik kosumu) yol yorumlayicinin adina gore
    # olusur; o durumda kendi klasorumuzu ekleriz.
    if not QCoreApplication.applicationName():
        kok = os.path.join(kok, "UML-Design-Studio")
    return os.path.join(kok, SPEC_FILE)


def bundled_spec_path() -> Optional[str]:
    """Kullanici kendi kopyasini uygulamanin yanina koyduysa onu bulur."""
    kok = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    for aday in (os.path.join(kok, "docs", SPEC_FILE),
                 os.path.join(kok, SPEC_FILE)):
        if os.path.isfile(aday):
            return aday
    return None


def spec_pdf_candidates() -> List[str]:
    """PDF'in bulunabilecegi TUM yollar, oncelik sirasiyla.

    ARAYUZ ile BETIKLER AYNI DOSYAYI GORMELIDIR.

    YASANAN HATA: `QStandardPaths.AppDataLocation`, uygulama adi
    AYARLIYSA onu icerir. Arayuz `main_window.run()` icinde adi kurar ve
    dosya `.../Roaming/UmlDesignStudio/UML Design Studio/` altina iner.
    Bir betik (tools/ altindaki testler, surum araclari) o adi kurmadigi
    icin ayni cagri BASKA bir klasor donduruyor ve arayuzun sorunsuz
    actigi PDF "bulunamadi" sayiliyordu. Iki yazim da denenir.
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
    """Varsa yerel PDF'in yolu."""
    for yol in spec_pdf_candidates():
        if os.path.isfile(yol):
            return yol
    return None


class _Downloader(QThread):
    """PDF'i AYRI IS PARCACIGINDA indirir; arayuz donmasin.

    SONUC SINYALLE DEGIL, ALANLARDA tasinir.

    YASANAN HATA: sonuc `finished_ok` sinyaliyle gonderiliyordu. Sinyal
    is parcacigindan ana parcaciga KUYRUKLA teslim edilir; oysa bekleyen
    taraf `isRunning()` yanlis olur olmaz donguden cikiyordu. Iki olay
    arasinda bir yaris vardi ve ana pencere mesgulken (zamanlayicilar,
    git is parcacigi) yaris KAYBEDILIYORDU: dosya eksiksiz iniyor, ama
    cagiran taraf "sonuc yok" gorup None donuyordu -- PDF indi, pencere
    acilmadi. Duz alan okumasinda yaris yoktur.
    """

    progress = pyqtSignal(int, int)          # (inen bayt, toplam bayt)

    def __init__(self, hedef: str, parent=None) -> None:
        super().__init__(parent)
        self.hedef = hedef
        #: run() bitince doldurulur; ANA PARCACIK bunlari okur.
        self.sonuc_yol = ""
        self.sonuc_hata = ""
        self.iptal_edildi = False
        self._iptal = False

    def cancel(self) -> None:
        self._iptal = True

    def run(self) -> None:                   # pragma: no cover - is parcacigi
        gecici = self.hedef + ".part"
        try:
            os.makedirs(os.path.dirname(self.hedef), exist_ok=True)
            istek = urllib.request.Request(
                SPEC_URL, headers={"User-Agent": "UML-Design-Studio"})
            with urllib.request.urlopen(istek, timeout=30) as cevap:
                toplam = int(cevap.headers.get("Content-Length") or 0)
                inen = 0
                # Ilerleme SEYREK bildirilir. 64 KB'lik her parcada sinyal
                # yaymak 18 MB icin 280'den fazla GUI guncellemesi demekti;
                # modal QProgressDialog.setValue() kendi icinde olay
                # isledigi icin bu, derin ve gereksiz bir ic ice girme
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
            # Yarim dosya ASLA hedefe konmaz: bir sonraki acilis onu
            # gecerli sanip bozuk bir PDF gostermesin.
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
    """Yerel PDF'i dondurur; yoksa KULLANICIYA SORARAK indirir.

    Indirme kendiliginden BASLAMAZ. Bir masaustu uygulamasinin kullaniciya
    sormadan internetten 18 MB cekmesi kabul edilebilir degil; ayrica
    kurumsal aglarda disari cikis engelli olabilir.
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

    # BEKLEME: elle `processEvents()` dongusu YERINE yerel bir olay
    # dongusu. Elle donmek, indirme boyunca butun uygulamayi yeniden
    # girilebilir kiliyordu (zamanlayicilar, yeniden cizim, git is
    # parcacigi) ve dongunun cikis kosulu sinyal teslimiyle YARISIYORDU.
    dongu = QEventLoop()
    isci.finished.connect(dongu.quit)
    isci.start()
    if isci.isRunning():
        dongu.exec()
    # Is parcaciginin GERCEKTEN bittiginden emin ol; alanlar ancak o
    # zaman guvenle okunur.
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
    # Buraya dusmek beklenmez; yine de sessiz kalmak yerine SOYLE.
    QMessageBox.critical(
        parent, "Download failed",
        "The download finished but the file could not be found at:\n\n%s"
        % hedef)
    return None


class _PageRenderer(QThread):
    """Sayfalari AYRI IS PARCACIGINDA cizer.

    Neden kendi is parcacigimiz: pdfium bir sayfayi ILK erisimde acmak
    zorunda ve bu belgede sayfa basina 30-40 ms suruyor. Ana parcacikta
    yapilsaydi her yeni sayfa gorunume girdiginde arayuz o kadar
    takilirdi. Olcumler:

        render(sayfa)          7-38 ms
        pagePointSize(sayfa)   ayni yuklemeyi paylasir

    Olcu ve cizim BIRLIKTE burada yapilir; boylece sayfa bir kez yuklenir.

    QPdfDocument bu kullanim icin guvenlidir: Qt'nin kendi
    QPdfPageRenderer sinifi da belgeyi bir isci parcacigindan kullanir.
    Ayrica olculdu -- arka planda 120 sayfa olcusu alinirken ana
    parcacikta 40 sayfa cizildi, cokme yok ve degerler dogru.
    """

    #: (sayfa, piksel genisligi, goruntu, genislik_pt, yukseklik_pt)
    hazir = pyqtSignal(int, int, QImage, float, float)

    #: Kuyrukta en fazla bu kadar istek tutulur.
    KUYRUK_SINIRI = 24

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._kilit = QMutex()
        self._uyandir = QWaitCondition()
        self._kuyruk: List[Tuple[int, int, int]] = []
        self._dur = False

    def iste(self, sayfa: int, genislik: int, yukseklik: int) -> List[int]:
        """Bir sayfayi siraya koyar.

        DUSURULEN isteklerin sayfa numaralarini dondurur. Cagiran taraf
        bunlari kendi "bekliyor" listesinden silmek ZORUNDA: aksi halde
        kuyruk sinirina takilip dusmus bir istek sonsuza dek bekliyor
        sayilir ve o sayfa bir daha hic cizilmezdi.
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
        """Is parcacigini bitirir. Belge yok edilmeden ONCE cagrilmali."""
        self._kilit.lock()
        try:
            self._dur = True
            self._kuyruk = []
            self._uyandir.wakeAll()
        finally:
            self._kilit.unlock()
        self.wait(5000)

    def run(self) -> None:                     # pragma: no cover - is parcacigi
        while True:
            self._kilit.lock()
            while not self._kuyruk and not self._dur:
                self._uyandir.wait(self._kilit)
            if self._dur:
                self._kilit.unlock()
                return
            # LIFO: en SON istenen sayfa once cizilir. Kullanici hizla
            # kaydirdiginda sirada bekleyen eski sayfalar degil, su an
            # bakilan sayfa oncelik alir.
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
    """Belgenin TAMAMINI tek bir surekli serit olarak gosteren gorunum.

    NEDEN QPdfView KULLANILMIYOR
    ----------------------------
    Qt'nin hazir gorunumunde iki secenek var ve ikisi de bu belgeye
    uymuyor:

      * SinglePage  -- kaydirma cubugu YALNIZCA gecerli sayfayi kapsar;
                       796 sayfada gezinmek mumkun degil.
      * MultiPage   -- belgenin tamaminin yerlesimini PESIN hesaplar:
                       her sayfa icin pagePointSize() cagirir. Olculdu:
                       dosyadan 47 804 ms, bellekten yuklemede bile
                       22 848 ms -- hepsi ana parcacikta, yani uygulama
                       yarim dakikadan uzun DONUYOR.

    Bu gorunum ikisini de yapmaz: yerlesimi ILK sayfanin olcusunden
    tahmin eder (tek bir ucuz cagri), kaydirma cubugunu butun belgeye
    yayar ve YALNIZCA gorunen sayfalari, o da ayri bir is parcaciginda
    cizer. Bir sayfanin gercek olcusu cizildiginde ogrenilir; tahminden
    farkliysa yerlesim kendini duzeltir.

    Yakinlastirma da buradadir: olcek "nokta basina piksel" olarak
    tutulur, sayfalar her olcekte yeniden cizilir (yeniden orneklenmis
    goruntu buyutulmez), bu yuzden yazi her yakinlikta net kalir.
    """

    #: Gecerli sayfa degistiginde (0 tabanli).
    sayfa_degisti = pyqtSignal(int)
    #: Yakinlastirma orani degistiginde (1.0 = %100).
    olcek_degisti = pyqtSignal(float)

    BOSLUK = 14                                # sayfalar arasi bosluk
    KENAR = 12                                 # serit kenar payi
    ZOOM_MIN = 0.20
    ZOOM_MAX = 4.00
    ZOOM_ADIM = 1.25
    #: Tek bir sayfa goruntusunun ust piksel siniri (kenar ve toplam).
    #: %400 yakinlikta bir A4 3173x4224 piksel, yani 53 MB eder; alan
    #: siniri bunu 32 MB'a indirir. Kayip yalnizca en uc yakinlikta
    #: gorunur ve orada bile sayfa basina 300 nokta/inc'in ustundedir.
    PIKSEL_SINIRI = 4000
    ALAN_SINIRI = 8_000_000
    #: Cizilmis sayfa onbelleginin hedef ust siniri. Genislige
    #: sigdirilmis bir A4 yaklasik 4,7 MB tutar.
    ONBELLEK_BAYT = 48 * 1024 * 1024
    #: Kaydirma durduktan sonra cizim istenene kadar beklenen sure (ms).
    ISTEK_GECIKMESI = 90
    #: Bir fare tekerlegi centi (Qt birimleri).
    CENT = 120

    def __init__(self, belge, parent=None) -> None:
        super().__init__(parent)
        self._belge = belge
        self._sayi = belge.pageCount()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.viewport().setAutoFillBackground(False)

        # Olcu bilgisi: yalnizca ILK sayfa pesin okunur (tek cagri).
        # Digerleri cizildikce ogrenilir; o ana kadar bu tahmin gecerli.
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

        # Hizli kaydirmada cizim ISTENMEZ; bkz. _gecikmeli_istek.
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

    # ------------------------------------------------------- yasam dongusu

    def shutdown(self) -> None:
        """Isci parcacigini durdurur ve bellegi birakir.

        Belge yok edilmeden ONCE cagrilmalidir: isci hala pdfium icinde
        olabilir. Cizim onbellegi de burada bosaltilir; onlarca megabayt
        tutan goruntuler kapatilmis bir pencerenin pesinde surunmemeli.
        """
        self._istek_zamanlayici.stop()
        if self._isci is not None:
            self._isci.durdur()
            self._isci = None
        self._onbellek.clear()
        self._bekleyen.clear()

    # ---------------------------------------------------------------- yerlesim

    def _olcu(self, sayfa: int) -> Tuple[float, float]:
        return self._pt.get(sayfa, self._varsayilan)

    def _yerlesim(self) -> None:
        """Sayfa dikdortgenlerini bastan hesaplar (796 oge: mikrosaniyeler).

        Olculer TAM PIKSELE yuvarlanir. Kesirli bir hedef dikdortgen,
        cizilmis goruntuyu Qt'nin 1:1 kopyalama yolundan cikarip
        yeniden orneklemeye sokar; olculdu: kenar karsitligi %65'e
        duserek yazi surekli yumusak gorunur. Tam piksel, "yazi her
        yakinlikta net kalir" ozelliginin on kosulu.
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

    # ------------------------------------------------------------------- capa

    def _capa_al(self) -> Tuple[int, float]:
        """Olcek degismeden once bakilan noktayi sayfaya gore saklar."""
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

    # ---------------------------------------------------------------- gezinme

    def current_page(self) -> int:
        return self._gecerli

    def page_count(self) -> int:
        return self._sayi

    def goto(self, sayfa: int) -> None:
        """Istenen sayfaya gider ve onu GECERLI sayfa yapar.

        Kaydirmadan turetilen "en cok yer kaplayan sayfa" kurali burada
        YETMEZ. Belgenin sonuna yakin bir sayfa istendiginde kaydirma
        cubugu daha asagi inemez; istenen sayfa gorunume girer ama en
        uste gelemez. O durumda turetilen deger baska bir sayfayi
        gosterir ve kullanici sayfa kutusuna 9 yazip 12 gorur. Istenen
        sayfa GERCEKTEN gorunuyorsa istek her zaman kazanir.
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
        """Gecerli sayfa: gorunumde EN COK YER KAPLAYAN sayfa.

        Sabit bir sonda -- ornegin "gorunumun ust ucte birindeki sayfa"
        -- kullanilamaz. Cok uzaklastirildiginda gorunume bes alti
        sayfa siginca o nokta bakilan sayfanin degil, birkac
        altindakinin uzerine duser. OLCULDU: 1453 piksel yuksekliginde
        bir pencerede %20 yakinlikta 16. sayfaya gidilince sayac 18
        yaziyordu; kullanici sayfa kutusuna 16 yazip 18 goruyordu.

        ESITLIK genelde EN USTTEKI sayfaya verilir; boylece goto(n) her
        yakinlikta n sonucunu doner. Tek istisna belgenin SONU: orada
        kaydirma cubugu daha fazla inemedigi icin son sayfa hicbir zaman
        en uste gelemez, esitlik ustteki lehine bozulursa SON SAYFA
        secilemez olurdu (olculdu: 400 sayfalik belgede son sayfa tam
        ekrandayken sayac 399 yaziyordu). Cubuk sonundayken esitlik en
        ALTTAKI tam gorunen sayfaya verilir.
        """
        if not self._alt:
            return
        cubuk = self.verticalScrollBar()
        ust = float(cubuk.value())
        alt = ust + self.viewport().height()
        sonda = cubuk.value() >= cubuk.maximum() and cubuk.maximum() > 0
        ilk, sonu = self._gorunur_aralik()
        # Tarama ust sinirla korunur: en kucuk olcekte gorunume cok
        # sayfa sigabilir, ama hepsi tam gorundugu icin zaten esittir.
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

    # ------------------------------------------------------------ yakinlastirma

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
        # Capa uygulamak kaydirma uretir; ama bu kullanicinin surukledigi
        # bir kaydirma degil. Yeni olcekteki sayfalar HEMEN istenmeli.
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
        # 1 piksel pay: yuvarlama yuzunden yatay cubuk cikip gorunum
        # genisligini degistirmesin, yoksa sigdirma salinim yapar.
        alan_g = max(1.0, self.viewport().width() - 2.0 * self.KENAR - 1.0)
        olcek = alan_g / wpt
        if kip == "sayfa":
            alan_y = max(1.0, self.viewport().height() - 2.0 * self.KENAR)
            olcek = min(olcek, alan_y / hpt)
        self.set_zoom(olcek / self._temel, kip)

    # -------------------------------------------------------------------- cizim

    def paintEvent(self, olay) -> None:        # noqa: N802 - Qt adlandirmasi
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

        # HIZLI KAYDIRMADA CIZIM ISTENMEZ.
        #
        # Kullanici kaydirma cubugunu belge boyunca surukledigi surece
        # her ara konum icin sayfa istemek, hicbiri goruntulenmeyecek
        # onlarca cizim uretir. Olculdu: boyle bir suruklemede tek bir
        # kaydirma adimi 225 ms'ye kadar cikiyordu ve maliyetin neredeyse
        # tamami istek uretmekteydi. Istekler kaydirma DURDUKTAN sonra
        # verilir; bu arada sayfalar yer tutucu olarak gorunur.
        if self._hizli:
            self._istek_zamanlayici.start()
        else:
            ilk2, sonu2 = self._istek_araligi()
            self._istekleri_yenile(ilk2, sonu2)

    def _gorunur_aralik(self) -> Tuple[int, int]:
        """Gorunumde (kismen de olsa) yer alan ilk ve son sayfa."""
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
        """Cizimi istenecek sayfa araligi.

        Gorunenlerin bir onu ve bir arkasi da istenir ki yavas
        kaydirmada sayfa bos cerceve olarak gorunmesin. Ama bu ON
        YUKLEME yalnizca butce elveriyorsa yapilir: cok yakinda tek bir
        sayfa goruntusu onlarca megabayt tutar, komsulari da istemek
        onbellegi asar ve atma-yeniden cizme dongusune sokardi.
        """
        ilk, sonu = self._gorunur_aralik()
        tahmin = self._tahmini_bayt(self._gecerli)
        if tahmin * (sonu - ilk + 3) <= self.ONBELLEK_BAYT:
            ilk = max(0, ilk - 1)
            sonu = min(self._sayi - 1, sonu + 1)
        return (ilk, sonu)

    def _piksel_olcu(self, sayfa: int) -> Tuple[int, int]:
        """Sayfanin istenecek GERCEK piksel olcusu."""
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
        # Gorunum disinda kalmis bekleyen istekler temizlenir.
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
                    # Kuyruktan DUSEN istek bir daha gelmez; bekliyor
                    # isaretini kaldirmazsak o sayfa sonsuza dek yer
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

        # Gercek olcu tahminden farkli cikarsa yerlesim duzeltilir.
        # Bu belgede butun sayfalar ayni olcude oldugu icin normalde
        # hic calismaz; farkli olculu bir belgede ise gorunum kendini
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
        """Cizimi saklar; ISTENEN ARALIKTAKI sayfalar ASLA atilmaz.

        YASANAN HATA: atma dongusu "en az iki girdi kalsin" diyordu,
        oysa her boyamada gorunenler artı birer komsu, yani en az UC
        sayfa isteniyordu. Uc goruntu butceyi asinca N'yi onbellege
        koymak N-1'i atiyor, bir sonraki boyama N-1'i yeniden istiyor ve
        dongu hic bitmiyordu. OLCULDU: pencere HICBIR girdi almadan,
        %200 yakinlikta 3 saniyede 118 kez yeniden boyaniyor, %250'de
        okunan sayfa 29 kez yer tutucuya donuyordu -- bir islemci
        cekirdegi bosuna doluydu.

        Cozum: istenen aralik korunur. Butce asilabilir, ama asim
        aralik kadardir ve sinirlidir; sonsuz dongu ise sinirsizdi.
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
        """Kaydirma durdu: artik gercekten bakilan sayfalar istenir."""
        self._hizli = False
        if not self._sayi:
            return
        ilk, sonu = self._istek_araligi()
        self._istekleri_yenile(ilk, sonu)
        self.viewport().update()

    # ------------------------------------------------------------------ olaylar

    def scrollContentsBy(self, dx: int, dy: int) -> None:   # noqa: N802
        super().scrollContentsBy(dx, dy)
        # Kaydirma suruyor kabul edilir; zamanlayici her adimda yeniden
        # baslar, yani cizim ancak kaydirma DURUNCA istenir.
        self._hizli = True
        self._istek_zamanlayici.start()
        self._gecerli_guncelle()
        self.viewport().update()

    def resizeEvent(self, olay) -> None:       # noqa: N802 - Qt adlandirmasi
        super().resizeEvent(olay)
        capa = self._capa_al()
        if self._kip in ("genislik", "sayfa"):
            self._sigdir(self._kip)
        # Kaydirma araligi GORUNUM YUKSEKLIGINE baglidir ve olcek hic
        # degismese bile yenilenmelidir.
        #
        # YASANAN HATA: yalnizca yukseklik degisen bir boyutlandirmada
        # genislige sigdirma ayni olcegi uretiyor, set_zoom erken
        # donuyor ve cubuk eski araliginda kaliyordu. OLCULDU: 796
        # sayfalik belgede pencere 900'den 480 piksele kisaltilinca
        # belgenin son 408 pikseline HICBIR yolla inilemiyor, ustelik
        # pageStep eski kaldigi icin her PageDown 408 piksellik metni
        # atliyordu.
        self._cubuklari_guncelle()
        self._capa_uygula(capa)
        self._hizli = False
        self._gecerli_guncelle()
        self.viewport().update()

    def wheelEvent(self, olay) -> None:        # noqa: N802 - Qt adlandirmasi
        if olay.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Cent BIRIKTIRILIR.
            #
            # YASANAN HATA: yalnizca isaret okunuyor ve her olay tam bir
            # 1,25 katı uyguluyordu. Windows hassas dokunmatik yuzeyi tek
            # bir harekette birkac birimlik ONLARCA olay gonderir:
            # olculdu, tek centin %40'i kadar bir harekette yakinlik
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

    def keyPressEvent(self, olay) -> None:     # noqa: N802 - Qt adlandirmasi
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
    """UML 2.5.1 PDF'ini gosteren AYRI pencere.

    Ana pencereden bagimsizdir: kullanici modeli cizerken belgeyi ikinci
    ekranda acik tutabilir. Belge butun sayfalari boyunca kesintisiz
    kaydirilir ve yakinlastirilabilir (bkz. ContinuousPdfView).
    """

    #: Belgenin kimligi; durum cubugunda kalici olarak durur.
    SURUM = "OMG UML 2.5.1 (formal/2017-12-05)"

    def __init__(self, pdf_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OMG UML 2.5.1 Specification — %s"
                            % os.path.basename(pdf_path))
        self.resize(980, 900)

        from PyQt6.QtPdf import QPdfDocument

        # Belge BILEREK ust nesnesizdir: boylece omru Python
        # basvurularina baglidir ve cizim iscisi de bir basvuru tutar.
        # Ust nesneli olsaydi pencere yikilirken belge iscinin ALTINDAN
        # cekilebilir, isci pdfium icinde serbest birakilmis bellege
        # dokunurdu.
        self.doc = QPdfDocument(None)
        durum = self.doc.load(pdf_path)
        # Bozuk ya da yarim bir dosyada SESSIZCE bos pencere acmak yerine
        # hata verilir; cagiran taraf bunu kullaniciya bildirir.
        if durum != QPdfDocument.Error.None_:
            raise RuntimeError("the PDF could not be opened (%s)"
                               % durum.name.rstrip("_"))
        if self.doc.pageCount() <= 0:
            raise RuntimeError("the PDF contains no pages")

        self.view = ContinuousPdfView(self.doc, self)
        # Gorunum kuruldugu ANDAN itibaren bir is parcacigi calisiyor.
        # Buradan sonra atilacak HER hata onu durdurmadan cikarsa,
        # calisan bir QThread yok edilir ve surec sessizce olur.
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

        # Sayfa gostergesi KALICI parcacik olmali, gecici mesaj degil.
        # Arac cubugu eylemlerinin durum ipuclari (setStatusTip) gecici
        # mesaj alanini kullanir: bir dugmenin ustunden gecmek gostergeyi
        # eziyor, uzaklasmak ise bos ipucu gonderip tamamen SILIYORDU.
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
        """Arac cubugu SIGACAK kadar genis acilir.

        980 piksellik sabit genislikte cubugun sonundaki eylemler
        Qt'nin ">>" tasma menusune dusuyordu. Hepsinin klavye kisayolu
        var, ama gorunmeyen bir dugme kesfedilemez.
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

    def closeEvent(self, olay) -> None:        # noqa: N802 - Qt adlandirmasi
        # Isci parcacigi belge yok edilmeden ONCE durdurulmali.
        self.view.shutdown()
        # pdfium'un kendi sayfa onbellegi de birakilir. Kapatilmis bir
        # pencere onlarca megabayti tutmaya devam etmemeli; olculdu:
        # pencere sekiz kez acilip kapatildiginda surec 131 MB'den
        # 463 MB'ye cikiyordu. Pencere yeniden gosterilmez -- ana
        # pencere her istekte yenisini kurar (bkz. show_spec).
        try:
            self.doc.close()
        except Exception:                      # noqa: BLE001
            pass
        super().closeEvent(olay)

    # ------------------------------------------------------------ arac cubugu

    def _eylem(self, bar: QToolBar, metin: str, kisayol: str, geri) -> QAction:
        eylem = QAction(metin, self)
        eylem.setShortcut(kisayol)
        # Kisayol ipucunda GORUNUR: arac cubugunda simge degil metin var,
        # tus atamasi baska turlu kesfedilemez.
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
        # Cipilak PageUp/PageDown BILEREK kullanilmadi: bu tuslar belgeyi
        # kaydirmalidir. Eylemlere baglansalardi gorunume hic ulasmaz,
        # kaydirma tamamen bozulurdu.
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

        # Bu eylem de _eylem() uzerinden kurulur. Elle kurulurken tek
        # kisayolsuz eylem oydu; arac cubugu dar kalip tasma menusune
        # dustugunde klavyeyle ERISILEMEZ oluyordu (arac cubugu dugmeleri
        # odak almaz, pencerede menu cubugu da yok).
        self.a_web = self._eylem(
            bar, "Open the spec page", "Ctrl+Shift+O",
            lambda: QDesktopServices.openUrl(QUrl(SPEC_PAGE)))
        self.a_web.setToolTip("Open %s in the browser  (Ctrl+Shift+O)"
                              % SPEC_PAGE)
        self.a_web.setStatusTip(self.a_web.toolTip())

        # Arac cubugu BILEREK satir-ici stil sayfasi almaz. Renk kurulum
        # aninda gomulurdu ve satir-ici sayfa uygulama sayfasini
        # ezdiginden, tema degistiginde serit eski zeminde kalir, yazi
        # 1,05:1 karsitliga duserek OKUNAMAZ olurdu. Uygulama sayfasi
        # QToolBar'i zaten bicimlendiriyor (bkz. theme.stylesheet).

    # ---------------------------------------------------------------- gezinme

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
