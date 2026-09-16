"""Tuvallerin ORTAK davranisi: gezinme, kenardan kaydirma ve HIZALAMA.

Iki tuval de (durum makinesi ve sinif diyagrami) ayni QGraphicsView
sorunlarini yasar, bu yuzden kural TEK YERDE durur. Ayri ayri yazilsaydi
biri duzeltilip oteki unutulurdu -- nitekim fark isaretleri tam boyle
yalnizca durum tuvaline eklenmis, sinif tuvalinde eksik kalmisti.

Cozulen iki somut sikayet:

* "surukleyince surekli baska yerlere atiyor" -- sahne dikdortgeni ancak
  fare birakildiginda yeniden hesaplaniyordu; kaydirma cubuklarinin
  araligi bir anda degisince goruntudeki her sey yerinden ziplyordu.
  Artik sahne surukleme SIRASINDA buyur ve her degisiklikte goruntu
  konumu telafi edilir.
* "pencere disina tasinacaksa otomatik sola/saga/yukari/asagi git" --
  goruntu alani hic kaydirilmiyordu; kenara gelen oge gozden kayboluyordu.
  Artik imlec kenara yaklasinca goruntu draw.io gibi kayar.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPen
from PyQt6.QtWidgets import QApplication

from .theme import C


class CanvasNavigation:
    """QGraphicsView tureyen tuvallere eklenen gezinme karisimi.

    Kullanan sinif ``self._scene`` alanini kurmali ve ``__init__``
    icinde :meth:`_init_navigation` cagirmalidir.
    """

    # -- SURUKLERKEN KENARDAN KAYDIRMA (draw.io davranisi) ------------------- #
    #: Imlec goruntu alani kenarina bu kadar yaklasinca kaydirma baslar (px).
    AUTOSCROLL_MARGIN = 34.0
    #: Kenara tam yapisikken bir tikta kaydirilan piksel.
    AUTOSCROLL_STEP = 26.0
    #: Kaydirma tik araligi (ms) -- ~60 Hz.
    AUTOSCROLL_MS = 16
    #: Surukleme sirasinda imlecin cevresinde tutulan bos sahne payi.
    SCENE_PAD = 600.0

    def _init_navigation(self) -> None:
        """Kaydirma zamanlayicisini ve surukleme durumunu kurar."""
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(self.AUTOSCROLL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        #: Her tikta kaydirilacak piksel (x, y); sifirsa kaydirma durur.
        self._autoscroll_vec = QPointF()
        #: Suruklemedeki son fare konumu (goruntu alani pikseli).
        self._drag_pos: Optional[QPoint] = None
        self._init_alignment()

    def _set_scene_rect(self, rect: QRectF) -> None:
        """Sahne dikdortgenini GORUNTUYU KAYDIRMADAN degistirir.

        YASANAN HATA: sahne dikdortgeni `itemsBoundingRect()` ile yeniden
        hesaplaniyordu. Kullanici bir kutuyu sagda uzaga surukleyip
        biraktiginda sahne bir anda genisliyor (1917 -> 3082 px olctuk),
        kaydirma cubuklarinin araligi degistigi icin de goruntudeki HER SEY
        yerinden ziplyordu. Kullanici bunu "surekli baska yerlere atiyor"
        diye bildirdi.

        Cozum: yeni dikdortgen verildikten sonra goruntu alaninin sol ust
        kosesinin SAHNEDEKI karsiligi olculur; kaydiysa kaydirma cubuklari
        aradaki fark kadar geri alinir. Boylece sahne buyur ama goruntu
        kimildamaz.
        """
        # SU AN GORUNEN alan her zaman sahnenin icinde kalmali.
        #
        # Aksi halde sahne kuculdugunde kaydirma cubugunun araligi daralir,
        # Qt degeri KIRPAR ve goruntu bir anda baska bir yere atlar --
        # olctugumuz kadariyla dikeyde -399'dan 0'a. Yeniden kurulumda
        # sahne icerige gore kuculdugu icin bu, surukleme bittigi anda
        # yasaniyordu.
        gorunen = self.mapToScene(self.viewport().rect()).boundingRect()
        rect = rect.united(gorunen)
        if rect == self._scene.sceneRect():
            return
        oncesi = self.mapToScene(QPoint(0, 0))
        self._scene.setSceneRect(rect)
        sonrasi = self.mapToScene(QPoint(0, 0))
        kayma = sonrasi - oncesi
        if kayma.isNull():
            return
        olcek = self.transform().m11() or 1.0
        dikey = self.transform().m22() or 1.0
        yatay_cubuk = self.horizontalScrollBar()
        dikey_cubuk = self.verticalScrollBar()
        yatay_cubuk.setValue(yatay_cubuk.value() + int(round(kayma.x() * olcek)))
        dikey_cubuk.setValue(dikey_cubuk.value() + int(round(kayma.y() * dikey)))

    def _grow_scene(self, rect: QRectF) -> None:
        """Sahneyi verilen dikdortgeni kapsayacak kadar BUYUTUR (kucultmez)."""
        simdiki = self._scene.sceneRect()
        birlesik = simdiki.united(rect)
        if birlesik != simdiki:
            self._set_scene_rect(birlesik)

    def _room_for_drag(self, view_pos: QPoint) -> None:
        """Imlecin cevresinde ve secili ogelerin etrafinda yer acar.

        Sahne surukleme SIRASINDA buyursa, birakma aninda buyuyecek bir
        sey kalmaz; sicrama da olmaz. Ayrica kaydirma cubuklarinin
        araligi genisledigi icin kenardan kaydirma gercekten ilerler --
        aksi halde imlec kenara dayandiginda kaydirilacak yer bulunamaz.
        """
        nokta = self.mapToScene(view_pos)
        pay = self.SCENE_PAD
        alan = QRectF(nokta.x() - pay, nokta.y() - pay, pay * 2.0, pay * 2.0)
        for oge in self._scene.selectedItems():
            alan = alan.united(oge.sceneBoundingRect())
        self._grow_scene(alan)

    def _autoscroll_vector(self, pos: QPoint) -> QPointF:
        """Imlec kenara ne kadar yakinsa o kadar hizli kaydirma vektoru."""
        alan = self.viewport().rect()
        kenar = self.AUTOSCROLL_MARGIN
        adim = self.AUTOSCROLL_STEP

        def eksen(deger: float, alt: float, ust: float) -> float:
            if deger < alt + kenar:
                return -adim * min(1.0, (alt + kenar - deger) / kenar)
            if deger > ust - kenar:
                return adim * min(1.0, (deger - (ust - kenar)) / kenar)
            return 0.0

        return QPointF(eksen(float(pos.x()), float(alan.left()),
                             float(alan.right())),
                       eksen(float(pos.y()), float(alan.top()),
                             float(alan.bottom())))

    def _update_autoscroll(self, pos: QPoint) -> None:
        """Surukleme sirasinda kenardan kaydirmayi baslatir/durdurur."""
        self._drag_pos = QPoint(pos)
        self._autoscroll_vec = self._autoscroll_vector(pos)
        calisiyor = self._autoscroll.isActive()
        gerekli = not self._autoscroll_vec.isNull()
        if gerekli and not calisiyor:
            self._autoscroll.start()
        elif not gerekli and calisiyor:
            self._autoscroll.stop()

    def _stop_autoscroll(self) -> None:
        self._autoscroll.stop()
        self._autoscroll_vec = QPointF()
        self._drag_pos = None

    def _autoscroll_step(self) -> None:
        """Bir kaydirma tiki: goruntuyu kaydirir ve SURUKLEMEYI ilerletir.

        Yalnizca kaydirmak yetmez: fare kimildamadigi icin Qt'nin oge
        surukleme makinesi tetiklenmez ve kutu oldugu yerde kalirdi --
        imlec kutudan uzaklasirdi. Bu yuzden kaydirmadan sonra AYNI fare
        konumu yeniden islenir; kutu imleci izler.
        """
        if self._drag_pos is None or self._autoscroll_vec.isNull():
            self._stop_autoscroll()
            return
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self._stop_autoscroll()
            return

        self._room_for_drag(self._drag_pos)
        yatay = self.horizontalScrollBar()
        dikey = self.verticalScrollBar()
        yatay.setValue(yatay.value() + int(round(self._autoscroll_vec.x())))
        dikey.setValue(dikey.value() + int(round(self._autoscroll_vec.y())))

        nokta = QPointF(self._drag_pos)
        olay = QMouseEvent(QMouseEvent.Type.MouseMove, nokta, nokta,
                           Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        self.mouseMoveEvent(olay)

    # == HIZALAMA KILAVUZLARI =============================================== #
    #
    # Kullanici bir durumu surukleyince, komsulariyla AYNI SATIRDA ya da
    # AYNI SUTUNDA oldugunda oraya yapisir ve hizayi gosteren kesikli bir
    # cizgi belirir (draw.io / Visio davranisi). Elle "goz kararı" hizalama
    # bir piksel sasar; diyagram basildiginda ya da bir belgeye konuldugunda
    # bu hemen goze carpar.
    #
    # Karsilastirilan olcutler AYNI TURDEN olanlardir: sol-sol, orta-orta,
    # sag-sag (sutun) ve ust-ust, orta-orta, alt-alt (satir). Capraz
    # eslesme (ornegin benim solum, onun sagi) BILEREK yapilmaz: yakalama
    # noktalarini cogaltir ve kullanici nereye yapistigini kestiremez.

    #: Yakalama yaricapi. EKRAN pikselidir: yakinlastirmadan bagimsiz
    #: olarak ayni "his" verir. Sahne biriminde sabitlenseydi uzaklasinca
    #: her sey birbirine yapisir, yakinlasinca hicbir sey yakalanmazdi.
    ALIGN_PX = 7.0
    #: Kilavuz cizgisinin hizalanan kutularin disina tastigi pay (sahne px).
    ALIGN_PAD = 26.0

    def _init_alignment(self) -> None:
        """Hizalama durumunu kurar."""
        self.align_enabled = True
        #: Su an cizilecek kilavuzlar; sahne koordinatinda (bas, son).
        self._align_guides: List[Tuple[QPointF, QPointF]] = []

    # ------------------------------------------------------------- kilavuzlar

    def align_clear(self) -> None:
        """Kilavuzlari siler (surukleme bitince)."""
        if getattr(self, "_align_guides", None):
            self._align_guides = []
            self.viewport().update()

    def align_paint(self, painter) -> None:
        """Kilavuzlari cizer; `drawForeground` icinden cagrilir."""
        cizgiler = getattr(self, "_align_guides", None)
        if not cizgiler:
            return
        kalem = QPen(QColor(C.ORANGE), 0.0)
        # KOZMETIK kalem: kalinlik yakinlastirmayla buyumez. Sahne
        # biriminde 1 px verilseydi %400 yakinlikta 4 px kalinliginda
        # bir serit olur, hizanin kendisini gizlerdi.
        kalem.setCosmetic(True)
        kalem.setStyle(Qt.PenStyle.DashLine)
        painter.save()
        painter.setPen(kalem)
        for bas, son in cizgiler:
            painter.drawLine(bas, son)
        painter.restore()

    # --------------------------------------------------------------- yakalama

    def _align_uygun(self, item) -> bool:
        """Hizalama YALNIZCA elle surukleme sirasinda calisir.

        `itemChange` programli `setPos()` cagrilarinda da tetiklenir:
        dosya yuklerken, geri al/yinele yaparken, ok tuslariyla
        kaydirirken. Oralarda yapismak modeli sessizce degistirirdi.
        Fareyle tutulan oge sahnenin `mouseGrabberItem()` degeridir.

        Cok secimde de yapilmaz: Qt her ogeye AYRI `ItemPositionChange`
        gonderir, yalnizca tutulan oge kaydirilirsa secim daginir.
        """
        if not getattr(self, "align_enabled", True):
            return False
        sahne = self.scene()
        if sahne is None or sahne.mouseGrabberItem() is not item:
            return False
        return len(sahne.selectedItems()) <= 1

    def align_drag(self, item, pt: QPointF, boyut, komsular) -> QPointF:
        """Suruklenen ogeyi komsularina hizalar.

        ``pt``      -- onerilen sol ust kose (ogenin EBEVEYN koordinati)
        ``boyut``   -- (genislik, yukseklik)
        ``komsular``-- ayni uzaydaki komsular: [(x, y, w, h), ...]

        Hizalanmis konumu dondurur ve cizilecek kilavuzlari hazirlar.
        """
        onceki = getattr(self, "_align_guides", [])
        if not self._align_uygun(item) or not komsular:
            if onceki:
                self._align_guides = []
                self.viewport().update()
            return pt

        olcek = abs(self.transform().m11()) or 1.0
        esik = self.ALIGN_PX / olcek
        gen, yuk = float(boyut[0]), float(boyut[1])

        dx, x_hiza = self._align_eksen(pt.x(), gen, komsular, 0, esik)
        dy, y_hiza = self._align_eksen(pt.y(), yuk, komsular, 1, esik)

        yeni = QPointF(pt.x() + dx, pt.y() + dy)
        kutu = (yeni.x(), yeni.y(), gen, yuk)
        cizgiler = []
        if x_hiza is not None:
            cizgiler.append(self._align_cizgi(item, x_hiza, kutu, komsular, 0))
        if y_hiza is not None:
            cizgiler.append(self._align_cizgi(item, y_hiza, kutu, komsular, 1))

        if cizgiler != onceki:
            self._align_guides = cizgiler
            self.viewport().update()
        return yeni

    @staticmethod
    def _align_olcutler(bas: float, uzunluk: float):
        """Bir eksendeki olcutler: ORTA once gelir.

        Esitlik halinde orta hizanin secilmesi icin sira onemli: iki
        kutunun hem ortasi hem kenari ayni uzaklikta olabilir ve o
        durumda gozle en dogal duran orta hizadir.
        """
        return (bas + uzunluk / 2.0, bas, bas + uzunluk)

    def _align_eksen(self, bas: float, uzunluk: float, komsular, eksen: int,
                     esik: float):
        """Tek eksende en yakin hizayi bulur.

        ``(kaydirma, hedef_deger)`` doner; hiza yoksa ``(0.0, None)``.
        """
        benim = self._align_olcutler(bas, uzunluk)
        en_iyi = None
        for komsu in komsular:
            k_bas = komsu[eksen]
            k_uzunluk = komsu[eksen + 2]
            onun = self._align_olcutler(k_bas, k_uzunluk)
            for sira in range(3):
                fark = onun[sira] - benim[sira]
                if abs(fark) > esik:
                    continue
                aday = (abs(fark), sira, fark, onun[sira])
                if en_iyi is None or aday[:2] < en_iyi[:2]:
                    en_iyi = aday
        if en_iyi is None:
            return (0.0, None)
        return (en_iyi[2], en_iyi[3])

    def _align_cizgi(self, item, deger: float, kutu, komsular, eksen: int):
        """Kilavuz cizgisini SAHNE koordinatinda uretir.

        Cizgi, hizayi PAYLASAN butun kutulari kapsayacak kadar uzatilir;
        boylece kullanici neye hizalandigini tek bakista gorur.
        """
        # Hizayi paylasan kutular (suruklenen dahil).
        ilgili = [kutu]
        for komsu in komsular:
            olcutler = self._align_olcutler(komsu[eksen], komsu[eksen + 2])
            if any(abs(o - deger) < 0.5 for o in olcutler):
                ilgili.append(komsu)

        diger = 1 - eksen
        bas = min(k[diger] for k in ilgili) - self.ALIGN_PAD
        son = max(k[diger] + k[diger + 2] for k in ilgili) + self.ALIGN_PAD
        if eksen == 0:
            p1, p2 = QPointF(deger, bas), QPointF(deger, son)
        else:
            p1, p2 = QPointF(bas, deger), QPointF(son, deger)

        # Oge bir bilesik durumun ICINDEYSE koordinatlari o ebeveyne
        # goredir; kilavuz sahnede cizildigi icin cevrilmesi gerekir.
        ebeveyn = item.parentItem()
        if ebeveyn is not None:
            p1 = ebeveyn.mapToScene(p1)
            p2 = ebeveyn.mapToScene(p2)
        return (p1, p2)
