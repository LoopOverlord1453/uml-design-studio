"""Canli koyu tema renk paleti ve Qt stil sayfasi.

Koyu lacivert zemin uzerinde doygun, canli vurgular kullanilir; diyagram
tuvali ile kod editoru ayni paleti paylasir ve arayuz tek bir urun gibi
gorunur.
"""

from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtGui import QColor, QFont, QFontDatabase


#: KOYU palet (varsayilan). Anahtarlar iki palette de AYNI olmali;
#: eksik anahtar tema degisiminde eski rengin kalmasina yol acar --
#: apply_theme() bunu denetler.
DARK = {
    # -- yuzeyler
    "WINDOW": "#20242F",
    "PANEL": "#252A38",
    "PANEL_DARK": "#1C202B",
    "EDITOR_BG": "#171A22",
    "CANVAS_BG": "#191D27",
    "GUTTER_BG": "#1C202B",
    "BORDER": "#141821",
    # Ayirici cizgiler panel zemininden AYIRT EDILEBILIR olmali;
    # #3A4356 ile oran 1.44 idi ve cizgiler kayboluyordu.
    "BORDER_LIGHT": "#454F66",
    "HOVER": "#323A4E",
    "SELECTION": "#2456A6",
    "CURRENT_LINE": "#232838",
    # -- metin
    "TEXT": "#D6DBE6",
    "TEXT_DIM": "#8A93A6",
    "TEXT_BRIGHT": "#EDF1F8",
    "GUTTER_TEXT": "#5A6377",
    # -- uretilen kod paneli (TEMAYI IZLER)
    #
    # Panel eskiden Visual Studio klasik semasini kullaniyor ve arayuz
    # temasindan BAGIMSIZ olarak hep beyaz kaliyordu; koyu temada ekranin
    # yarisi beyaz parliyordu. Artik tema ile birlikte degisir ve sema
    # sadelestirilmistir: YALNIZCA yorumlar yesil, kodun geri kalani duz
    # metin rengindedir.
    "CODE_BG": "#171A22",
    "CODE_TEXT": "#DCE3EF",
    "CODE_COMMENT": "#6A9955",
    # Sozdizimi semasi -- KOYU zemin icin. Ton sayisi bilerek sinirli:
    # her belirtec turune ayri renk vermek kodu alacali yapar ve
    # okunurlugu DUSURUR. Bes anlam grubu var: anahtar kelime, tip,
    # dize, sayi/sabit, onislemci.
    "CODE_KEYWORD": "#569CD6",
    "CODE_TYPE": "#4EC9B0",
    "CODE_STRING": "#CE9178",
    "CODE_NUMBER": "#B5CEA8",
    "CODE_PREPROC": "#C586C0",
    "CODE_FUNCTION": "#DCDCAA",
    "CODE_CURRENT_LINE": "#1F2430",
    "CODE_GUTTER_BG": "#12151C",
    "CODE_GUTTER_TEXT": "#8994AC",
    "CODE_GUTTER_CUR": "#EDF1F8",
    # Kod panelindeki uyari / hata bandinin zemini.
    # Uzerine RED yazilir; #4B2B2B ile oran 4.13 idi (4.5 alti).
    "BANNER_ERROR_BG": "#3A2020",
    "BANNER_WARN_BG": "#4A4326",
    # -- vurgular
    "ACCENT": "#4D9FFF",  # secim / odak mavisi
    "ACCENT_DARK": "#2456A6",
    "ORANGE": "#FF9F43",  # anahtar kelime
    "YELLOW": "#FFD166",  # fonksiyon adi
    "GREEN": "#3DDC84",  # dize / basari
    "BLUE": "#6FB3FF",  # sayi
    "PURPLE": "#B983FF",  # sabit / makro
    "OLIVE": "#C7D66D",  # onislemci
    "DOC_GREEN": "#5FBF87",  # dokuman yorumu
    "RED": "#FF5C5C",  # hata
    "WARN": "#FFB020",  # uyari
    "INFO": "#4D9FFF",  # bilgi
    "CYAN": "#35D0BA",  # ikincil vurgu
    # -- diyagram
    "STATE_FILL": "#2A3145",
    "STATE_FILL_ALT": "#273041",  # bilesik durumun govdesi
    "STATE_HEADER": "#3B6FD4",  # basit durum baslik seridi
    "STATE_HEADER_ALT": "#2E9E8F",  # bilesik durum baslik seridi
    "STATE_BORDER": "#5B6B8F",
    "STATE_TEXT": "#D6DBE6",
    "STATE_TITLE": "#FFFFFF",
    "STATE_SELECTED": "#4D9FFF",
    "STATE_HOVER": "#8FA7D9",
    "STATE_ERROR": "#FF5C5C",
    "GRID_MINOR": "#1F2431",
    "GRID_MAJOR": "#262C3C",
    "TRANSITION": "#9FB0CC",
    "TRANSITION_SEL": "#4D9FFF",
    # Gecis etiketinin zemini: TUVAL rengiyle ayni. Amaci altindaki oku
    # maskelemek, kutucuk gibi gorunmek DEGIL.
    "LABEL_BG": "#191D27",
    "LABEL_TEXT": "#F2F5FA",
    "SIM_ACTIVE": "#2EE59D",  # simulasyonda etkin durum halesi
    "PSEUDO_FILL": "#E8ECF4",
    # INITIAL ve FINAL, UML 2.5.1 §14.2.4.7'deki gibi TEK RENK cizilir:
    # initial dolu bir daire, final ic ice iki daire. Standart bunlari SIYAH
    # gosterir; koyu temada duz siyah, koyu tuval uzerinde (1.2:1) gorunmez
    # olurdu, bu yuzden ayni MUREKKEP acik tona cevrilir. Acik temada
    # gercekten siyahtir.
    "INITIAL_FILL": "#E8ECF4",  # initial sozde-durumu (murekkep)
    "FINAL_RING": "#E8ECF4",  # final durumun halkasi ve cekirdegi
    "CHOICE_FILL": "#FFB020",  # choice/junction elmasi
    "HISTORY_FILL": "#B983FF",  # tarih sozde-durumlari
    "TERMINATE_FILL": "#FF5C5C",  # terminate sozde-durumu
    # -- sinif diyagrami
    "CLASS_HEADER": "#7E57C2",  # sinif baslik seridi
    "IFACE_HEADER": "#00897B",  # <<interface>> baslik seridi
    "ABSTRACT_HEADER": "#5C6BC0",  # soyut sinif baslik seridi
    "CLASS_FILL": "#2A3145",
    "CLASS_TEXT": "#D6DBE6",
    # -- git paneli
    "GIT_ADD": "#3DDC84",  # eklenen satir metni
    "GIT_ADD_BG": "#1B3A2A",  # eklenen satir zemini
    "GIT_DEL": "#FF7A7A",  # silinen satir metni
    "GIT_DEL_BG": "#3A1F24",  # silinen satir zemini
    "GIT_HUNK": "#6FB3FF",  # @@ basligi
    "GIT_META": "#8A93A6",  # diff/index basliklari
    "GIT_NODE": "#EDF1F8",  # commit dugumu cekirdegi
    "GIT_HEAD_RING": "#FFD166",  # HEAD halkasi
    "GIT_REF_BRANCH": "#2E7D5B",  # dal etiketi zemini
    "GIT_REF_REMOTE": "#4A5568",  # uzak dal etiketi zemini
    "GIT_REF_TAG": "#7A5C1E",  # etiket (tag) zemini
    "GIT_REF_HEAD": "#2456A6",  # HEAD etiketi zemini
    "GIT_STAGED": "#3DDC84",  # hazirlanmis dosya isareti
    "GIT_UNSTAGED": "#FF9F43",  # hazirlanmamis dosya isareti
    "GIT_CONFLICT": "#FF5C5C",  # cakisma
    # -- ZITLIK belirteci: zemin uzerine yazilan metnin rengi. Sabit bir
    # "#FFFFFF" iki temada da dogru OLAMAZ; her yuzey icin ayri tutulur.
    "ON_ACCENT": "#FFFFFF",  # vurgu (accent) zemini uzerindeki metin
    "SELECTED_TEXT": "#FFFFFF",  # secili liste satirinin metni
    "SELECTION_TEXT": "#FFFFFF",  # metin alanindaki secimin rengi
    "ALT_ROW": "#20242F",  # siralamali listelerde tek satir zemini
    "SCROLL_HANDLE": "#4E5254",  # kaydirma cubugu tutamagi
    "SCROLL_HANDLE_HOVER": "#5E6365",
}

#: ACIK palet. Anahtar kumesi DARK ile BIREBIR AYNIDIR.
#:
#: Renkler koyu paletten mekanik olarak ters cevrilmedi: dogrudan cevirme
#: vurgu renklerini beyaz zeminde okunmaz hale getirir (ornegin #3DDC84
#: yesili beyazda kayboluyor). Her vurgu, acik zeminde AYNI ANLAMI tasiyan
#: ama kontrasti yeterli bir tona ayri ayri esitlendi.
LIGHT = {
    # -- yuzeyler
    "WINDOW": "#EEF1F6",
    "PANEL": "#F7F9FC",
    "PANEL_DARK": "#E7EBF2",
    "EDITOR_BG": "#FFFFFF",
    "CANVAS_BG": "#F6F8FC",
    "GUTTER_BG": "#EDF0F6",
    "BORDER": "#C7CEDB",
    "BORDER_LIGHT": "#AEB8C9",
    "HOVER": "#DCE3EF",
    "SELECTION": "#B7D4FF",
    "CURRENT_LINE": "#EDF2FB",
    # -- metin
    "TEXT": "#1F2733",
    "TEXT_DIM": "#5E6A7D",
    "TEXT_BRIGHT": "#0C1119",
    "GUTTER_TEXT": "#98A2B3",
    # -- uretilen kod paneli (bkz. DARK'taki aciklama)
    "CODE_BG": "#FFFFFF",
    "CODE_TEXT": "#14181F",
    "CODE_COMMENT": "#007A1F",
    # Sozdizimi semasi -- ACIK zemin icin. Ayni bes anlam grubu, beyaz
    # uzerinde 4.5:1 kontrasti gecen tonlarla.
    "CODE_KEYWORD": "#0033B3",
    "CODE_TYPE": "#00627A",
    "CODE_STRING": "#A31515",
    "CODE_NUMBER": "#1750EB",
    "CODE_PREPROC": "#7A3E9D",
    "CODE_FUNCTION": "#795E26",
    "CODE_CURRENT_LINE": "#F1F4FA",
    "CODE_GUTTER_BG": "#F3F5F9",
    "CODE_GUTTER_TEXT": "#5A6478",
    "CODE_GUTTER_CUR": "#0C1119",
    # Acik temada bant da ACIK olmali; koyu tema degerleri sabit kalinca
    # beyaz arayuzde koyu kahverengi lekeler olusuyordu.
    "BANNER_ERROR_BG": "#FDECEC",
    "BANNER_WARN_BG": "#FFF6E0",
    # -- vurgular
    "ACCENT": "#1668D6",
    "ACCENT_DARK": "#0F4EA8",
    "ORANGE": "#B45309",
    "YELLOW": "#8A6100",
    "GREEN": "#137A46",
    "BLUE": "#1D5FBF",
    "PURPLE": "#6B34C4",
    "OLIVE": "#5E6B12",
    "DOC_GREEN": "#1B6B47",
    "RED": "#C62828",
    "WARN": "#A65B00",
    "INFO": "#1668D6",
    "CYAN": "#0A6E63",
    # -- diyagram
    # Acik temada diyagram: beyaz govde + doygun baslik seridi. Baslik
    # tonlari, uzerlerindeki BEYAZ yazi 4.5:1 kontrasti gecsin diye
    # koyulastirildi; govde ile tuval arasindaki fark da kutulari zeminden
    # ayirmaya yetecek kadar acik birakildi.
    "STATE_FILL": "#FFFFFF",
    "STATE_FILL_ALT": "#EEF3FB",
    "STATE_HEADER": "#2563C7",
    "STATE_HEADER_ALT": "#12766B",
    "STATE_BORDER": "#8494B0",
    "STATE_TEXT": "#1F2733",
    "STATE_TITLE": "#FFFFFF",
    "STATE_SELECTED": "#1668D6",
    "STATE_HOVER": "#5B7FBF",
    "STATE_ERROR": "#C62828",
    "GRID_MINOR": "#E9EDF5",
    "GRID_MAJOR": "#D9E0EC",
    "TRANSITION": "#44506A",
    "TRANSITION_SEL": "#1668D6",
    # Acik temada beyaz kutucuklar acik gri tuvalde "etrafi belirgin
    # olmayan" lekeler gibi duruyordu; zemin tuvalle ayni, yazi SIMSIYAH.
    "LABEL_BG": "#F6F8FC",
    "LABEL_TEXT": "#000000",
    # Acik zeminde okunacak kadar KOYU olmali: simulasyondaki etkin durum
    # etiketi acik gri panel uzerine yazilir ve panelin en kritik bilgisidir.
    "SIM_ACTIVE": "#0A6B3A",
    "PSEUDO_FILL": "#2A3145",
    # Acik temada murekkep SIYAHTIR (bkz. DARK'taki aciklama).
    "INITIAL_FILL": "#0B0F16",
    "FINAL_RING": "#0B0F16",
    "CHOICE_FILL": "#C98000",
    "HISTORY_FILL": "#6B34C4",
    "TERMINATE_FILL": "#C62828",
    # -- sinif diyagrami
    "CLASS_HEADER": "#5E35B1",
    "IFACE_HEADER": "#00695C",
    "ABSTRACT_HEADER": "#3949AB",
    "CLASS_FILL": "#FFFFFF",
    "CLASS_TEXT": "#1F2733",
    # -- git paneli
    "GIT_ADD": "#137A46",
    "GIT_ADD_BG": "#DFF5E8",
    "GIT_DEL": "#C62828",
    "GIT_DEL_BG": "#FBE3E3",
    "GIT_HUNK": "#1D5FBF",
    "GIT_META": "#5E6A7D",
    "GIT_NODE": "#0C1119",
    "GIT_HEAD_RING": "#B8860B",
    "GIT_REF_BRANCH": "#BFE7D2",
    "GIT_REF_REMOTE": "#D7DDE8",
    "GIT_REF_TAG": "#F2E2B8",
    "GIT_REF_HEAD": "#C8DEFB",
    "GIT_STAGED": "#137A46",
    "GIT_UNSTAGED": "#B45309",
    "GIT_CONFLICT": "#C62828",
    # Acik temada secim zemini KOYU mavidir (ACCENT_DARK), bu yuzden uzerindeki
    # metin beyaz kalir; ama siralamali satir zemini ve kaydirma tutamagi acik
    # tonlara ceker -- koyu paletten kopyalanirsa beyaz uzerinde koyu lekeler
    # olusur ve satirlar okunmaz.
    "ON_ACCENT": "#FFFFFF",
    "SELECTED_TEXT": "#FFFFFF",
    # Metin alanlarinda secim zemini ACIK mavidir (SELECTION); uzerine beyaz
    # yazmak metni yok eder, bu yuzden burada KOYU on plan kullanilir.
    "SELECTION_TEXT": "#0C1119",
    "ALT_ROW": "#F0F3F9",
    "SCROLL_HANDLE": "#B4BECD",
    "SCROLL_HANDLE_HOVER": "#93A0B3",
}


#: Kullanilabilir temalar (ayar dosyasinda saklanan anahtar -> gorunen ad).
THEMES = {"dark": "Dark", "light": "Light"}

_active = ["dark"]


class C:
    """CANLI tema renkleri.

    Nitelikleri ``apply_theme()`` doldurur. Sinif govdesinde sabit deger
    TUTULMAZ: iki paletin ayni anahtar kumesini tasidigi tek yerden
    (asagidaki denetim) dogrulanabilsin ve tema degistiginde TEK kaynak
    guncellensin diye.
    """


def active_theme() -> str:
    return _active[0]


def apply_theme(name: str) -> str:
    """Paleti degistirir; gecerli tema adini dondurur.

    Bilinmeyen ad KOYU temaya duser -- ayar dosyasindaki bozuk bir deger
    yuzunden uygulama acilmamazlik etmemeli.
    """
    if name not in THEMES:
        name = "dark"
    palette = LIGHT if name == "light" else DARK
    for key, value in palette.items():
        setattr(C, key, value)
    _active[0] = name
    return name


# Iki palet ayni anahtarlari tasimali: eksik bir anahtar tema degisiminde
# ONCEKI temanin rengini birakir ve arayuzde okunmaz bir leke olusur.
# Bu, ancak calisma aninda fark edilebilecek bir hatadir; import aninda
# yakalanir.
_eksik_light = sorted(set(DARK) - set(LIGHT))
_eksik_dark = sorted(set(LIGHT) - set(DARK))
if _eksik_light or _eksik_dark:
    raise RuntimeError(
        "Tema paletleri ayrismis - LIGHT'ta eksik: %s | DARK'ta eksik: %s"
        % (_eksik_light, _eksik_dark))

# Bozuk bir renk degeri Qt tarafindan SESSIZCE yok sayilir: widget siyah
# yada saydam cizilir ve sebebi hicbir yerde gorunmez. Bir kez oldu
# ("#4A5costs"), bu yuzden bicim import aninda dogrulanir.
_bozuk = sorted(
    "%s.%s = %r" % (ad, anahtar, deger)
    for ad, palet in (("DARK", DARK), ("LIGHT", LIGHT))
    for anahtar, deger in palet.items()
    if not (isinstance(deger, str) and len(deger) == 7 and deger[0] == "#"
            and all(ch in "0123456789abcdefABCDEF" for ch in deger[1:])))
if _bozuk:
    raise RuntimeError("Invalid colour value: %s" % ", ".join(_bozuk))

apply_theme("dark")


def qc(name: str, alpha: int = 255) -> QColor:
    col = QColor(name)
    col.setAlpha(alpha)
    return col


# --------------------------------------------------------------------------- #
#  Yazi tipleri  --  JetBrains Mono
# --------------------------------------------------------------------------- #
#
# Arayuzun tamami JetBrains Mono ile yazilir. Yazi tipi SISTEME KURULU OLMAK
# ZORUNDA DEGILDIR: varsa `assets/fonts/` altindaki .ttf dosyalari uygulama
# icine yuklenir (QFontDatabase.addApplicationFont). Boylece paketlenmis
# EXE'de de ayni gorunum elde edilir.
#
# Dosya yoksa uygulama COKMEZ; asagidaki yedek zinciri devreye girer ve
# arayuz calismaya devam eder. Yazi tipi bir suslemedir, calisma sarti degil.

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "..", "assets", "fonts")

#: Tercih sirasi: once JetBrains, sonra platformun makul karsiliklari.
MONO_STACK = ("JetBrains Mono", "JetBrains Mono NL", "Cascadia Mono",
              "Consolas", "DejaVu Sans Mono", "Courier New")
UI_STACK = ("JetBrains Mono", "JetBrains Sans", "Inter",
            "Segoe UI Variable Text", "Segoe UI", "Noto Sans")

_fonts_loaded = False
_loaded_families: List[str] = []


def load_bundled_fonts() -> List[str]:
    """`assets/fonts/*.ttf|*.otf` dosyalarini uygulamaya yukler.

    Bir kez calisir; yuklenen aile adlarini dondurur. QApplication
    olusturulduktan SONRA cagrilmalidir -- oncesinde font veritabani yoktur.
    """
    # `_loaded_families` YERINDE degistirilir (append), yeniden baglanmaz;
    # bu yuzden global bildirimi yalnizca `_fonts_loaded` icindir.
    global _fonts_loaded
    if _fonts_loaded:
        return _loaded_families

    _fonts_loaded = True
    kok = os.path.normpath(FONT_DIR)
    if not os.path.isdir(kok):
        return _loaded_families

    for ad in sorted(os.listdir(kok)):
        if not ad.lower().endswith((".ttf", ".otf")):
            continue
        fid = QFontDatabase.addApplicationFont(os.path.join(kok, ad))
        if fid < 0:
            continue
        for aile in QFontDatabase.applicationFontFamilies(fid):
            if aile not in _loaded_families:
                _loaded_families.append(aile)
    return _loaded_families


def _first_available(stack) -> Optional[str]:
    families = QFontDatabase.families()
    for name in stack:
        if name in families:
            return name
    return None


#: Arayuzdeki TUM yazi boyutlarina uygulanan kaydirma (punto).
#:
#: Kullanici: "yazilari fontlari biraz kucult, astah uml gibi olsun,
#: tuval buyuk olsun." Boyutlar 100'den fazla cagri yerinde tek tek
#: yaziliyor; tek bir kaydirma hepsini ORANTIYI BOZMADAN kucultur.
FONT_DELTA = -1

#: Punto bu degerin altina dusmez (okunaksizlik siniri).
FONT_MIN_PT = 7


def scaled_pt(size: int) -> int:
    """Cagri yerindeki puntoyu arayuz olceginde dondurur."""
    return max(FONT_MIN_PT, size + FONT_DELTA)


def mono_font(size: int = 11) -> QFont:
    """Kod ve tablolar icin tek aralikli yazi tipi (JetBrains Mono)."""
    load_bundled_fonts()
    size = scaled_pt(size)
    name = _first_available(MONO_STACK)
    f = QFont(name or "monospace", size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    return f


def ui_font(size: int = 9) -> QFont:
    """Arayuz yazi tipi (JetBrains Mono; yoksa yedek zinciri)."""
    load_bundled_fonts()
    size = scaled_pt(size)
    name = _first_available(UI_STACK)
    if name is None:
        return QFont("sans-serif", size)
    return QFont(name, size)


def font_report() -> str:
    """Yardim > Hakkinda icin: hangi yazi tipi gercekten kullaniliyor."""
    load_bundled_fonts()
    return "UI: %s   Mono: %s" % (_first_available(UI_STACK) or "sans-serif",
                                  _first_available(MONO_STACK) or "monospace")


STYLESHEET = """
* {{
    outline: 0;
}}

QWidget {{
    background: {panel};
    color: {text};
    selection-background-color: {sel};
    selection-color: {seltextfield};
}}

QMainWindow, QDialog {{
    background: {panel};
}}

/* ------------------------------------------------------------ menu / arac */
QMenuBar {{
    background: {panel};
    border-bottom: 1px solid {border};
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
    border-radius: 4px;
}}
QMenuBar::item:selected {{ background: {hover}; }}
QMenuBar::item:pressed  {{ background: {accentdark}; }}

QMenu {{
    background: {paneldark};
    border: 1px solid {borderlight};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 28px 6px 24px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background: {accentdark}; color: {onaccent}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{
    height: 1px;
    background: {borderlight};
    margin: 5px 8px;
}}

QToolBar {{
    background: {panel};
    border: none;
    border-bottom: 1px solid {border};
    padding: 3px 6px;
    spacing: 3px;
}}
QToolBar::separator {{
    width: 1px;
    background: {borderlight};
    margin: 5px 6px;
}}
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 7px;
    color: {text};
}}
/* Uc serit tek satira indirildi; menu dugmeleri dar tutulmazsa
   diyagram araclari sagdan tasip tasma dugmesine dusuyor. */
QToolBar#topBar QToolButton {{ padding: 3px 5px; margin: 0 1px; }}
QToolBar#topBar QToolButton::menu-indicator {{ image: none; width: 0; }}
/* SECILI ARAC BELLI OLSUN: kullanici "sectigi toolu gorsun" dedi.
   Sadece zemin degil, KALIN yazi da ayirt edicidir. */
QToolBar#topBar QToolButton:checked {{ font-weight: 700; }}
QToolButton:hover  {{ background: {hover}; border-color: {borderlight}; }}
QToolButton:pressed{{ background: {accentdark}; }}
QToolButton:checked{{
    background: {accentdark};
    border-color: {accent};
    color: {onaccent};
}}
QToolButton:disabled {{ color: {dim}; }}

/* ------------------------------------------------------------------ dock */
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {textbright};
    font-weight: 600;
}}
QDockWidget::title {{
    background: {paneldark};
    padding: 6px 10px;
    border-bottom: 1px solid {border};
}}

QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:vertical   {{ height: 3px; }}
QSplitter::handle:hover      {{ background: {accentdark}; }}

/* --------------------------------------------------------------- sekmeler */
QTabWidget::pane {{
    border: 1px solid {border};
    background: {editor};
    top: -1px;
}}
QTabBar {{ background: {paneldark}; }}
QTabBar::tab {{
    background: {paneldark};
    color: {dim};
    padding: 7px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
}}
QTabBar::tab:hover {{ color: {text}; background: {hover}; }}
QTabBar::tab:selected {{
    color: {textbright};
    background: {editor};
    border-bottom: 2px solid {orange};
}}

/* ---------------------------------------------------------------- girisler */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {editor};
    border: 1px solid {borderlight};
    border-radius: 4px;
    padding: 4px 6px;
    color: {textbright};
    selection-background-color: {sel};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    color: {dim};
    background: {paneldark};
}}

QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {paneldark};
    border: 1px solid {borderlight};
    selection-background-color: {accentdark};
    color: {textbright};
    padding: 2px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background: {panel};
    border: none;
    width: 14px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {hover}; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {borderlight};
    border-radius: 3px;
    background: {editor};
}}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
}}

QPushButton {{
    background: {panel};
    border: 1px solid {borderlight};
    border-radius: 4px;
    padding: 6px 14px;
    color: {text};
}}
QPushButton:hover {{ background: {hover}; border-color: {accent}; }}
QPushButton:pressed {{ background: {accentdark}; }}
QPushButton:default {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {dim}; border-color: {border}; }}

/* --------------------------------------------------------------- listeler */
QTreeWidget, QListWidget, QTableWidget {{
    background: {editor};
    border: 1px solid {border};
    alternate-background-color: {altrow};
    color: {text};
}}
QTreeWidget::item, QListWidget::item {{
    padding: 4px 2px;
    border: none;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {hover};
    color: {textbright};
}}
QTreeWidget::item:selected, QListWidget::item:selected,
QTreeWidget::item:selected:active, QListWidget::item:selected:active,
QTreeWidget::item:selected:!active, QListWidget::item:selected:!active {{
    background: {accentdark};
    color: {seltext};
    font-weight: bold;
}}
QHeaderView::section {{
    background: {paneldark};
    color: {dim};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}

/* ----------------------------------------------------------- kaydirma cubugu */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {scroll};
    min-height: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {scrollhover}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {scroll};
    min-width: 28px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {scrollhover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -------------------------------------------------------------- durum cub. */
QStatusBar {{
    background: {paneldark};
    border-top: 1px solid {border};
    color: {dim};
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 8px; background: transparent; }}

/* Derleme ilerleme cubugu (durum cubugunun en saginda).

   METIN YOKTUR ve bu bilincli bir tercihtir: yuzde yazisi hem DOLU hem BOS
   kismin uzerinden gecer, dolayisiyla TEK bir renk iki zeminde birden
   okunamaz (koyu temada parlak metin, mavi dolgu uzerinde 2.4:1 kaliyordu;
   dolguya gore secilen renk de bos izde okunmuyordu). Hangi adimin
   calistigi zaten durum cubugunun SOLUNDA yaziyla gorunur. */
QProgressBar {{
    background: {editor};
    border: 1px solid {border};
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background: {accent};
    border-radius: 2px;
}}

QGroupBox {{
    border: 1px solid {border};
    border-radius: 5px;
    margin-top: 14px;
    padding-top: 8px;
    color: {dim};
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}

QLabel {{ background: transparent; }}
QToolTip {{
    background: {paneldark};
    color: {textbright};
    border: 1px solid {accent};
    padding: 5px 8px;
}}

QGraphicsView {{
    background: {canvas};
    border: none;
}}
"""


def stylesheet() -> str:
    return STYLESHEET.format(
        panel=C.PANEL, paneldark=C.PANEL_DARK, editor=C.EDITOR_BG,
        canvas=C.CANVAS_BG, border=C.BORDER, borderlight=C.BORDER_LIGHT,
        hover=C.HOVER, sel=C.SELECTION, text=C.TEXT, textbright=C.TEXT_BRIGHT,
        dim=C.TEXT_DIM, accent=C.ACCENT, accentdark=C.ACCENT_DARK,
        orange=C.ORANGE, onaccent=C.ON_ACCENT, seltext=C.SELECTED_TEXT,
        altrow=C.ALT_ROW, scroll=C.SCROLL_HANDLE,
        scrollhover=C.SCROLL_HANDLE_HOVER, seltextfield=C.SELECTION_TEXT,
    )
