"""Ana pencere: iki diyagram kipi (durum makinesi + sinif), uretilen kod paneli.

Yerlesim:
  * Ust arac cubugu: dosya/duzen/gorunum/uretim
  * Sol dikey arac cubugu: etkin diyagram kipinin araclari (adlar simge altinda)
  * Merkez: "State Diagram" / "Class Diagram" sekmeleri
      - Durum kipi: MODEL AGACI + OZELLIKLER | SIMULASYON (ustte) + tuval + SORUNLAR
      - Sinif kipi: tuval + SORUNLAR
  * Sag: uretilen kod paneli (F9 ile gizlenir/acilir)
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                             QFileDialog, QFrame, QLabel, QMainWindow,
                             QMessageBox, QPlainTextEdit, QProgressBar,
                             QProxyStyle, QStackedWidget,
                             QSplitter, QStyle, QTabWidget, QToolBar,
                             QToolButton, QVBoxLayout,
                             QWidget)


class _QuietMnemonicStyle(QProxyStyle):
    """Erisim harflerinin altini cizmeyen stil.

    Fusion, SH_UnderlineShortcut ipucuna KOSULSUZ 1 doner; Windows'un
    "Alt'a basilana kadar gizle" davranisi Fusion'da yoktur, bu yuzden
    "File", "References", "Help" yazilari hep alti cizili gorunur. Ipucunu
    0'a cekmek yalnizca CIZGIYI kaldirir: Alt+F ve arkadaslari calismaya
    devam eder, cunku kisayol eslesmesi ayri bir mekanizmadir.
    """

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, returnData)

from ..codegen.c_generator import generate_c
from ..codegen.class_c_generator import generate_class_c
from ..codegen.class_cpp_generator import generate_class_cpp
from ..codegen.class_plantuml_generator import generate_class_plantuml
from ..codegen.cpp_generator import generate_cpp
from ..codegen.ir import CodegenError
from ..codegen.plantuml_generator import generate_plantuml
from ..core.class_validator import validate_classes
from ..core.git_backend import GitError, Repo, git_available
from ..core.examples import CLASS_EXAMPLES, STATE_EXAMPLES
from ..core.samples import (demo_class_model, demo_machine, empty_class_model,
                            empty_machine)
from ..core.validator import Issue, has_errors, validate
from ..core.model_diff import element_status
from ..core.workspace import (Workspace, WorkspaceError, normalise_recent,
                              push_recent)
from . import icons
from .canvas import DiagramCanvas, Tool
from .class_canvas import ClassCanvas, ClassTool
from .code_panel import LANGUAGES, CodePanel
from .document import KIND_CLASS, Document, detect_kind
from .git_panel import GitPanel
from .inspector import Inspector
from .panels import ProblemsPanel
from .sim_panel import SimulatorPanel
from .theme import (C, THEMES, active_theme, apply_theme, mono_font,
                    stylesheet, ui_font)
from .workspace_dialog import pick_workspace
from .workspace_tree import WorkspaceTree

APP_NAME = "UML Design Studio"
APP_VERSION = "2.0.0"

#: Ust bardaki simge boyutu. Astah benzeri sik bir serit icin
#: kucuk tutulur; uc satir tek satira indirilirken yukseklik
#: buradan belirlenir.
TOOLBAR_ICON = 16
#: Ust bardaki menu dugmelerinin punto olcusu. Arayuzun geri kalani 9
#: punto; menuler simge kipindeki serit icinde bundan da kucuk gorunuyordu.
MENU_POINT = 10

#: Ayarlarin yazildigi kurulus / uygulama adi.
SETTINGS_ORG = "UmlDesignStudio"
SETTINGS_APP = "UmlStateDiagramTool"


def app_settings() -> QSettings:
    """Uygulama ayarlari -- testlerde AYRI bir depoya yazar.

    NEDEN: arayuz testleri gercek bir MainWindow kurar ve
    `apply_workspace` calisma alanini "son kullanilanlar" ile
    "last_workspace"e YAZAR. Bu ayarlar kullanicinin kendi deposuna
    gidince, testin gecici klasoru (ornegin ``.../umlui_9z0xku7g/calisma``)
    kullanicinin listesine sizip acilista karsisina cikiyordu -- klasor
    silindikten sonra bile, bos bir calisma alani olarak.

    `UMLSTUDIO_SETTINGS_SCOPE` tanimliysa uygulama adina eklenir; boylece
    test sureclerinin yazdiklari kullanicinin ayarlarina hic dokunmaz.
    """
    kapsam = os.environ.get("UMLSTUDIO_SETTINGS_SCOPE", "").strip()
    ad = "%s-%s" % (SETTINGS_APP, kapsam) if kapsam else SETTINGS_APP
    return QSettings(SETTINGS_ORG, ad)
SM_FILTER = "State machine (*.usm);;JSON (*.json);;All files (*)"
CD_FILTER = "Class diagram (*.ucd);;JSON (*.json);;All files (*)"

def _mnemonic(action, text: str) -> None:
    """Eylemin menu metnini ERISIM HARFI (&) ile birlikte yazar.

    Arac cubugu ipuclari `&` icermemeli; Qt'nin `QAction.text()` degeri
    hem menude hem ipucunda kullanildigi icin ipucu ayrica korunur.
    """
    ipucu = action.toolTip()
    action.setText(text)
    if ipucu and "&" not in ipucu:
        action.setToolTip(ipucu)


STATE_TOOLS = [
    (Tool.SELECT, "Select", "V", icons.select_icon, "Select / move / resize  (V)"),
    (Tool.STATE, "State", "S", icons.state_icon, "State  (S)"),
    (Tool.COMPOSITE, "Composite", "G", icons.composite_icon,
     "Composite State  (G)"),
    (Tool.INITIAL, "Initial", "I", icons.initial_icon, "Initial Pseudostate  (I)"),
    (Tool.FINAL, "Final", "F", icons.final_icon, "Final State  (F)"),
    (Tool.CHOICE, "Choice", "C", icons.choice_icon, "Choice Pseudostate  (C)"),
    (Tool.JUNCTION, "Junction", "J", icons.junction_icon,
     "Junction Pseudostate  (J)"),
    (Tool.SHALLOW_HISTORY, "History", "H", icons.shallow_history_icon,
     "Shallow History (H)"),
    (Tool.DEEP_HISTORY, "Deep History", "D", icons.deep_history_icon,
     "Deep History (H*)  (D)"),
    (Tool.TERMINATE, "Terminate", "X", icons.terminate_icon,
     "Terminate Pseudostate  (X)"),
    # Fork/join yalnizca ORTOGONAL durumlarla anlamlidir; harfler iki
    # palette de bostu (bkz. tools/test_standards.py, kisayol tekilligi).
    (Tool.FORK, "Fork", "W", icons.fork_icon,
     "Fork Pseudostate: split into orthogonal regions  (W)"),
    (Tool.JOIN, "Join", "Q", icons.join_icon,
     "Join Pseudostate: wait for all orthogonal regions  (Q)"),
    (Tool.ENTRY_POINT, "Entry Point", "U", icons.entry_point_icon,
     "Entry Point: a named way into a composite state  (U)"),
    (Tool.EXIT_POINT, "Exit Point", "O", icons.exit_point_icon,
     "Exit Point: a named way out of a composite state  (O)"),
    (Tool.SUBMACHINE, "Submachine", "M", icons.submachine_icon,
     "Submachine State: reuse another state machine  (M)"),
    (Tool.TRANSITION, "Transition", "T", icons.transition_icon,
     "Transition: source first, then target  (T)"),
]

#: Sinif kipi araclari.
#:
#: ADLAR KISALTILMAZ. Onceden menude "Assoc." / "Aggreg." / "General."
#: yaziyordu; menu ogesi kisaltmak arayuz kilavuzlarina aykiridir ve
#: UML terimini taniyan biri icin bile okunaksizdir.
#:
#: Arayuz harfi (`I`) DURUM kipindeki "Initial" ile cakisiyordu: Qt ayni
#: pencerede iki ayni kisayol gorunce "Ambiguous shortcut" der ve HICBIRI
#: calismayabilir. Interface artik `E` kullanir.
CLASS_TOOLS = [
    (ClassTool.SELECT, "Select", "V", icons.select_icon,
     "Select / move / resize  (V)"),
    (ClassTool.CLASS, "Class", "K", icons.class_icon, "Class  (K)"),
    (ClassTool.ABSTRACT, "Abstract Class", "B", icons.abstract_icon,
     "Abstract Class  (B)"),
    (ClassTool.INTERFACE, "Interface", "E", icons.interface_icon,
     "«interface»  (E)"),
    (ClassTool.ASSOCIATION, "Association", "A", icons.association_icon,
     "Association  (A)"),
    (ClassTool.AGGREGATION, "Aggregation", "R", icons.aggregation_icon,
     "Aggregation: WHOLE first, then PART  (R)"),
    (ClassTool.COMPOSITION, "Composition", "P", icons.composition_icon,
     "Composition: WHOLE first, then PART  (P)"),
    (ClassTool.GENERALIZATION, "Generalization", "N",
     icons.generalization_icon,
     "Generalization: subclass first, then superclass  (N)"),
    (ClassTool.REALIZATION, "Realization", "Z", icons.realization_icon,
     "Realization: class first, then interface  (Z)"),
    (ClassTool.DEPENDENCY, "Dependency", "Y", icons.dependency_icon,
     "Dependency  (Y)"),
]

REFERENCES = [
    ("OMG UML 2.5.1 Specification",
     "Every bit of diagram semantics and notation in this tool comes from "
     "that standard (Clause 9 Classification, Clause 14 StateMachines).\n"
     "OMG document no: formal/2017-12-05 — https://www.omg.org/spec/UML/2.5.1"),
    ("MISRA C:2012 — Guidelines for the use of the C language in critical systems",
     "The shape and the restrictions of the generated C code (no dynamic "
     "memory, no recursion, a default branch in every switch, width-explicit "
     "types) follow this guideline."),
    ("MISRA C++:2008 / AUTOSAR C++14 Guidelines",
     "The restrictions in the generated C++ code (no exceptions or RTTI, "
     "copying disabled, explicit conversions) rest on these guidelines."),
    ("ISO/IEC 9899:2011 (C11, GNU extensions) and ISO/IEC 14882:2011 (C++11)",
     "The target language standards of the generated code."),
    ("Miro Samek — Practical UML Statecharts in C/C++ (2nd edition)",
     "The table-driven, dynamic-memory-free embedded implementation approach "
     "used for the hierarchical state machine."),
    ("Gamma, Helm, Johnson, Vlissides — Design Patterns",
     "The patterns used to map class relationships (interface, composition, "
     "inheritance) onto code. The State and Singleton patterns shape the "
     "generated state machine."),
    ("Bruce Powel Douglass — Design Patterns for Embedded Systems in C",
     "State machine patterns for embedded C: table-driven dispatch, object "
     "structures without dynamic memory, and the separation of a "
     "hardware-independent context."),
    ("Qt 6 / PyQt6 documentation",
     "The UI widgets and the graphics scene architecture."),
    ("Git version control — git-scm.com documentation",
     "The commands and stable output formats the repository panel relies on "
     "(status --porcelain=v2, log --topo-order --parents, diff)."),
    ("GNU General Public License v3",
     "The license of this application (full text under the Help menu)."),
]


class DesignWindow(QMainWindow):
    """Diyagram sayfasini tasiyan AYRI ust duzey pencere.

    Kullanici modeli burada duzenlerken ANA pencere serbest kalir: uretilen
    koda, baska bir modele ya da depo sekmesine bakabilir.

    Pencere KAPANINCA sayfa ana pencereye geri doner; aksi halde sayfa
    hicbir yerde gorunmez ve kullanici modelini kaybetmis sanirdi.
    """

    def __init__(self, parent, on_close) -> None:
        super().__init__(parent)
        # Parent VERILIR (uygulamayla birlikte kapansin) ama Window bayragi
        # olmadan cocuk bir pano gibi gomulu kalirdi.
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.on_close = on_close

    def closeEvent(self, event) -> None:
        geri = self.on_close
        self.on_close = None
        if geri is not None:
            geri()
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1680, 980)
        # Asgari pencere: sol sutun + diyagram + kod paneli asgarilerinin
        # toplami sigmali, yoksa Qt en sagdaki bolmeyi kirpar.
        self.setMinimumSize(QSize(1240, 700))

        # ACILISTA ORNEK MODEL YUKLENMEZ.
        #
        # Onceki surum her iki kipi de hazir demo modelle (Blinky / RoomPlan)
        # aciyordu. Sonuc kullaniciyi yaniltiyordu: calisma alaninda TEK
        # model yokken -- hatta "No models here yet" yazarken -- tuvalde
        # dokuz durumluk bir diyagram duruyor, ozellikler panelinde "Blinky"
        # yaziyordu. Kullanici bunu kendi projesinin bir parcasi saniyor,
        # sildiginde de geri geliyordu.
        #
        # Ornekler menuden acilir: File > Sample State Machine / Sample
        # Class Diagram (bkz. a_demo, a_demo_class).
        self.doc = Document(empty_machine(), self, default_name="untitled.usm")
        self.class_doc = Document(empty_class_model(), self,
                                  default_name="untitled.ucd")
        self.settings = app_settings()
        #: ETKIN kipin en son URETILEN dosyalari (disa aktarma / yazma bunu
        #: kullanir). Model degistiginde bayatlar ama SILINMEZ: kullanici
        #: derlemeden once eski kodu inceleyebilmelidir.
        self._last_files: Dict[str, str] = {}
        #: Kip -> o kip icin en son uretilen dosyalar. Sekme degistirmek
        #: YENIDEN URETIM TETIKLEMEZ; onbellek gosterilir. Aksi halde
        #: State/Class sekmeleri arasinda gidip gelmek her seferinde bir
        #: uretim + disk yazma turu baslatirdi.
        self._built: Dict[str, Dict[str, str]] = {}
        #: KIP BASINA bayatlik. Tek bir bayrak yanlis olurdu: durum
        #: makinesini duzenlemek sinif diyagraminin kodunu bayatlatmaz ve
        #: tersi de gecerlidir.
        self._stale_modes = set()
        #: build() yeniden girise kapalidir (ilerleme cubugu processEvents
        #: cagirdigi icin kullanici Build'e tekrar basabilir).
        self._building = False
        self._last_export_dir = ""
        self.workspace: Optional[Workspace] = None
        #: Tema degisiminde yeniden boyanacak bolum baslik seritleri.
        self._section_headers: List[QLabel] = []
        #: Eylem -> simge ureteci. Simgeler KURULUM anindaki tema
        #: renkleriyle cizilir; tema degisince yeniden uretilmeleri
        #: gerekir, yoksa acik zeminde beyaz simge gorunmez olur.
        self._action_icons = {}
        #: AYRI pencereye alinmis diyagram sayfalari: kip -> QMainWindow.
        self._detached = {}
        #: Sayfa ayriyken sekmede duran yer tutucu etiketler (tema).
        self._placeholders = []
        #: Depo sekmesine GIRERKEN panellerin durumu (kod, simulasyon).
        #: Sekmeden cikilinca tercih geri yuklenir; None = depo kipinde degil.
        self._git_panel_state = None

        self._build_ui()
        self._build_actions()
        self._build_menu()
        self._build_toolbars()
        self._connect()

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_views)

        #: Ilerleme cubugunu %100'de kisa sure tutup gizler.
        self._progress_hide = QTimer(self)
        self._progress_hide.setSingleShot(True)
        self._progress_hide.timeout.connect(
            lambda: self.progress.setVisible(False))

        self._restore_state()
        self._sync_mode()
        self._rebuild_views()
        # ACILISTA BIR KEZ derlenir: kullanici uygulamayi actiginda kod
        # panelinin bos olmasi beklenmez. Bundan sonrasi Build'e baglidir.
        QTimer.singleShot(0, self.build)
        QTimer.singleShot(60, self.canvas.zoom_fit)
        QTimer.singleShot(60, self.class_canvas.zoom_fit)

    # ================================================================== kurulum

    def _build_ui(self) -> None:
        # -- durum makinesi kipi
        self.canvas = DiagramCanvas(self.doc, self)
        self.inspector = Inspector(self.doc, self)
        self.problems = ProblemsPanel(self)
        self.sim_panel = SimulatorPanel(self.doc, self)

        # CALISMA ALANI AGACI. Bir calisma alaninda birden fazla model
        # bulunur (sistem + alt sistemleri); onceki panel yalnizca ACIK
        # olani gosterdigi icin digerleri arayuzde hic gorunmuyordu.
        self.ws_tree = WorkspaceTree(self)

        # MODEL TREE PANELI KALDIRILDI.
        #
        # Ayni bilgiyi CALISMA ALANI agaci zaten gosteriyordu -- ustelik
        # yalnizca acik modeli degil, calisma alanindaki BUTUN modelleri.
        # Iki agac ekranda yan yana durunca kullanici ayni icerigi iki kez
        # goruyor, sol sutun geregisiz yer kapliyordu.
        #
        # Sol sutun kip SEKMELERININ ICINDE DEGIL, onlarin YANINDADIR: model
        # agaci kaldirildigi icin calisma alani agaci tek gezinme aracidir ve
        # sinif diyagramina gecince kaybolmasi kullaniciyi modelsiz birakirdi.
        # PROPERTIES yalnizca durum kipinde anlamlidir (sinif ozellikleri
        # cift tiklama diyalogundan duzenlenir), o yuzden kipe gore gizlenir.
        self._ws_section = self._titled("WORKSPACE", self.ws_tree)
        self._props_section = self._titled("PROPERTIES", self.inspector)
        self.left_col = QSplitter(Qt.Orientation.Vertical)
        self.left_col.addWidget(self._ws_section)
        self.left_col.addWidget(self._props_section)
        # Ozellikler formu calisma alani agacindan daha COK dikey yer ister:
        # agac tipik olarak birkac satirdir, form ise her zaman dolu.
        self.left_col.setSizes([330, 560])
        # OZELLIKLER FORMU icin asgari genislik. 230 px'te "Extra includes"
        # etiketi yaninda alana ~90 px kaliyordu ve icerik kirpiliyordu;
        # kullanici yazdigi #include satirini goremiyordu.
        self.left_col.setMinimumWidth(300)

        center_col = QSplitter(Qt.Orientation.Vertical)
        center_col.addWidget(self.sim_panel)
        center_col.addWidget(self.canvas)
        center_col.addWidget(self._titled("PROBLEMS", self.problems))
        # TUVAL EN BUYUK PAYI ALIR. Simulasyon ve Problems bilgi
        # seritleridir; tuval calisma alanidir. Eski dagitimda
        # (190/560/150) ikisi birlikte tuvalin yarisi kadar yer
        # kapliyordu.
        center_col.setSizes([150, 720, 120])
        center_col.setStretchFactor(1, 1)
        self.state_center = center_col

        state_page = center_col

        # -- sinif diyagrami kipi
        self.class_canvas = ClassCanvas(self.class_doc, self)
        self.class_problems = ProblemsPanel(self)

        class_page = QSplitter(Qt.Orientation.Vertical)
        class_page.addWidget(self.class_canvas)
        class_page.addWidget(self._titled("PROBLEMS", self.class_problems))
        class_page.setSizes([720, 150])
        class_page.setStretchFactor(0, 1)

        # -- depo kipi
        self.git_panel = GitPanel(self)

        # -- kip sekmeleri
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setDocumentMode(True)
        # DIYAGRAM SAYFALARI YIGIN ICINE SARILIR.
        #
        # Kullanici tasarim penceresini AYRI bir pencereye alabilir; o sirada
        # ana pencerede uretilen koda, baska bir modele ya da depoya
        # bakabilmelidir. Sayfayi sekmeden CIKARMAK olmazdi: active_mode()
        # sekme INDISINE bakiyor (0=state, 1=class, 2=git) ve bir sekmeyi
        # kaldirmak butun eslemeyi kaydirirdi. Bunun yerine sekme yerinde
        # kalir, ICERIGI yer tutucuyla degisir.
        self._state_stack = self._detachable(state_page, "State Diagram")
        self._class_stack = self._detachable(class_page, "Class Diagram")
        self.mode_tabs.addTab(self._state_stack, "State Diagram")
        self.mode_tabs.addTab(self._class_stack, "Class Diagram")
        self.mode_tabs.addTab(self.git_panel, "Repository (Git)")

        self.code_panel = CodePanel(self)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.left_col)
        self.main_splitter.addWidget(self.mode_tabs)
        self.main_splitter.addWidget(self.code_panel)
        self.main_splitter.setSizes([290, 1000, 560])
        self.main_splitter.setStretchFactor(1, 3)
        self.main_splitter.setStretchFactor(2, 2)
        # AYIRICI SONUNA KADAR SURUKLENEREK BOLME YOK EDILEMEZ.
        #
        # Varsayilan olarak QSplitter bir bolmeyi 0 piksele indirmeye izin
        # verir. Diyagram bolmesi bir kez 0'a inince geriye tutulacak
        # gorunur bir sey kalmaz: kullanici tuvali geri getiremez ve ayar
        # kaydedildigi icin sorun her acilista tekrarlar. Asgari genislik
        # her bolmenin ekranda kalmasini garanti eder; paneller yine
        # F9 / F10 ile TAMAMEN gizlenebilir (gizleme farkli bir yoldur).
        self.main_splitter.setChildrenCollapsible(False)
        self.mode_tabs.setMinimumWidth(320)
        # ...AMA KORUNMASI GEREKEN TEK BOLME DIYAGRAM BOLMESIDIR.
        #
        # Yasak uc bolmeye birden konulunca kod paneli de kapanamaz oldu:
        # kullanici sag ayiriciyi sonuna kadar surukluyor, panel yerinde
        # kaliyordu. Oysa kod panelinin yok olmasi tehlikeli degil --
        # F9 ve View menusu onu geri getirir. Diyagram bolmesinde ise
        # tutunacak hicbir sey kalmiyordu (bkz. yukaridaki aciklama).
        self.main_splitter.setCollapsible(
            self.main_splitter.indexOf(self.code_panel), True)
        # Diyagram bolmesi ACIKCA korunur. setChildrenCollapsible(False) tek
        # basina yeterli ama isCollapsible() onu YANSITMAZ (Qt yalnizca
        # bilesen-basi bayragi dondurur, varsayilani "True" gorunur); niyeti
        # burada acikca yazmak hem okunur hem sinanabilir kilar.
        self.main_splitter.setCollapsible(
            self.main_splitter.indexOf(self.mode_tabs), False)
        # Suruklemeyle kapatmak ile menuden kapatmak AYNI seye varmali:
        # aksi halde panel ekranda yokken "Show Code Panel" isaretli kalir
        # ve dugme bozuk gorunur.
        self.main_splitter.splitterMoved.connect(self._sync_code_panel_action)
        # Kod panelinin asgari genisligi KENDI baslik seridinden turer
        # (bkz. CodePanel._fit_header); burada sabit bir sayi vermek
        # onu ezer ve serit yine kirpilirdi.
        self.setCentralWidget(self.main_splitter)

        # ------------------------------------------------------------ durum cub.
        self.lbl_message = QLabel("Ready.")
        self.lbl_workspace = QLabel("")
        self.lbl_git = QLabel("")
        self.lbl_counts = QLabel("")
        self.lbl_issues = QLabel("")
        self.lbl_zoom = QLabel("100%")
        for lbl in (self.lbl_workspace, self.lbl_git, self.lbl_counts,
                    self.lbl_issues, self.lbl_zoom):
            lbl.setFont(ui_font(8))
        self.lbl_workspace.setStyleSheet("color: %s;" % C.TEXT_DIM)
        self.lbl_git.setStyleSheet("color: %s;" % C.TEXT_DIM)

        # Derleme ilerleme cubugu -- durum cubugunun EN SAGINDA.
        # addPermanentWidget sagdan sola yerlestirir, bu yuzden EN SON
        # eklenen en sagda kalir.
        self.progress = QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.setFixedHeight(14)
        # Yuzde YAZISI yok: dolu ve bos kisim uzerinde ayni renkle okunamaz
        # (bkz. theme.py'deki QProgressBar aciklamasi). Adim adi durum
        # cubugunun solunda yazili.
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)

        bar = self.statusBar()
        bar.addWidget(self.lbl_message, 1)
        #: Kullanicinin KENDI yazmasi gereken dis sembollerin sayisi.
        self.lbl_external = QLabel("")
        self.lbl_external.setFont(ui_font(9))
        self.lbl_external.setAccessibleName("External symbols")
        bar.addPermanentWidget(self.lbl_workspace)
        bar.addPermanentWidget(self.lbl_git)
        bar.addPermanentWidget(self.lbl_counts)
        bar.addPermanentWidget(self.lbl_external)
        bar.addPermanentWidget(self.lbl_issues)
        bar.addPermanentWidget(self.lbl_zoom)
        bar.addPermanentWidget(self.progress)

    def _panels_for_git(self, girildi: bool) -> None:
        """Depo sekmesinde kod ve simulasyon panellerini GIZLER.

        Ikisi de DIYAGRAM icindir: kod paneli uretilen kaynagi, simulasyon
        seridi etkin durumu gosterir. Depo sekmesinde ikisi de anlamsizdir
        ama ekranin yarisini goturur -- commit agaci ve fark goruntuleyici
        dar bir seride sikisirdi.

        Kullanicinin TERCIHI korunur: sekmeden cikilinca paneller girmeden
        onceki haline doner.
        """
        if girildi:
            if self._git_panel_state is not None:
                return                      # zaten depo kipindeyiz
            # HER PANEL ICIN DOGRU KAYNAK:
            #
            # * Kod paneli sekmelerin DISINDA durur, gorunurlugu guvenilir
            #   -- ama ayirici sonuna kadar suruklendiyse "gorunur" olup
            #   0 piksel olabilir; code_panel_open() bunu hesaba katar.
            # * Simulasyon seridi sekmenin ICINDE. Bu islev sekme
            #   DEGISTIKTEN SONRA calisir, yani depo sekmesi etkinken
            #   sim_panel.isVisible() her zaman False'tur; gorunurluge
            #   bakmak paneli hep kapali kaydeder ve geri getirmezdi.
            #   Dogru kaynak eylemin isaretidir.
            self._git_panel_state = (self.code_panel_open(),
                                     self.a_sim_panel.isChecked())
            for eylem, panel in ((self.a_code_panel, self.code_panel),
                                 (self.a_sim_panel, self.sim_panel)):
                if eylem.isChecked():
                    eylem.blockSignals(True)
                    eylem.setChecked(False)
                    eylem.blockSignals(False)
                panel.setVisible(False)
            return

        onceki = self._git_panel_state
        self._git_panel_state = None
        if onceki is None:
            return
        kod_acik, sim_acik = onceki
        if kod_acik:
            self.a_code_panel.blockSignals(True)
            self.a_code_panel.setChecked(True)
            self.a_code_panel.blockSignals(False)
            self.code_panel.setVisible(True)
            self._restore_splitter_share(self.main_splitter,
                                         self.code_panel, 0.40)
        if sim_acik:
            self.a_sim_panel.blockSignals(True)
            self.a_sim_panel.setChecked(True)
            self.a_sim_panel.blockSignals(False)
            self.sim_panel.setVisible(True)
            self._restore_splitter_share(self.state_center,
                                         self.sim_panel, 0.30)

    def _detachable(self, page: QWidget, title: str) -> QStackedWidget:
        """Sayfayi, yerine yer tutucu konabilen bir yigina sarar."""
        stack = QStackedWidget()
        stack.addWidget(page)

        yer_tutucu = QLabel(
            "%s is open in a separate window.\n\n"
            "Close that window (or use Tool ▸ Design Window) to bring it "
            "back here." % title)
        yer_tutucu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        yer_tutucu.setWordWrap(True)
        self._placeholders.append(yer_tutucu)
        stack.addWidget(yer_tutucu)
        return stack

    def toggle_design_window(self, checked: bool) -> None:
        """Etkin diyagram sayfasini AYRI pencereye alir / geri getirir."""
        mode = self.active_mode()
        if mode == "git":
            self.a_tool_window.setChecked(False)
            self.flash("Open a diagram tab first.")
            return
        if checked:
            self._detach_page(mode)
        else:
            self._attach_page(mode)

    def _detach_page(self, mode: str) -> None:
        if mode in self._detached:
            return
        stack = self._state_stack if mode == "state" else self._class_stack
        page = stack.widget(0)
        if page is None:
            return

        win = DesignWindow(self, lambda m=mode: self._attach_page(m))
        win.setWindowTitle("%s — %s"
                           % ("State Diagram" if mode == "state"
                              else "Class Diagram", APP_NAME))
        # setCentralWidget sayfayi yigindan ALIR (yeniden ebeveynler); geri
        # koyabilmek icin sayfaya AYRICA referans tutulur, yigin indisine
        # guvenilmez -- sayfa ciktiktan sonra yer tutucu 0. indise kayar.
        win.setCentralWidget(page)
        # SAYFAYI ACIKCA GOSTER -- ama YALNIZCA sayfayi.
        #
        # setParent() (setCentralWidget icinden cagrilir) bileseni GIZLI
        # isaretler ve bu isaret ust bilesen gosterilince KALKMAZ: ayri
        # pencere bombos aciliyordu.
        #
        # ALT BILESENLER TOPLUCA GOSTERILMEZ. Ilk duzeltmede
        # `for alt in page.findChildren(QWidget): alt.show()` yaziliydi ve
        # bu, GIZLI KALMASI GEREKEN bilesenleri de aciyordu -- ozellikle
        # tuvalin QRubberBand'ini. Gorunmez bir secim kaplamasi tuvalin
        # ustune yayiliyor, fare tiklamalari ona gidiyor ve kullaniciya
        # "fare takildi" gibi geliyordu. Qt zaten yalnizca YENIDEN
        # EBEVEYNLENEN bilesene gizli isaretini koyar; cocuklar kendi
        # durumlarini korur ve ust bilesen gosterilince gorunur olur.
        page.show()
        win.resize(1100, 780)
        self._detached[mode] = (win, page)
        stack.setCurrentWidget(stack.widget(stack.count() - 1))
        win.show()
        win.raise_()
        # TAM YENIDEN BOYAMA. Yeniden ebeveynlenen bilesenin arka tamponu
        # eski icerigi tasiyor ve pencerenin bir kosesinde onceki
        # yerlesimden kalma bir leke kaliyordu.
        page.update()
        self.active_canvas().viewport().update()
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Design window opened — the main window is free now.")

    def _attach_page(self, mode: str) -> None:
        kayit = self._detached.pop(mode, None)
        if kayit is None:
            return
        win, page = kayit
        stack = self._state_stack if mode == "state" else self._class_stack
        win.on_close = None                 # geri cagirma DONGUSUNU kes
        page.setParent(None)
        stack.insertWidget(0, page)
        stack.setCurrentIndex(0)
        # Ayirmada oldugu gibi: setParent() gizli isaretini birakir.
        page.show()
        self.sim_panel.setVisible(self.a_sim_panel.isChecked())
        page.update()
        self.active_canvas().viewport().update()
        win.hide()
        win.deleteLater()
        if self.a_tool_window.isChecked():
            self.a_tool_window.setChecked(False)
        QTimer.singleShot(40, self.active_canvas().zoom_fit)

    def _titled(self, title: str, widget: QWidget) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(title)
        f = ui_font(8)
        f.setBold(True)
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.0)
        header.setFont(f)
        header.setContentsMargins(10, 6, 10, 6)
        # Baslik seritleri tema degisiminde yeniden boyanir (bkz. set_theme).
        self._section_headers.append(header)
        layout.addWidget(header)

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(widget)
        layout.addWidget(frame, 1)
        return host

    # ================================================================== kipler

    def active_mode(self) -> str:
        index = self.mode_tabs.currentIndex()
        if index == 1:
            return "class"
        if index == 2:
            return "git"
        return "state"

    def active_doc(self) -> Document:
        """Etkin dokuman; depo kipinde durum dokumanina duser."""
        return self.class_doc if self.active_mode() == "class" else self.doc

    def active_canvas(self):
        return self.class_canvas if self.active_mode() == "class" else self.canvas

    def _sync_mode(self) -> None:
        """Sekme degisince arac cubuklarini ve kisayollari esler."""
        mode = self.active_mode()
        state_mode = mode == "state"
        class_mode = mode == "class"
        diagram_mode = mode != "git"
        self._show_tools_for(mode)
        self._sync_tool_labels()
        for a in self.tool_actions.values():
            a.setEnabled(state_mode)
        for a in self.class_tool_actions.values():
            a.setEnabled(class_mode)
        # Depo sekmesinde tuval GORUNMEZ. Tuvale is yapan kisayollar orada
        # etkin kalirsa (Ctrl+A, Del) kullanici hicbir sey gormeden butun
        # diyagrami silebilir; bu yuzden kip disinda kapatilirlar.
        # Undo/Redo de bu listeye AITTIR. Depo sekmesinde tuval gorunmez;
        # orada Ctrl+Z'ye basmak, kullanicinin GORMEDIGI diyagrami sessizce
        # degistiriyordu -- geri alinan seyi gorene kadar ne oldugunu
        # anlamanin bir yolu yoktu.
        for a in (self.a_undo, self.a_redo, self.a_select_all,
                  self.a_zoom_in, self.a_zoom_out,
                  self.a_zoom_reset, self.a_zoom_fit):
            a.setEnabled(diagram_mode)
        # KOD ve SIMULASYON panelleri depo kipinde ACILAMAZ.
        #
        # Panelleri sekmeye girerken kapatmak yetmiyordu: kullanici depo
        # ekranindayken F9/F10'a basip ikisini geri acabiliyor, fark
        # goruntuleyici yine dar bir seride sikisiyordu. Eylemler kip
        # disinda kapatilir; sekmeden cikilinca geri acilir.
        self.a_code_panel.setEnabled(diagram_mode)
        self.a_sim_panel.setEnabled(state_mode)
        if not state_mode:
            # Sinif diyagramina gecilince durum tuvali gizlenir; benzetim
            # vurgusu orada asili kalmasin.
            self._clear_simulation()
        self.a_delete.setEnabled(
            diagram_mode and bool(self.active_canvas().selected_ids()))
        self.set_tool(Tool.SELECT)
        self.set_class_tool(ClassTool.SELECT)
        self._update_counts()

        # PROPERTIES yalnizca durum kipinde anlamlidir: sinif ozellikleri
        # cift tiklama diyalogundan duzenlenir, depo kipinde model yoktur.
        # WORKSPACE agaci HER kipte gorunur -- tek gezinme aracidir.
        self._props_section.setVisible(state_mode)
        self.left_col.setVisible(diagram_mode)
        if diagram_mode:
            self._restore_splitter_share(self.main_splitter,
                                         self.left_col, 0.18)

        if mode == "git":
            self._panels_for_git(True)
            self.git_panel.refresh()
            return
        self._panels_for_git(False)
        # Kip degisince diyagrami pencereye SIGDIR. Tuval gizliyken yapilan
        # yakinlastirma/kaydirma islemleri gorunumu kaydirir; kullanici sekmeye
        # dondugunde modeli bulmak icin ugrasmak zorunda kalmamali.
        QTimer.singleShot(0, self.active_canvas().zoom_fit)
        # Sekme degistirmek URETIM TETIKLEMEZ. Bunun yerine o kipin en son
        # derlenmis ciktisi onbellekten gosterilir; henuz derlenmemisse panel
        # bunu soyler. Boylece State/Class arasinda gidip gelmek disk
        # yazmaya yol acmaz, ama panel de BASKA kipin kodunu gostermez --
        # aksi halde Ctrl+E yanlis dosyalari disa aktarabilirdi.
        self._show_mode_build()
        self._validate_active()

    # ------------------------------------------------------------------ eylemler

    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, icon=None, tip=None, checkable=False):
            a = QAction(text, self)
            if icon:
                a.setIcon(icon())
                # Ureteci sakla: tema degisince simge yeniden cizilir.
                self._action_icons[a] = icon
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
                a.setStatusTip(tip)
            a.setCheckable(checkable)
            if slot:
                a.triggered.connect(slot)
            return a

        self.a_new = act("&New", self.file_new, "Ctrl+N", icons.new_icon,
                         "Create an empty diagram")
        self.a_open = act("&Open…", self.file_open, "Ctrl+O", icons.open_icon,
                          "Open a saved diagram")
        self.a_save = act("&Save", self.file_save, "Ctrl+S", icons.save_icon,
                          "Save the diagram")
        self.a_save_as = act("Save &As…", self.file_save_as, "Ctrl+Shift+S")
        self.a_export = act("E&xport Code…", self.export_code, "Ctrl+E",
                            icons.export_icon, "Write the generated files into a folder")
        self.a_demo = act("Sample State Machine", self.load_demo)
        self.a_demo_class = act("Sample Class Diagram", self.load_demo_class)
        self.a_quit = act("&Quit", self.close, "Ctrl+Q")

        self.a_workspace = act("Workspace…", self.choose_workspace,
                               "Ctrl+Shift+W",
                               tip="Choose the folder that holds the model and the generated code")
        self.a_workspace_open = act("Reveal Workspace in File Manager",
                                    self.reveal_workspace)
        self.a_write_now = act("Write Generated Code to Workspace",
                               self.write_generated_now, "Ctrl+Shift+G")
        self.a_auto_write = act("Write Automatically After Generation",
                                self.toggle_auto_write, checkable=True)
        self.a_auto_write.setChecked(True)
        self.a_git_refresh = act("Refresh Repository", self.refresh_git, "F6",
                                 tip="Re-read the git status and history")
        self.a_git_tab = act("Open Repository Panel", self.show_git_tab, "F8")

        self.a_undo = act("Undo", self._undo, None, icons.undo_icon)
        self.a_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.a_redo = act("Redo", self._redo, None, icons.redo_icon)
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"),
                                  QKeySequence("Ctrl+Shift+Z")])

        self.a_delete = act("Delete", self._delete_selection, "Del",
                            icons.delete_icon, "Delete the selected elements")
        self.a_select_all = act("Select All", self.select_all, "Ctrl+A")

        self.a_zoom_in = act("Zoom In", lambda: self.active_canvas().zoom_by(1.2),
                             "Ctrl++", icons.zoom_in_icon)
        self.a_zoom_out = act("Zoom Out",
                              lambda: self.active_canvas().zoom_by(1 / 1.2),
                              "Ctrl+-", icons.zoom_out_icon)
        self.a_zoom_reset = act("Actual Size",
                                lambda: self.active_canvas().zoom_reset(), "Ctrl+0")
        self.a_zoom_fit = act("Fit", lambda: self.active_canvas().zoom_fit(),
                              "Ctrl+Shift+F", icons.fit_icon,
                              "Fit the diagram to the window")

        self.a_snap = act("Snap to Grid", self.toggle_snap, None,
                          icons.grid_icon, "Snap to a 10 px grid while dragging",
                          checkable=True)
        self.a_snap.setChecked(True)
        self.a_align = act("Alignment Guides", self.toggle_align, None,
                           None,
                           "Snap to a neighbour when it lines up, and show "
                           "the guide while dragging",
                           checkable=True)
        self.a_align.setChecked(True)
        self.a_grid = act("Show Grid", self.toggle_grid, None,
                          icons.eye_icon,
                          checkable=True)
        self.a_grid.setChecked(True)

        # TAM EKRAN. F11 standart kisayoldur; Esc de cikarir (asagida
        # keyPressEvent). Isaretli durumu pencere durumundan TUREMEZ,
        # kullanici pencere yoneticisiyle de cikabilecegi icin
        # changeEvent'te eslenir -- aksi halde menudeki tik yalan soyler.
        self.a_fullscreen = act("Full Screen", self.toggle_fullscreen, "F11",
                                None, "Switches the window to full screen (F11)",
                                checkable=True)

        # KURTARMA YOLU. Bozuk bir yerlesim (bir bolmenin 0 piksele
        # inmesi gibi) kaydedildigi icin her acilista tekrarlar; menude
        # tek tikla varsayilana donmek, kullaniciyi ayarlari elle silmeye
        # zorlamaktan iyidir.
        self.a_reset_layout = act("Reset Layout", self.reset_layout, None,
                                  None, "Restores the default panel sizes")

        # TEMA. Secim QSettings'te saklanir ve acilista uygulanir.
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for key, label in THEMES.items():
            # Alt menunun adi zaten "Theme"; ogede yinelemek gereksiz.
            # Erisim harfi de eklenir (D / L).
            a = QAction("&%s" % label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _c, k=key: self.set_theme(k))
            self.theme_group.addAction(a)
            self.theme_actions[key] = a
        self.theme_actions[active_theme()].setChecked(True)

        # AYRI SIMGELER. Iki panel anahtari da goz simgesi tasiyinca
        # seritte yan yana ayirt edilemiyordu; kod paneli sagda bir serit
        # oldugu icin panel simgesi, benzetim icin de oynat ucgeni.
        self.a_code_panel = act("Show Code Panel", self.toggle_code_panel,
                                "F9", icons.panel_icon,
                                "Hide or show the generated-code panel",
                                checkable=True)
        self.a_code_panel.setChecked(True)

        # SIMULASYON PANELI ACILISTA KAPALIDIR.
        #
        # Tuvalin ustunden ~190 piksel goturuyordu ve kullanicilarin
        # cogu once modeli cizip sonra kosturuyor. Tercih KAYDEDILIR:
        # bir kez acildiysa sonraki acilista da acik gelir.
        self.a_sim_panel = act("Show Simulation", self.toggle_sim_panel,
                               "F10", icons.play_icon,
                               "Hide or show the simulation strip",
                               checkable=True)
        self.a_sim_panel.setChecked(False)
        self.sim_panel.setVisible(False)

        # BUILD, kod uretiminin TEK tetigidir. Model degistiginde kod artik
        # kendiliginden uretilmez (bkz. _on_model_changed).
        self.a_build = act("Build", self.build, "F5", icons.build_icon,
                           "Validate the model and generate the code "
                           "(F5) — nothing is generated until you ask")
        # Tasarim penceresini AYIRMA. Kullanici modeli ayri bir pencerede
        # duzenlerken ana pencerede uretilen koda / baska bir modele /
        # depoya bakabilir.
        self.a_tool_window = act("Design Window (separate)",
                                 self.toggle_design_window, "F4",
                                 None,
                                 "Open the active diagram in its own "
                                 "window so the main window is free",
                                 checkable=True)
        self.a_validate = act("Validate", self.force_validate, "F7",
                              icons.validate_icon, "Validate the model")

        self.a_spec = act("UML 2.5.1 Specification (PDF)…",
                          self.show_spec, "Shift+F1",
                          tip="Open the OMG UML 2.5.1 specification "
                              "in a separate window")
        self.a_about = act("About", self.show_about)
        self.a_shortcuts = act("Shortcuts", self.show_shortcuts, "F1")
        self.a_license = act("License (GPL v3)", self.show_license)

        # ------------------------------------------------- durum kipi araclari
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: Dict[Tool, QAction] = {}
        for tool, text, key, icon, tip in STATE_TOOLS:
            a = QAction(icon(), text, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(lambda _c, t=tool: self.set_tool(t))
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
            self._action_icons[a] = icon
        self.tool_actions[Tool.SELECT].setChecked(True)

        # ------------------------------------------------- sinif kipi araclari
        self.class_tool_group = QActionGroup(self)
        self.class_tool_group.setExclusive(True)
        self.class_tool_actions: Dict[ClassTool, QAction] = {}
        for tool, text, key, icon, tip in CLASS_TOOLS:
            a = QAction(icon(), text, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(tip)
            a.setStatusTip(tip)
            a.triggered.connect(lambda _c, t=tool: self.set_class_tool(t))
            self.class_tool_group.addAction(a)
            self.class_tool_actions[tool] = a
            self._action_icons[a] = icon
        self.class_tool_actions[ClassTool.SELECT].setChecked(True)

    def _build_menu(self) -> None:
        """Menu cubugunu STANDART duzende kurar.

        Uyulan kurallar (Windows/CUA ve Qt uygulamalarinin ortak duzeni):

        * Sira: File, Edit, View, ... alan menuleri ..., Help. Onceden
          Repository, File ile Edit'in ARASINDAYDI; File-Edit bitisikligi
          butun masaustu kilavuzlarinda sabittir.
        * HER ogenin menu icinde TEKIL bir erisim harfi (&) vardir;
          klavye ile menude gezinmenin tek yolu budur.
        * Diyalog acan komutlar "..." ile biter; dogrudan calisan ya da
          yalnizca bilgi gosteren komutlar bitmez (About, Shortcuts).
        * Bolum basliklari `addSection()` ile konur. Once "pasif QAction"
          kullaniliyordu; ekran okuyucular onu "devre disi menu ogesi"
          diye okuyordu.
        """
        m = self.menuBar()
        #: Tek sirali ust bara tasinacak menuler (bkz. _build_toolbars).
        self._menus = []

        # -- File ---------------------------------------------------------- #
        f = m.addMenu("&File")
        _mnemonic(self.a_workspace, "&Workspace…")
        _mnemonic(self.a_workspace_open, "&Reveal Workspace in File Manager")
        f.addActions([self.a_workspace, self.a_workspace_open])
        f.addSeparator()
        _mnemonic(self.a_new, "&New")
        _mnemonic(self.a_open, "&Open…")
        _mnemonic(self.a_save, "&Save")
        _mnemonic(self.a_save_as, "Save &As…")
        f.addActions([self.a_new, self.a_open, self.a_save, self.a_save_as])
        f.addSeparator()
        _mnemonic(self.a_write_now, "&Generate Code into Workspace")
        _mnemonic(self.a_auto_write, "Write Automatically After &Build")
        _mnemonic(self.a_export, "Ex&port Code…")
        f.addActions([self.a_write_now, self.a_auto_write, self.a_export])
        f.addSection("Examples")
        _mnemonic(self.a_demo, "Sample State &Machine")
        _mnemonic(self.a_demo_class, "Sample &Class Diagram")
        f.addActions([self.a_demo, self.a_demo_class])
        self._build_examples_menu(f)
        f.addSeparator()
        _mnemonic(self.a_quit, "E&xit")
        f.addAction(self.a_quit)

        # -- Edit ---------------------------------------------------------- #
        e = m.addMenu("&Edit")
        _mnemonic(self.a_undo, "&Undo")
        _mnemonic(self.a_redo, "&Redo")
        e.addActions([self.a_undo, self.a_redo])
        e.addSeparator()
        _mnemonic(self.a_delete, "&Delete")
        _mnemonic(self.a_select_all, "Select &All")
        e.addActions([self.a_delete, self.a_select_all])
        # GORUNUM araclari Edit altinda da durur. Kullanicinin acik
        # istegi: "geri al, ileri git, fit gibi araclari da edit altina
        # koy". Ayni eylemin iki menude bulunmasi masaustu
        # uygulamalarinda olagandir; kisayollari tektir.
        e.addSeparator()
        e.addActions([self.a_zoom_in, self.a_zoom_out, self.a_zoom_reset,
                      self.a_zoom_fit])
        e.addSeparator()
        e.addActions([self.a_grid, self.a_snap, self.a_align])

        # -- View ---------------------------------------------------------- #
        v = m.addMenu("&View")
        _mnemonic(self.a_zoom_in, "Zoom &In")
        _mnemonic(self.a_zoom_out, "Zoom &Out")
        _mnemonic(self.a_zoom_reset, "Actual Si&ze")
        _mnemonic(self.a_zoom_fit, "&Fit to Window")
        v.addActions([self.a_zoom_in, self.a_zoom_out, self.a_zoom_reset,
                      self.a_zoom_fit])
        v.addSeparator()
        _mnemonic(self.a_grid, "Show &Grid")
        _mnemonic(self.a_snap, "&Snap to Grid")
        _mnemonic(self.a_align, "Alig&nment Guides")
        v.addActions([self.a_grid, self.a_snap, self.a_align])
        v.addSeparator()
        _mnemonic(self.a_code_panel, "Show &Code Panel")
        _mnemonic(self.a_sim_panel, "Show Si&mulation")
        v.addActions([self.a_code_panel, self.a_sim_panel])
        v.addSeparator()
        _mnemonic(self.a_fullscreen, "F&ull Screen")
        v.addAction(self.a_fullscreen)
        v.addSeparator()
        _mnemonic(self.a_reset_layout, "Reset &Layout")
        v.addAction(self.a_reset_layout)
        v.addSeparator()
        tema = v.addMenu("&Theme")
        tema.addActions(list(self.theme_actions.values()))

        # -- Tool ---------------------------------------------------------- #
        #
        # Ust seritte araclar yalnizca SIMGE olarak duruyor; hangi simgenin
        # hangi arac oldugunu ogrenmenin tek yolu ipucunu beklemekti. Menu
        # araclari ADIYLA ve KISAYOLUYLA listeler.
        t = m.addMenu("&Tool")
        _mnemonic(self.a_tool_window, "&Design Window (separate)")
        t.addAction(self.a_tool_window)
        t.addSection("State machine")
        for tool, *_rest in STATE_TOOLS:
            t.addAction(self.tool_actions[tool])
        t.addSection("Class diagram")
        for tool, *_rest in CLASS_TOOLS:
            t.addAction(self.class_tool_actions[tool])

        # -- Code ---------------------------------------------------------- #
        k = m.addMenu("&Code")
        self.lang_group = QActionGroup(self)
        self.lang_actions: Dict[str, QAction] = {}
        # DIL ETIKETLERI TEK KAYNAKTAN gelir (code_panel.LANGUAGES).
        # Menu ile sag panelin etiketleri elle yazilinca ayrisiyordu.
        # Erisim harfleri burada eklenir; metnin kendisi degismez.
        _KOD_HARF = {"c": "&C", "cpp": "C&++", "puml": "Plant&UML"}
        for text, key in LANGUAGES:
            isaretli = text
            for duz, harfli in (("PlantUML", _KOD_HARF["puml"]),
                                ("C++", _KOD_HARF["cpp"]),
                                ("C", _KOD_HARF["c"])):
                if text.startswith(duz):
                    isaretli = harfli + text[len(duz):]
                    break
            a = QAction(isaretli, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _c, kk=key: self.set_language(kk))
            self.lang_group.addAction(a)
            self.lang_actions[key] = a
            k.addAction(a)
        self.lang_actions["c"].setChecked(True)
        k.addSeparator()
        _mnemonic(self.a_build, "&Build")
        _mnemonic(self.a_validate, "&Validate")
        k.addActions([self.a_build, self.a_validate, self.a_export])

        # -- Repository ----------------------------------------------------- #
        g = m.addMenu("Re&pository")
        _mnemonic(self.a_git_tab, "&Open Repository Panel")
        _mnemonic(self.a_git_refresh, "&Refresh Repository")
        g.addActions([self.a_git_tab, self.a_git_refresh])

        # -- References ----------------------------------------------------- #
        r = m.addMenu("&References")
        # Spesifikasyonun KENDISI: atiflari izlenebilir kilar.
        r.addAction(self.a_spec)
        r.addSeparator()
        for title, detail in REFERENCES:
            a = QAction(title, self)
            a.triggered.connect(
                lambda _c, t=title, d=detail: self.show_reference(t, d))
            r.addAction(a)

        # -- Help ------------------------------------------------------------ #
        h = m.addMenu("&Help")
        _mnemonic(self.a_shortcuts, "&Keyboard Shortcuts")
        _mnemonic(self.a_license, "&License (GPL v3)")
        _mnemonic(self.a_about, "&About UML Design Studio")
        h.addActions([self.a_shortcuts, self.a_license, self.a_about])

        self._menus = [f, e, v, t, k, g, r, h]

    def _build_toolbars(self) -> None:
        """TEK SIRALI UST BAR.

        Once uc ayri satir vardi: menu cubugu, ana eylemler, diyagram
        araclari -- ustune bir de kip sekmeleri. Dort serit, tuvalin
        ustunden ~120 piksel goturuyordu. Kullanici: "2. ve 3. ust barlari
        da kaldir, onlari da en ust barda olacak sekilde yerlestir, bana
        alan ac."

        Simdi hepsi TEK QToolBar'da: solda menuler (acilir dugme olarak),
        ardindan ana eylemler, ardindan etkin kipin araclari. QMenuBar
        nesnesi DURUYOR ama gizli: menuler ona bagli kalir, kisayollar ve
        `menuBar().actions()` uzerinden yurunen kodlar bozulmaz.

        Dar pencerede QToolBar kendi tasma dugmesini gosterir; hicbir sey
        erisilemez hale gelmez.
        """
        bar = QToolBar("Main")
        bar.setObjectName("topBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(TOOLBAR_ICON, TOOLBAR_ICON))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.top_bar = bar

        # -- menuler ------------------------------------------------------
        # MENU YAZILARI BUYUTULUR. Kullanici "menubari biraz daha buyut,
        # cok kucuk olmus" dedi: serit simge kipine gecince menu dugmeleri
        # de arac simgeleriyle ayni kucuk olcege dusmustu.
        menu_font = ui_font(MENU_POINT)
        self.menu_buttons: List[QToolButton] = []
        for menu in self._menus:
            btn = QToolButton(bar)
            # ERISIM HARFI (&) KORUNUR.
            #
            # Gercek menu cubugu gizlenip yerine bu dugmeler konuyor.
            # Onceden `&` siliniyordu; boylece Alt+F / Alt+E gibi menu
            # kisayollari HIC CALISMIYORDU -- klavyeyle menuye ulasmanin
            # tek yolu kapanmisti. QAbstractButton, metindeki `&` icin
            # kendiliginden bir Alt kisayolu kurar ve InstantPopup
            # dugmesinde bu menuyu acar.
            btn.setText(menu.title())
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setAutoRaise(True)
            btn.setFont(menu_font)
            # Acilan menunun KENDI yazisi da ayni olcekte olmali; yoksa
            # dugme buyur, icerigi kucuk kalir.
            menu.setFont(menu_font)
            bar.addWidget(btn)
            self.menu_buttons.append(btn)
        self.menuBar().setVisible(False)

        # ANA EYLEMLER: yalnizca SIK KULLANILANLAR.
        #
        # Tek serit sinirli: 7 menu + 11 eylem + 10 arac 1500 px'lik bir
        # ekrana sigmiyor ve araclarin yarisi tasma dugmesinin arkasinda
        # kaliyordu -- oysa surekli kullanilan sey ARACLAR. Yakinlastirma,
        # izgara, panel anahtarlari ve disa aktarma seritten cikti; hepsi
        # View / Code menusunde ve kisayollarinda duruyor.
        bar.addSeparator()
        bar.addActions([self.a_new, self.a_open, self.a_save])
        bar.addSeparator()
        bar.addActions([self.a_undo, self.a_redo])
        bar.addSeparator()
        bar.addActions([self.a_zoom_fit, self.a_build])
        bar.addSeparator()
        # Panel anahtarlari SERITTE. Menu + kisayol tek basina
        # kesfedilebilir degil: kullanici kod panelini kapatinca geri
        # getirmenin gorunur bir yolu kalmiyordu.
        bar.addAction(self.a_code_panel)
        bar.addAction(self.a_sim_panel)

        # -- diyagram araclari: ETKIN kipe gore degisir -------------------
        # Araclar adlariyla birlikte durur: "hangi araci sectim" sorusunun
        # cevabi bir simgeden okunamiyordu.
        self._tool_sep = bar.addSeparator()

        def tool_actions_of(spec, actions):
            out = []
            for tool, _t, _k, _i, _tip in spec:
                out.append(actions[tool])
            return out

        self._state_tool_actions = tool_actions_of(STATE_TOOLS,
                                                   self.tool_actions)
        self._class_tool_actions = tool_actions_of(CLASS_TOOLS,
                                                   self.class_tool_actions)
        for act in self._state_tool_actions + self._class_tool_actions:
            bar.addAction(act)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        # Acilista da kipe gore suz: `_sync_mode` ilk sekme
        # degisimine kadar cagrilmiyor ve iki kipin araclari
        # birlikte gorunuyordu (iki ayri 'Select' dugmesi).
        self._show_tools_for(self.active_mode())
        self._sync_tool_labels()

    def _sync_tool_labels(self) -> None:
        """Butun araclari SIMGE olarak gosterir.

        Bir onceki surumde SECILI arac adiyla da yaziliyordu ("sectigim
        araci goreyim" istegi icin). Sonuc kotu duruyordu: serit her arac
        degisiminde genisleyip daraliyor, yanindaki dugmeler kayiyor ve
        tek bir "Select" yazisi araclarin arasinda yamalik gibi kaliyordu.
        Secili arac zaten BASILI gorunumuyle belli; hangi arac oldugu
        ipucunda, Tool menusunde ve durum cubugunda yazili.
        """
        for act in self._state_tool_actions + self._class_tool_actions:
            dugme = self.top_bar.widgetForAction(act)
            if dugme is None:
                continue
            dugme.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _show_tools_for(self, mode: str) -> None:
        """Ust barda YALNIZCA etkin kipin araclarini birakir.

        Gorunurluk WIDGET uzerinden degil EYLEM uzerinden ayarlanir:
        QToolBar kendi yerlesimini kurarken eylemin dugmesini yeniden
        gosterir, bu yuzden `widgetForAction(...).setVisible(False)`
        tutmuyordu -- iki kipin araclari ust uste duruyor, seritte iki
        ayri "Select" dugmesi gorunuyordu.
        """
        for act in self._state_tool_actions:
            act.setVisible(mode == "state")
        for act in self._class_tool_actions:
            act.setVisible(mode == "class")
        self._tool_sep.setVisible(mode in ("state", "class"))

    def _connect(self) -> None:
        self.doc.changed.connect(self._on_model_changed)
        self.doc.path_changed.connect(self._update_title)
        self.class_doc.changed.connect(self._on_model_changed)
        self.class_doc.path_changed.connect(self._update_title)

        self.canvas.selection_changed.connect(self._on_selection)
        self.canvas.status_message.connect(self.flash)
        self.canvas.tool_finished.connect(lambda: self.set_tool(Tool.SELECT))

        self.class_canvas.status_message.connect(self.flash)
        self.class_canvas.tool_finished.connect(
            lambda: self.set_class_tool(ClassTool.SELECT))
        self.class_canvas.selection_changed.connect(self._on_class_selection)

        self.ws_tree.model_activated.connect(self.open_model_path)
        self.ws_tree.model_removed.connect(self._on_model_removed)
        # Agactan secilen eleman ETKIN tuvalde odaklanir. Eskiden yalnizca
        # durum tuvaline bagliydi; sinif diyagraminda agactan bir sinifa
        # tiklamak hicbir sey yapmiyordu.
        self.ws_tree.element_activated.connect(self._focus_element)
        self.ws_tree.model_selected.connect(self._model_selected)

        self.problems.element_activated.connect(self.canvas.focus_element)
        self.class_problems.element_activated.connect(
            self.class_canvas.focus_element)
        self.inspector.message.connect(self.flash)

        self.code_panel.language_changed.connect(self.set_language)
        self.code_panel.export_requested.connect(self.export_code)
        self.code_panel.copy_requested.connect(self.copy_current_file)

        self.sim_panel.active_changed.connect(self.canvas.set_active_states)
        self.sim_panel.message.connect(self.flash)
        # Simulasyon baslayinca diyagrami sigdir: etkin durum zinciri
        # vurgulanir, kullanicinin once elle yakinlastirmasi gerekmez.
        self.sim_panel.started.connect(
            lambda: QTimer.singleShot(0, self.canvas.zoom_fit))

        self.git_panel.status_changed.connect(self._on_git_status)
        # Depo panelinde bir MODEL dosyasi secilince, farki ayrica
        # DIYAGRAM uzerinde de isaretle.
        self.git_panel.model_diff_requested.connect(self._git_model_diff)

        self.mode_tabs.currentChanged.connect(lambda _i: self._sync_mode())

    # ================================================================== akis

    def _on_model_changed(self) -> None:
        # Olay isleme sirasinda eleman silmemek icin bir sonraki dongude yenile.
        self._rebuild_timer.start(0)
        # KOD URETIMI BURADA CALISMAZ. Eskiden her degisiklikten 180 ms sonra
        # tum kod yeniden uretilir, renklendirilir ve DISKE YAZILIRDI; her
        # surukleme/duzenleme jesti bir dosya yazma turu baslatiyor ve arayuz
        # takiliyordu (virus tarayicili makinelerde yazma suresi ongorulemez).
        # Uretim artik yalnizca kullanici Build dedigi zaman calisir.
        self._mark_code_stale()

    def _rebuild_views(self) -> None:
        self.canvas.rebuild()
        self.class_canvas.rebuild()
        # Agactaki ACIK model dugumleri kaydedilmemis degisiklikleri de
        # gostermeli; kapali dugumlere dokunulmaz (tembel yukleme).
        self.ws_tree.set_open_models(self.open_models())
        self.inspector.refresh()
        self.sim_panel.rebuild()
        self._update_title()
        self._update_counts()
        # DOGRULAMA canli kalir (~0.2-1.3 ms): Problems paneli, tuvaldeki hata
        # isaretleri ve durum cubugu bunun uzerinden calisir ve kullanicinin
        # hatayi ANINDA gormesi gerekir. Pahali olan ve diske dokunan adim
        # KOD URETIMIDIR; o Build dugmesine tasindi.
        self._validate_active()

    def _git_model_diff(self, path: str, taban: str) -> None:
        """Depo panelinden secilen modelin farkini DIYAGRAM uzerinde gosterir.

        Metinsel fark bir JSON modelinde okunmaz: bir kutuyu tasimak
        onlarca satir uretir. Kullanicinin sordugu "ne eklendi, ne
        cikarildi" sorusunun cevabi resmin kendisidir.

        Fark ancak dosya TUVALDE ACIKSA cizilebilir; degilse durum
        cubugunda soylenir. Karsilastirma tabani depo panelinin sectigi
        taraftir: hazirlanmis dosyada hazirlik alani, degilse HEAD.
        """
        try:
            acik = None
            hedef = os.path.normcase(os.path.abspath(path))
            for doc in (self.doc, self.class_doc):
                if doc.path and os.path.normcase(
                        os.path.abspath(doc.path)) == hedef:
                    acik = doc
                    break
            if acik is None:
                self.canvas.set_diff_marks(None)
                self.class_canvas.set_diff_marks(None)
                self.flash_warning(
                    "%s — open it to see the change on the diagram."
                    % os.path.basename(path))
                return

            repo = getattr(self.git_panel, "repo", None)
            if repo is None:
                return
            goreli = os.path.relpath(path, repo.root).replace(os.sep, "/")
            eski = (repo.staged_text(goreli) if taban == "staged"
                    else repo.file_at("HEAD", goreli))
            if not eski:
                return
            marks = element_status(eski, acik.machine.to_json())
            tuval = (self.class_canvas if acik is self.class_doc
                     else self.canvas)
            diger = (self.canvas if tuval is self.class_canvas
                     else self.class_canvas)
            diger.set_diff_marks(None)
            tuval.set_diff_marks(marks)
            self.flash("%s — %d added, %d removed, %d changed (vs %s)."
                       % (os.path.basename(path), len(marks["added"]),
                          len(marks["removed"]), len(marks["changed"]),
                          "staged" if taban == "staged" else "HEAD"))
        except Exception as exc:                   # noqa: BLE001
            self.flash_error("Could not compare '%s': %s"
                             % (os.path.basename(path), exc))
            traceback.print_exc()

    def _model_selected(self, path: str) -> None:
        """`model_selected` sinyalinin GUVENLI girisi.

        PyQt6'da bir yuvada (slot) yakalanmamis Python istisnasi sureci
        OLDURUR (0xC0000409). Bir kere tam boyle oldu: sinif tuvalinde
        `set_diff_marks` yoktu, agacta bir modele tiklamak AttributeError
        firlatti ve uygulama -- kaydedilmemis modelle birlikte -- kapandi.

        Fark GOSTERIMI bir kolayliktir; basarisiz olmasi kullanicinin
        calismasini kaybetmesine yol acmamali. Sorun sessizce yutulmaz:
        durum cubugunda bildirilir ve stderr'e yazilir.
        """
        try:
            self._show_model_diff(path)
        except Exception as exc:                   # noqa: BLE001 - kasitli
            try:
                self.canvas.set_diff_marks(None)
                self.class_canvas.set_diff_marks(None)
            except Exception:                      # noqa: BLE001
                pass
            self.flash_error("Could not compare '%s': %s"
                       % (os.path.basename(path), exc))
            traceback.print_exc()

    def _show_model_diff(self, path: str) -> None:
        """Agacta secilen modelin DEGISIKLIKLERINI diyagram uzerinde gosterir.

        Kullanicinin sordugu sey "bu dosyada ne degismis" ve cevabi metin
        farkinda degil DIYAGRAMDA aramak istiyor: eklenen bloklar yesil,
        degisenler sari, SILINENLER eski yerlerinde kesik kirmizi hayalet.

        Karsilastirma tabani, en genis anlamli tabandir:

          * depoda bir HEAD surumu VARSA taban odur -- boylece hem commit
            edilmemis hem de kaydedilmemis degisiklikler TEK RESIMDE gorunur;
          * depo yoksa (ya da dosya hic commit edilmemisse) taban diskteki
            surumdur, yani yalnizca "kaydetmediklerim" gosterilir.

        Fark ancak dosya TUVALDE ACIKSA cizilebilir: baska bir modelin
        farkini ekrandaki diyagramin uzerine boyamak yaniltici olurdu.
        """
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                diskteki = fh.read()
        except OSError:
            return

        acik_doc = None
        hedef = os.path.normcase(os.path.abspath(path))
        for doc in (self.doc, self.class_doc):
            if doc.path and os.path.normcase(os.path.abspath(doc.path)) == hedef:
                acik_doc = doc
                break

        if acik_doc is None:
            # Diyagram ekranda degil; boyayacak bir sey yok.
            self.canvas.set_diff_marks(None)
            self.class_canvas.set_diff_marks(None)
            self.flash("%s — open it (double-click) to compare."
                       % os.path.basename(path))
            return

        yeni = acik_doc.machine.to_json()
        eski = self._head_version(path)
        if eski is not None:
            # YAZIM HATASI BURADAYDI: taban HEAD oldugunda isaretler
            # hesaplaniyor ama tuvale `None` gonderiliyordu; "commit
            # etmediklerim" gorunumu bu yuzden HIC CIZILMIYORDU.
            kaynak = ("uncommitted changes" if yeni == diskteki
                      else "uncommitted + unsaved changes")
        else:
            eski, kaynak = diskteki, "unsaved changes"

        marks = element_status(eski, yeni)
        # Fark YALNIZCA o modelin kipinde anlamlidir.
        tuval = (self.class_canvas if acik_doc is self.class_doc
                 else self.canvas)
        diger = self.canvas if tuval is self.class_canvas else self.class_canvas
        diger.set_diff_marks(None)
        tuval.set_diff_marks(marks)
        toplam = (len(marks["added"]) + len(marks["removed"])
                  + len(marks["changed"]))
        if toplam == 0:
            self.flash("%s — no changes (%s)."
                       % (os.path.basename(path), kaynak))
        else:
            self.flash("%s — %d added, %d removed, %d changed (%s)."
                       % (os.path.basename(path), len(marks["added"]),
                          len(marks["removed"]), len(marks["changed"]),
                          kaynak))

    def _head_version(self, path: str):
        """Dosyanin git HEAD'deki icerigi; depo/dosya yoksa None.

        `Repo.file_at` DEPO KOKUNE GORELI yol ister; mutlak yol verilirse
        git dosyayi bulamaz ve bos doner.
        """
        panel = getattr(self, "git_panel", None)
        repo = getattr(panel, "repo", None) if panel is not None else None
        if repo is None:
            return None
        try:
            goreli = os.path.relpath(path, repo.root).replace(os.sep, "/")
            if goreli.startswith(".."):
                return None            # depo disinda
            metin = repo.file_at("HEAD", goreli)
            return metin or None
        except Exception:
            # Depo bos olabilir (HEAD yok) ya da dosya hic commit
            # edilmemis olabilir; ikisi de HATA DEGIL, "fark yok" demektir.
            return None

    def _focus_element(self, eid: str) -> None:
        """Agactan secilen elemani ETKIN tuvalde odaklar.

        Eleman hangi modele aitse o kipe de gecer: kullanici agacta bir
        sinifa tikladiginda durum diyagrami sekmesinde kalmasi anlamsizdi.
        """
        if eid in self.class_doc.machine.classes \
                or eid in self.class_doc.machine.relations:
            if self.active_mode() != "class":
                self.mode_tabs.setCurrentIndex(1)
            self.class_canvas.focus_element(eid)
            return
        if self.active_mode() != "state":
            self.mode_tabs.setCurrentIndex(0)
        self.canvas.focus_element(eid)

    def _on_class_selection(self, ids: List[str]) -> None:
        if len(ids) == 1:
            self.ws_tree.select_element(ids[0])
        self.a_delete.setEnabled(bool(ids) and self.active_mode() != "git")

    def _on_selection(self, ids: List[str]) -> None:
        if list(ids) == self.inspector.selected_ids():
            # Ayni secim yeniden yayildi (tuval yeniden cizildi). Formu YIKMA:
            # kullanici o anda bir alana yaziyor olabilir; refresh() odak
            # korumasi sayesinde yalnizca guvenliyse tazeler.
            self.inspector.refresh()
        else:
            self.inspector.show_selection(ids)
        if len(ids) == 1:
            # Secim CALISMA ALANI agacinda isaretlenir (model agaci
            # kaldirildi; ayni bilgiyi zaten o agac tasiyor).
            self.ws_tree.select_element(ids[0])
        self.a_delete.setEnabled(bool(ids) and self.active_mode() != "git")

    def set_tool(self, tool: Tool) -> None:
        self.canvas.set_tool(tool)
        action = self.tool_actions.get(tool)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._sync_tool_labels()
        hints = {
            Tool.SELECT: "Double-click an element to edit its properties.",
            Tool.TRANSITION: "Click the source state, then the target state.",
        }
        self.flash(hints.get(tool, "Click the canvas to place it."))

    def set_class_tool(self, tool: ClassTool) -> None:
        self.class_canvas.set_tool(tool)
        action = self.class_tool_actions.get(tool)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        self._sync_tool_labels()

    def set_language(self, key: str) -> None:
        self.code_panel.set_language(key)
        action = self.lang_actions.get(key)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        # Dil degistirmek KOD hakkinda acik bir kullanici istegidir; panelin
        # baska bir dilin kodunu gostermesi yaniltici olurdu.
        self.build()

    def submachine_resolver(self):
        """Altmakine referanslarini cozen islev.

        ACIK BELGE ONCELIKLIDIR: kullanici referans edilen makineyi baska
        bir sekmede degistirip henuz kaydetmediyse, diskteki eski kopyayla
        kod uretmek EKRANDA GORULEN diyagramla uyusmayan bir cikti verirdi.
        """
        from ..core.submachine import workspace_resolver

        acik = {}
        yol = getattr(self.doc, "path", "") or ""
        if yol and self.workspace is not None:
            try:
                acik[self.workspace.relative(yol)] = self.doc.machine
            except Exception:                  # noqa: BLE001
                pass
        return workspace_resolver(self.workspace, acik)

    def force_validate(self) -> None:
        issues = self._validate_active()
        n_err = sum(1 for i in issues if i.is_error)
        if n_err:
            self.flash_error("%d validation error(s)." % n_err)
        else:
            self.flash("Model validated.")

    def _undo(self) -> None:
        if self.active_mode() == "git":
            return          # tuval gorunmuyor; gorunmeyen seyi degistirme
        self.active_doc().undo_stack.undo()

    def _redo(self) -> None:
        if self.active_mode() == "git":
            return          # tuval gorunmuyor; gorunmeyen seyi degistirme
        self.active_doc().undo_stack.redo()

    def _odaktaki_metin(self):
        """Bu pencerede odakta bir METIN alani varsa onu dondurur.

        Kod paneli SALT OKUNUR bir editordur ve Qt'de salt okunur bir
        metin alani `ShortcutOverride` olayini KABUL ETMEZ: pencere
        kapsamli bir QAction kisayolu onu yener. Editable bir alan yener,
        salt okunur olan yenilir -- fark tam olarak budur.

        Sonuc, sessiz bir VERI KAYBIYDI: kullanici uretilen kodu
        kopyalamak icin panele tiklayip Ctrl+A'ya basiyor, kod
        secilmiyor; onun yerine GORMEDIGI tuvalde her sey seciliyor ve
        Del butun modeli siliyordu (9 durum, 11 gecis -> 0). Silme
        eylemi zaten Ctrl+A ile ETKINLESIYOR, yani iki tusluk bir jest.
        """
        from PyQt6.QtWidgets import (QApplication, QLineEdit,
                                     QPlainTextEdit, QTextEdit)
        odak = QApplication.focusWidget()
        if isinstance(odak, (QLineEdit, QPlainTextEdit, QTextEdit)) \
                and self.isAncestorOf(odak):
            return odak
        return None

    def _tuval_odakta(self) -> bool:
        """Odak ETKIN TUVALIN icinde mi (ya da hicbir yerde mi)?

        Tuvali degistiren eylemler yalnizca o zaman calisir. Menuden ya
        da arac cubugundan gelen cagri odagi degistirmez (dugmeler
        Qt::NoFocus), dolayisiyla bu kosul onlari engellemez.
        """
        from PyQt6.QtWidgets import QApplication
        odak = QApplication.focusWidget()
        if odak is None:
            return True
        tuval = self.active_canvas()
        return odak is tuval or tuval.isAncestorOf(odak)

    def _delete_selection(self) -> None:
        if self.active_mode() == "git":
            return          # tuval gorunmuyor; gorunmeyen seyi silme
        if not self._tuval_odakta():
            # Odak baska bir panelde: kod editoru, calisma alani agaci,
            # ozellik alani... Del oraya aittir, tuvale degil.
            return
        self.active_canvas().delete_selection()

    # ---------------------------------------------------------------- uretim

    # -- 1) DOGRULAMA: ucuz, her model degisiminde calisir ------------------ #

    def _validate_active(self) -> List[Issue]:
        """Etkin kipin modelini dogrular ve gostergeleri gunceller.

        Kod URETMEZ ve diske DOKUNMAZ. Boylece surukleme/duzenleme jestleri
        dosya yazma turu baslatmaz; kullanici yine de hatayi aninda gorur.
        """
        if self.active_mode() == "class":
            cm = self.class_doc.machine
            issues = validate_classes(cm)
            panel, canvas = self.class_problems, self.class_canvas
        else:
            issues = validate(self.doc.machine,
                              resolve=self.submachine_resolver())
            panel, canvas = self.problems, self.canvas

        panel.show_issues(issues)
        error_ids: Set[str] = {i.element_id for i in issues
                               if i.is_error and i.element_id}
        warn_ids: Set[str] = {i.element_id for i in issues
                              if i.severity == "warning" and i.element_id}
        canvas.mark_errors(error_ids, warn_ids)
        self._update_issue_label(issues)
        return issues

    # -- 2) URETIM: pahali, YALNIZCA Build ile calisir ---------------------- #

    def _generate_files(self, issues: List[Issue]) -> Optional[Dict[str, str]]:
        """Etkin kipin kodunu uretir. Hata olursa None doner ve panele yazar."""
        if has_errors(issues):
            n_err = sum(1 for i in issues if i.is_error)
            # Bayat dosyalari BIRAKMA: aksi halde Ctrl+E / Ctrl+Shift+G bir
            # onceki (ya da baska kipin) kodunu sessizce diske yazardi.
            self._drop_build()
            self.code_panel.show_blocked(
                "%d validation error(s); code generation stopped." % n_err)
            return None

        language = self.code_panel.current_language()
        is_class = self.active_mode() == "class"
        model = self.class_doc.machine if is_class else self.doc.machine
        table = {
            (False, "c"): generate_c,
            (False, "cpp"): generate_cpp,
            (False, "puml"): generate_plantuml,
            (True, "c"): generate_class_c,
            (True, "cpp"): generate_class_cpp,
            (True, "puml"): generate_class_plantuml,
        }
        generator = table.get((is_class, language), table[(is_class, "puml")])
        try:
            if is_class:
                return generator(model)
            # Durum makinesi ureteclerine ALTMAKINE COZUMLEYICISI verilir.
            return generator(model, resolve=self.submachine_resolver())
        except CodegenError as exc:
            self._drop_build()
            self.code_panel.show_blocked("Code generation failed: %s" % exc)
            return None
        except Exception as exc:                       # beklenmeyen durum
            self._drop_build()
            self.code_panel.show_blocked(
                "Unexpected generation error: %s\n%s"
                % (exc, traceback.format_exc(limit=3)))
            return None

    # -- 3) BUILD: kullanici istegiyle, ilerleme cubugu ile ---------------- #

    #: Build adimlari: (yuzde, durum cubugunda gorunecek metin).
    BUILD_STEPS = (
        (10, "Validating model…"),
        (45, "Generating %s…"),
        (75, "Formatting source…"),
        (95, "Writing to workspace…"),
        (100, "Build finished."),
    )

    def build(self) -> bool:
        """Modeli dogrular, kodu uretir, panele koyar ve calisma alanina yazar.

        TEK GIRIS NOKTASI. Kullanici Build dedigi (F5), dili degistirdigi ya
        da kip degistirdigi zaman calisir; model degisiminde CALISMAZ.

        Adimlar ARKA IS PARCACIGINDA DEGIL, ana is parcaciginda sirayla
        kosar. Olculen surelerde uretim 1-6 ms surer (bkz. tools/); is
        parcacigi eklemek modelin derin kopyasini gerektirir ve kopya ile
        canli model arasinda sessiz tutarsizlik riski dogurur -- uretilen kod
        kritik yerlerde kullanildigi icin bu risk kabul edilebilir degil.
        Ongorulemeyen tek adim DISKE YAZMADIR; cubuk asil orada is gorur.
        """
        if self._building:
            return False                      # yeniden girisi engelle
        self._building = True
        try:
            self._begin_progress()

            self._step_progress(0)
            issues = self._validate_active()

            self._step_progress(1, self.code_panel.current_language().upper())
            files = self._generate_files(issues)
            if files is None:
                self._end_progress(ok=False)
                return False

            self._step_progress(2)
            self._last_files = files
            self._built[self._build_key()] = files
            self.code_panel.set_files(files)

            self._step_progress(3)
            self._auto_write(files)

            self._step_progress(4)
            self._report_build(files, issues)
            self._stale_modes.discard(self.active_mode())
            self.code_panel.set_stale(False)
            self.a_build.setText("&Build")
            self._end_progress(ok=True)
            return True
        finally:
            self._building = False

    def _build_key(self):
        """Onbellek anahtari: (kip, DIL).

        Dil de anahtara girer; aksi halde kullanici C++'a gecip Build dedikten
        sonra diger sekmeye donunce panelde HALA C kodu gorunur, basligi
        'C++' derdi ve Ctrl+E yanlis dosyalari yazardi.
        """
        return (self.active_mode(), self.code_panel.current_language())

    def _drop_build(self) -> None:
        """Etkin kip+dil icin uretilen dosyalari ve ONBELLEGI atar."""
        self._last_files = {}
        self._built.pop(self._build_key(), None)

    def _show_mode_build(self) -> None:
        """Etkin kipin ONBELLEKTEKI derleme ciktisini panele koyar."""
        if self.active_mode() == "git":
            return
        files = self._built.get(self._build_key())
        self._last_files = files or {}
        if files:
            self.code_panel.set_files(files)
            total = sum(len(t.splitlines()) for t in files.values())
            self.code_panel.show_ok("%d files · %d lines" % (len(files), total))
            self.code_panel.set_stale(self._is_stale())
        else:
            self.code_panel.show_blocked(
                "Not built yet — press Build (F5) to generate the code.")

    def _is_stale(self, mode=None) -> bool:
        return (mode or self.active_mode()) in self._stale_modes

    def _mark_code_stale(self) -> None:
        """Degisen MODELIN kipini bayat olarak isaretler.

        Kod artik kendiliginden yenilenmedigi icin kullanici, ekranda gordugu
        kaynagin hangi model surumune ait oldugunu bilmek zorundadir; aksi
        halde eski kodu dogru sanip disa aktarabilirdi.

        Hangi BELGENIN degistigi sinyalin gonderenine bakilarak bulunur:
        kullanici sinif sekmesindeyken durum makinesini de degistirebilir
        (ornegin geri al), o yuzden ETKIN kipe bakmak yanlis olurdu.
        """
        gonderen = self.sender()
        if gonderen is self.class_doc:
            mode = "class"
        elif gonderen is self.doc:
            mode = "state"
        else:
            mode = self.active_mode()
        if mode == "git":
            return
        self._stale_modes.add(mode)
        if mode == self.active_mode():
            self.code_panel.set_stale(True)
            if hasattr(self, "a_build"):
                self.a_build.setText("&Build *")

    def _begin_progress(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._progress_hide.stop()

    def _step_progress(self, index: int, *args) -> None:
        percent, text = self.BUILD_STEPS[index]
        self.progress.setValue(percent)
        self.lbl_message.setText(text % args if args else text)
        # Cubugun GERCEKTEN boyanmasi icin olay dongusune bir tur birak.
        # Kullanici "derleme surecini gostersin" dedi; adimlar arasinda
        # boyama yapilmazsa cubuk yalnizca sonda bir kez gorunurdu.
        QApplication.processEvents()

    def _end_progress(self, ok: bool) -> None:
        if not ok:
            self.progress.setValue(0)
            self.progress.setVisible(False)
            return
        # Hizli bir derlemede cubuk goz kirpmasi kadar kalirdi; kisa bir sure
        # 100%'de tutulur ki kullanici derlemenin BITTIGINI gorsun.
        self._progress_hide.start(900)

    def _report_build(self, files: Dict[str, str],
                      issues: List[Issue]) -> None:
        total = sum(len(t.splitlines()) for t in files.values())
        n_warn = sum(1 for i in issues if i.severity == "warning")
        if n_warn:
            self.code_panel.show_warning(
                "%d warning(s); the code was generated, but review it." % n_warn)
            self.code_panel.status.setText("%d files · %d lines"
                                           % (len(files), total))
            self.code_panel.status.setStyleSheet("color: %s;" % C.WARN)
        else:
            self.code_panel.show_ok("%d files · %d lines" % (len(files), total))
        self.flash("Build finished: %d files · %d lines." % (len(files), total))

        # KULLANICININ YAZMASI GEREKENLERI SOYLE.
        #
        # Model icindeki entry/exit/do/effect/guard govdeleri kullanicinin
        # yazdigi C/C++ metinleridir; icindeki cagrilar uretilmez. Bunu
        # ancak baglama asamasinda "undefined reference" olarak gormek
        # gec ve anlasilmaz. Uretilen basliklarda belgeleniyor, ama
        # kullanici dosyayi acmadan da bilmeli.
        self._report_required(files)

    def _report_required(self, files: Dict[str, str]) -> None:
        """Derleme sonrasi, disaridan saglanmasi gereken sembolleri yazar."""
        try:
            if self.active_mode() != "state":
                self.lbl_external.setText("")
                return
            from ..codegen.ir import build_ir
            gerekli = build_ir(self.doc.machine).required_functions()
        except Exception:                          # noqa: BLE001
            self.lbl_external.setText("")
            return
        if not gerekli:
            self.lbl_external.setText("")
            self.lbl_external.setToolTip("")
            return
        satir_sonu = chr(10)
        self.lbl_external.setText("⚙ %d external" % len(gerekli))
        self.lbl_external.setStyleSheet("color: %s;" % C.WARN)
        self.lbl_external.setToolTip(
            "You must implement these yourself; the generator does not:"
            + satir_sonu
            + satir_sonu.join("  %s      used by: %s"
                              % (r.summary(), ", ".join(r.sites))
                              for r in gerekli)
            + satir_sonu * 2
            + "A missing one fails at LINK time, not at compile time.")

    def _update_issue_label(self, issues: List[Issue]) -> None:
        n_err = sum(1 for i in issues if i.is_error)
        n_warn = sum(1 for i in issues if i.severity == "warning")
        if n_err:
            self.lbl_issues.setText("● %d errors, %d warnings" % (n_err, n_warn))
            self.lbl_issues.setStyleSheet("color: %s;" % C.RED)
        elif n_warn:
            self.lbl_issues.setText("● %d warning(s)" % n_warn)
            self.lbl_issues.setStyleSheet("color: %s;" % C.WARN)
        else:
            self.lbl_issues.setText("● validated")
            self.lbl_issues.setStyleSheet("color: %s;" % C.GREEN)

    def _update_counts(self) -> None:
        if self.active_mode() == "class":
            cm = self.class_doc.machine
            self.lbl_counts.setText("%d classes · %d relationships"
                                    % (len(cm.classes), len(cm.relations)))
        else:
            sm = self.doc.machine
            self.lbl_counts.setText("%d states · %d transitions · %d events"
                                    % (len(sm.states), len(sm.transitions),
                                       len(sm.events())))
        self.lbl_zoom.setText("%d%%" % self.active_canvas().zoom_percent())

    def _update_title(self) -> None:
        if self.workspace is not None:
            self.setWindowTitle("%s — %s — %s"
                                % (self.active_doc().title(),
                                   self.workspace.name, APP_NAME))
        else:
            self.setWindowTitle("%s — %s" % (self.active_doc().title(),
                                             APP_NAME))

    #: Durum cubugu iletisinin SIDDETI -> renk.
    FLASH_RENK = {"error": C.RED, "warning": C.WARN, "ok": C.GREEN}

    def flash(self, text: str, kind: str = "info") -> None:
        """Durum cubuguna ileti yazar ve SIDDETINE gore renklendirir.

        Onceden her ileti duz metin rengindeydi: "Code generation failed"
        ile "Code panel shown" ayni goruntuydu. Kullanici hata / uyari /
        kritik metinlerin vurgulanmasini istedi.
        """
        self.lbl_message.setText(text)
        renk = self.FLASH_RENK.get(kind)
        self.lbl_message.setStyleSheet(
            ("color: %s; font-weight: bold;" % renk) if kind == "error"
            else ("color: %s;" % renk) if renk else "")

    def flash_error(self, text: str) -> None:
        """Basarisiz bir islemi KIRMIZI ile bildirir."""
        self.flash(text, "error")

    def flash_warning(self, text: str) -> None:
        """Dikkat isteyen bir durumu KEHRIBAR ile bildirir."""
        self.flash(text, "warning")

    def commit_pending_edits(self) -> None:
        """OZELLIKLER panelinde yazilmakta olan alani modele isler.

        Alanlar degeri ancak ODAK KAYBINDA modele yazar. Kaydetme, disa
        aktarma ve kapanis yollari modeli dogrudan okudugu icin, imlec bir
        alanin icindeyken yapilan islem kullanicinin az once yazdigi metni
        gormezdi: dosyaya eski deger yazilir, uygulama 'Kaydedildi' derdi.
        Odagi birakmak commit'i tetikler.
        """
        focus = QApplication.focusWidget()
        if focus is not None and self.inspector.isAncestorOf(focus):
            focus.clearFocus()

    # ============================================================ calisma alani

    def recent_workspaces(self) -> List[str]:
        stored = self.settings.value("recent_workspaces", [])
        if isinstance(stored, str):
            stored = [stored]
        return normalise_recent(list(stored or []))

    def choose_workspace(self) -> bool:
        """Calisma alani diyalogunu acar; secilirse uygular."""
        workspace, init_git = pick_workspace(self.recent_workspaces(), self,
                                             allow_cancel=True)
        if workspace is None:
            return False
        self.apply_workspace(workspace, init_git=init_git)
        return True

    def apply_workspace(self, workspace: Workspace,
                        init_git: bool = False) -> None:
        """Calisma alanini benimser: yollar, git paneli, son kullanilanlar."""
        self.workspace = workspace
        try:
            workspace.ensure_layout()
        except WorkspaceError as exc:
            QMessageBox.warning(self, "Workspace", str(exc))

        if init_git and git_available():
            try:
                Repo(workspace.root).init()
            except GitError as exc:
                QMessageBox.warning(self, "Could not initialise the git repository",
                                    exc.message)

        self.settings.setValue(
            "recent_workspaces",
            push_recent(self.recent_workspaces(), workspace.root))
        self.settings.setValue("last_workspace", workspace.root)

        self.a_auto_write.setChecked(workspace.auto_write)
        self.git_panel.set_root(workspace.root)
        self.lbl_workspace.setText("▣ %s" % workspace.name)
        self.lbl_workspace.setToolTip(workspace.root)
        self._last_export_dir = workspace.generated_path
        self.refresh_workspace_tree()
        self._update_title()
        self.flash("Workspace: %s" % workspace.root)
        if self._last_files:
            self._auto_write(self._last_files)

    def open_models(self) -> Dict[str, object]:
        """{mutlak yol: model} -- ACIK belgeler.

        Agac, acik bir modelin DISKTEKI degil DUZENLENMEKTE olan surumunu
        gostermelidir; aksi halde kaydedilmemis durumlar orada gorunmez.
        """
        out = {}
        for doc in (self.doc, self.class_doc):
            if doc.path:
                out[doc.path] = doc.machine
        return out

    def refresh_workspace_tree(self) -> None:
        self.ws_tree.set_workspace(self.workspace, self.open_models())
        self.ws_tree.set_open_models(self.open_models())

    def open_model_path(self, path: str) -> None:
        """Agactan cift tiklanan modeli uygun kipte acar."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                kind = detect_kind(fh.read())
        except OSError as exc:
            QMessageBox.warning(self, "Could not open",
                                "Could not read the file:\n%s\n\n%s"
                                % (path, exc))
            return
        if kind is None:
            QMessageBox.warning(self, "Could not open",
                                "Unrecognised file: not a valid state machine "
                                "(.usm) or class diagram (.ucd).")
            return

        target_mode = 1 if kind == KIND_CLASS else 0
        doc = self.class_doc if kind == KIND_CLASS else self.doc
        if not self._confirm_discard(doc):
            return
        try:
            doc.load(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open", str(exc))
            return
        self.mode_tabs.setCurrentIndex(target_mode)
        self._remember_model(doc, path)
        self.refresh_workspace_tree()
        # Acilan modeli pencereye sigdir; kullanici onceki modelin yakinlastirma
        # duzeyiyle bos bir tuvale bakmasin.
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Opened: %s" % os.path.basename(path))

    def _on_model_removed(self, path: str) -> None:
        """Agactan silinen model ACIKSA tuvali de KAPAT.

        Onceki surum belgenin icerigini tutuyor, yalnizca `path` alanini
        temizliyordu. Sonuc kullaniciyi yaniltiyordu: dosya calisma
        alanindan kalkiyor ama diyagram tuvalde duruyor, baslikta da
        "sm.usm*" yaziyordu -- yani artik VAR OLMAYAN bir dosyanin adi.
        Calisma alaninda bulunmayan bir model gosterilmemeli.

        Veri kaybi riski yok: silme her zaman onay diyalogundan gecer
        (bkz. workspace_tree.remove_selected) ve diyalog acik modelin
        KAPATILACAGINI soyler.
        """
        hedef = os.path.normcase(os.path.abspath(path))
        for doc, bos in ((self.doc, empty_machine),
                         (self.class_doc, empty_class_model)):
            if not doc.path:
                continue
            if os.path.normcase(os.path.abspath(doc.path)) != hedef:
                continue
            doc.replace(bos(), None)          # tuval bosalir, path temizlenir
        self.refresh_workspace_tree()
        # Uretilen kod da silinen modele aitti; bayat kalmasin.
        self._drop_build()
        self._show_mode_build()
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Removed: %s" % os.path.basename(path))

    def reveal_workspace(self) -> None:
        if self.workspace is None:
            self.flash_warning("No workspace selected.")
            return
        _open_in_file_manager(self.workspace.root)

    def toggle_auto_write(self, checked: bool) -> None:
        if self.workspace is None:
            return
        self.workspace.auto_write = bool(checked)
        try:
            self.workspace.save()
        except WorkspaceError:
            pass
        self.flash("Automatic write after generation: %s."
                   % ("on" if checked else "off"))
        if checked and self._last_files:
            self._auto_write(self._last_files)

    def _auto_write(self, files: Dict[str, str]) -> None:
        """Uretilen dosyalari calisma alanina yazar (acikken)."""
        if self.workspace is None or not self.workspace.auto_write or not files:
            return
        self._write_files(files, announce=False)

    def write_generated_now(self) -> None:
        self.commit_pending_edits()
        # Diske GUNCEL kod yazilmalidir; bu yuzden once derlenir.
        self.build()
        if self.workspace is None:
            if not self.choose_workspace():
                return
        if not self._last_files:
            self.flash_warning("There is no generated file to write.")
            return
        self._write_files(self._last_files, announce=True)

    def _write_files(self, files: Dict[str, str], announce: bool) -> None:
        try:
            written = self.workspace.write_generated(files)
        except WorkspaceError as exc:
            QMessageBox.critical(self, "Could not write", str(exc))
            return
        if written:
            self.flash("%d file(s) written: %s"
                       % (len(written), self.workspace.generated_path))
            self.refresh_git()
        elif announce:
            self.flash("Files were already up to date: %s"
                       % self.workspace.generated_path)

    # ====================================================================== depo

    def refresh_git(self) -> None:
        self.git_panel.refresh()

    def show_git_tab(self) -> None:
        self.mode_tabs.setCurrentIndex(2)

    def _on_git_status(self, text: str) -> None:
        self.lbl_git.setText(("⎇ " + text) if text else "")

    # ================================================================== dosya

    def _confirm_discard(self, doc: Optional[Document] = None) -> bool:
        self.commit_pending_edits()
        docs = [doc] if doc is not None else [self.doc, self.class_doc]
        for d in docs:
            if not d.is_dirty():
                continue
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText("The changes to '%s' have not been saved." % d.title())
            box.setInformativeText("What do you want to do?")
            box.setIcon(QMessageBox.Icon.Warning)
            save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Don't save", QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is cancel:
                return False
            if box.clickedButton() is save:
                if not self._save_doc(d):
                    return False
        return True

    def file_new(self) -> None:
        doc = self.active_doc()
        if not self._confirm_discard(doc):
            return
        if self.active_mode() == "class":
            doc.replace(empty_class_model(), None)
        else:
            doc.replace(empty_machine(), None)
            self.set_tool(Tool.SELECT)
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("New diagram created.")

    def _build_examples_menu(self, parent_menu) -> None:
        """File > Examples: her arac icin AYRI bir model.

        Menu metni ornegin adini, ipucu ise hangi araclari ogrettigini ve
        ilgili UML 2.5.1 madde numarasini tasir; boylece kullanici hangi
        ornegi neden acacagini menuden okuyabilir.
        """
        gal = parent_menu.addMenu("&Examples")
        self._menus_examples = gal
        for baslik, liste, kip in (("&State Machine", STATE_EXAMPLES, "state"),
                                   ("&Class Diagram", CLASS_EXAMPLES, "class")):
            alt = gal.addMenu(baslik)
            for ornek in liste:
                eylem = QAction(ornek.title, self)
                ipucu = "%s\n%s\nUML 2.5.1 §%s" % (
                    ornek.teaches, ornek.summary, ornek.reference)
                eylem.setToolTip(ipucu)
                eylem.setStatusTip("%s — %s" % (ornek.teaches, ornek.summary))
                eylem.triggered.connect(
                    lambda _c=False, o=ornek, k=kip: self.load_example(o, k))
                alt.addAction(eylem)
            alt.setToolTipsVisible(True)
        gal.setToolTipsVisible(True)

    def load_example(self, ornek, kip: str) -> None:
        """Galeriden bir ornegi yukler ve dogru diyagram kipine gecer."""
        belge = self.class_doc if kip == "class" else self.doc
        if not self._confirm_discard(belge):
            return
        self.mode_tabs.setCurrentIndex(1 if kip == "class" else 0)
        belge.replace(ornek.build(), None)
        if kip == "state":
            self.set_tool(Tool.SELECT)
        tuval = self.class_canvas if kip == "class" else self.canvas
        QTimer.singleShot(40, tuval.zoom_fit)
        self.flash("%s — %s" % (ornek.title, ornek.teaches))

    def load_demo(self) -> None:
        if not self._confirm_discard(self.doc):
            return
        self.mode_tabs.setCurrentIndex(0)
        self.doc.replace(demo_machine(), None)
        QTimer.singleShot(40, self.canvas.zoom_fit)
        self.flash("Sample state machine loaded.")

    def load_demo_class(self) -> None:
        if not self._confirm_discard(self.class_doc):
            return
        self.mode_tabs.setCurrentIndex(1)
        self.class_doc.replace(demo_class_model(), None)
        QTimer.singleShot(40, self.class_canvas.zoom_fit)
        self.flash("Sample class diagram loaded.")

    def file_open(self) -> None:
        filt = CD_FILTER if self.active_mode() == "class" else SM_FILTER
        start = self.workspace.model_path if self.workspace is not None else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open diagram", start, filt)
        if not path:
            return

        # Kipi ICERIK belirler, uzanti degil: '.json' her iki turu de tasiyabilir
        # ve yanlis kipe yuklenen dosya bos bir modele donusup ilk kaydetmede
        # ozgun icerigi silerdi.
        try:
            with open(path, "r", encoding="utf-8") as fh:
                kind = detect_kind(fh.read())
        except (OSError, UnicodeDecodeError) as exc:
            QMessageBox.critical(self, "Could not open",
                                 "Could not read the file:\n%s\n\n%s" % (path, exc))
            return
        if kind is None:
            QMessageBox.critical(
                self, "Could not open",
                "This file is not a UML Design Studio model:\n\n%s\n\n"
                "Expected: a state machine (.usm) or a class diagram (.ucd)."
                % path)
            return

        doc = self.class_doc if kind == KIND_CLASS else self.doc
        if not self._confirm_discard(doc):
            return
        self.mode_tabs.setCurrentIndex(1 if kind == KIND_CLASS else 0)
        try:
            doc.load(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open",
                                 "Could not read the file:\n%s\n\n%s" % (path, exc))
            return
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Opened: %s" % path)

    def _save_doc(self, doc: Document) -> bool:
        self.commit_pending_edits()
        if not doc.path:
            is_class = doc is self.class_doc
            if is_class:
                suggested = "%s.ucd" % doc.machine.prefix
                filt = CD_FILTER
            else:
                suggested = "%s.usm" % doc.machine.prefix
                filt = SM_FILTER
            if self.workspace is not None:
                suggested = self.workspace.model_file(suggested)
            path, _ = QFileDialog.getSaveFileName(self, "Save as",
                                                  suggested, filt)
            if not path:
                return False
            try:
                doc.save(path)
            except OSError as exc:
                QMessageBox.critical(self, "Could not save", str(exc))
                return False
            self._remember_model(doc, path)
            self.flash("Saved: %s" % path)
            return True
        try:
            doc.save(doc.path)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self._remember_model(doc, doc.path)
        self.flash("Saved: %s" % doc.path)
        return True

    def _remember_model(self, doc: Document, path: str) -> None:
        """Calisma alaninda son kullanilan model dosyasini not eder."""
        if self.workspace is None or not path:
            return
        rel = self.workspace.relative(path)
        if doc is self.class_doc:
            self.workspace.last_class_model = rel
        else:
            self.workspace.last_state_model = rel
        try:
            self.workspace.save()
        except WorkspaceError:
            pass
        # Yeni kaydedilen dosya WORKSPACE agacinda GORUNMELI: aksi halde
        # kaydetme basarili olsa da kullanici modelin calisma alanina
        # girmedigini sanir.
        self.refresh_workspace_tree()
        self.refresh_git()

    def file_save(self) -> bool:
        return self._save_doc(self.active_doc())

    def file_save_as(self) -> bool:
        self.commit_pending_edits()
        doc = self.active_doc()
        is_class = doc is self.class_doc
        suggested = doc.path or ("%s.%s" % (doc.machine.prefix,
                                            "ucd" if is_class else "usm"))
        filt = CD_FILTER if is_class else SM_FILTER
        path, _ = QFileDialog.getSaveFileName(self, "Save as", suggested,
                                              filt)
        if not path:
            return False
        try:
            doc.save(path)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return False
        self._remember_model(doc, path)
        self.flash("Saved: %s" % path)
        return True

    # ---------------------------------------------------------------- disa akt.

    def export_code(self) -> None:
        self.commit_pending_edits()
        # Disa aktarilan kod MODELLE AYNI olmalidir; onceki derlemenin bayat
        # ciktisini sessizce yazmak kabul edilemez.
        self.build()
        if not self._last_files:
            QMessageBox.information(
                self, "No file to export",
                "Fix the validation errors first; export becomes "
                "available once the code is generated.")
            return
        start = self._last_export_dir \
            or os.path.dirname(self.active_doc().path or "") or ""
        folder = QFileDialog.getExistingDirectory(self, "Target folder", start)
        if not folder:
            return

        existing = [n for n in self._last_files
                    if os.path.exists(os.path.join(folder, n))]
        if existing:
            reply = QMessageBox.question(
                self, "Overwrite?",
                "These files already exist and will be overwritten:"
                "\n\n  %s\n\nContinue?"
                % "\n  ".join(existing),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            for name, text in self._last_files.items():
                with open(os.path.join(folder, name), "w",
                          encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
        except OSError as exc:
            QMessageBox.critical(self, "Could not write", str(exc))
            return

        self._last_export_dir = folder
        self.flash("%d file(s) written: %s" % (len(self._last_files), folder))

    def copy_current_file(self) -> None:
        name, text = self.code_panel.current_file()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.flash("Copied to the clipboard: %s" % name)

    # ================================================================== diger

    def select_all(self) -> None:
        metin = self._odaktaki_metin()
        if metin is not None:
            # Kullanici bir METIN alanindayken Ctrl+A'ya bastiysa metni
            # secmek ister. Salt okunur kod panelinde Qt bunu kendisi
            # yapamadigi icin (bkz. _odaktaki_metin) burada yapilir.
            metin.selectAll()
            return
        if self.active_mode() == "git":
            return          # tuval gorunmuyor
        if not self._tuval_odakta():
            return
        if self.active_mode() == "class":
            cm = self.class_doc.machine
            ids = list(cm.classes.keys()) + list(cm.relations.keys())
            self.class_canvas.set_selected_ids(ids)
        else:
            sm = self.doc.machine
            ids = list(sm.states.keys()) + list(sm.transitions.keys())
            self.canvas.set_selected_ids(ids)

    def toggle_snap(self, checked: bool) -> None:
        self.canvas.snap_enabled = checked
        self.class_canvas.snap_enabled = checked
        self.flash("Snap to grid: %s (elements move freely when off)."
                   % ("on" if checked else "off"))

    def toggle_align(self, checked: bool) -> None:
        self.canvas.align_enabled = checked
        self.class_canvas.align_enabled = checked
        if not checked:
            self.canvas.align_clear()
            self.class_canvas.align_clear()
        self.flash("Alignment guides: %s." % ("on" if checked else "off"))

    def toggle_fullscreen(self, checked: bool) -> None:
        """F11 / menu: tam ekrana gecer, ciktiginda ONCEKI duruma doner.

        `showNormal()` cagirmak yanlis olurdu: pencere tam ekrandan once
        BUYUTULMUS ise kullanici onu kucultulmus halde geri alirdi.
        """
        if checked:
            self._pre_fullscreen_maximized = self.isMaximized()
            self.showFullScreen()
            self.flash("Full screen - press F11 or Esc to exit.")
        elif getattr(self, "_pre_fullscreen_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()

    def changeEvent(self, event) -> None:
        """Pencere durumu DISARIDAN degisirse menudeki tiki esitler."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            eylem = getattr(self, "a_fullscreen", None)
            if eylem is not None and eylem.isChecked() != self.isFullScreen():
                eylem.blockSignals(True)
                eylem.setChecked(self.isFullScreen())
                eylem.blockSignals(False)

    def keyPressEvent(self, event) -> None:
        """Esc tam ekrandan cikarir (F11 ile ayni yol kullanilir)."""
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.a_fullscreen.setChecked(False)
            self.toggle_fullscreen(False)
            return
        super().keyPressEvent(event)

    def set_theme(self, name: str) -> None:
        """Koyu / acik tema arasinda gecis yapar.

        UC ADIM gerekir ve ucu de zorunludur:

          1. ``apply_theme`` -- ``C`` niteliklerini yeni palete cevirir.
          2. Uygulama stil sayfasi YENIDEN uygulanir; stil sayfasi
             kurulurken renkleri METNE gomdugu icin sadece ``C``yi
             degistirmek yetmez.
          3. Renkleri KURULUM aninda okumus bilesenler (tuval firca ve
             kalemleri, satir-ici ``setStyleSheet`` kullanan paneller)
             ``retheme()`` ile yeniden kurulur. Bu adim atlanirsa arayuz
             yeni temaya gecer ama tuval eski zeminde kalir.
        """
        yeni = apply_theme(name)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet())

        for widget in (self.canvas, self.class_canvas, self.code_panel,
                       self.sim_panel, self.git_panel, self.inspector,
                       self.problems, self.class_problems,
                       self.ws_tree):
            fn = getattr(widget, "retheme", None)
            if callable(fn):
                fn()

        # Bolum baslik seritleri (WORKSPACE / PROPERTIES / PROBLEMS).
        for header in self._section_headers:
            header.setStyleSheet(
                "background: %s; color: %s; border-bottom: 1px solid %s;"
                % (C.PANEL_DARK, C.TEXT_DIM, C.BORDER))

        # SIMGELER YENIDEN CIZILIR. Simgeler kurulum anindaki tema
        # renkleriyle bir kez cizilip QAction'a gomulur; tema degisince
        # yeniden uretilmezlerse acik zeminde beyaz (yani gorunmez)
        # kalirlar -- arac cubugu bombos gorunur.
        for action, factory in self._action_icons.items():
            action.setIcon(factory())

        # Tuval sahnesi zemini ve ogeler modelden yeniden kurulur.
        self.canvas.rebuild()
        self.class_canvas.rebuild()
        self._rebuild_views()

        act = self.theme_actions.get(yeni)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.flash("%s theme applied." % THEMES[yeni])

    def toggle_grid(self, checked: bool) -> None:
        self.canvas.show_grid = checked
        self.class_canvas.show_grid = checked
        self.canvas.viewport().update()
        self.class_canvas.viewport().update()
        self.flash("Grid: %s." % ("visible" if checked else "hidden"))

    #: Diyagram bolmesinin ana bolucude alabilecegi EN KUCUK pay.
    #: Bunun altinda tuval calisilabilir olmaktan cikar.
    MIN_DIAGRAM_SHARE = 0.22

    def _sizes_usable(self, sizes) -> bool:
        """Kaydedilmis bolucu paylari calisilabilir mi.

        Yalnizca sifir denetlemek yetmez: 1 px de "sifir degil"dir ama
        kullanilamaz. Diyagram bolmesi -- uc kipin de icinde durdugu bolme --
        toplamin belirgin bir kismini almalidir.
        """
        toplam = sum(sizes)
        if toplam <= 0:
            return False
        if any(v < 0 for v in sizes):
            return False
        index = self.main_splitter.indexOf(self.mode_tabs)
        if index < 0 or index >= len(sizes):
            return False
        return (sizes[index] / float(toplam)) >= self.MIN_DIAGRAM_SHARE

    @staticmethod
    def _restore_splitter_share(splitter, widget, ratio: float) -> None:
        """Bilesene splitter icinde GORUNUR bir pay verir.

        QSplitter, gizlenen bir bilesenin payini komsulara dagitir ve pay
        yeniden gosterildiginde 0 kalir. Bilesen "gorunur" olur ama ekranda
        hic yer kaplamaz; kullaniciya dugme calismiyor gibi gelir. Ayni sey
        kullanici ayirici cubugu sonuna kadar surukleyip pencereyi kapattiginda
        da olur: kaydedilen 0 pay bir sonraki acilista geri yuklenir.
        """
        index = splitter.indexOf(widget)
        if index < 0:
            return
        sizes = splitter.sizes()
        if index >= len(sizes) or sizes[index] > 0:
            return
        total = sum(sizes) or splitter.width() or splitter.height()
        if total <= 0:
            return
        donor = max(range(len(sizes)), key=lambda i: sizes[i])
        if donor == index:
            return
        share = max(1, int(total * ratio))
        share = min(share, max(1, sizes[donor] - 1))
        sizes[donor] -= share
        sizes[index] = share
        splitter.setSizes(sizes)

    def reset_layout(self) -> None:
        """Panel boyutlarini varsayilana dondurur.

        Gizlenmis panelleri de geri getirir: kullanici "yerlesimi sifirla"
        dediginde beklentisi ILK ACILISTAKI goruntudur, yarim yamalak bir
        ara durum degil.
        """
        self.a_code_panel.setChecked(True)
        self.code_panel.setVisible(True)
        # Simulasyon paneli ILK ACILISTA KAPALI; "sifirla" da onu kapatir.
        # Acik birakmak, sifirlamayi ilk acilistan FARKLI bir duruma
        # gotururdu.
        self.a_sim_panel.setChecked(False)
        self.sim_panel.setVisible(False)
        self._clear_simulation()

        toplam = max(self.main_splitter.width(), 900)
        self.main_splitter.setSizes([int(toplam * 0.16),
                                     int(toplam * 0.52),
                                     int(toplam * 0.32)])
        self.left_col.setSizes([330, 560])
        self.state_center.setSizes([150, 720, 120])
        self.flash("Layout reset.")

    def code_panel_open(self) -> bool:
        """Kod paneli GERCEKTEN ekranda mi.

        `isVisible()` yetmez: ayirici sonuna kadar suruklendiginde bilesen
        "gorunur" kalir ama genisligi sifirdir.
        """
        index = self.main_splitter.indexOf(self.code_panel)
        if index < 0 or not self.code_panel.isVisible():
            return False
        sizes = self.main_splitter.sizes()
        return index < len(sizes) and sizes[index] > 0

    def _sync_code_panel_action(self, *_args) -> None:
        """Ayirici suruklendiginde menu isaretini gercege esitler."""
        acik = self.code_panel_open()
        if acik == self.a_code_panel.isChecked():
            return
        # Isareti DEGISTIRMEK eylemi tetiklerdi (toggle_code_panel yeniden
        # pay verir ve kullanicinin surukledigi ayirici geri zipllardi).
        self.a_code_panel.blockSignals(True)
        self.a_code_panel.setChecked(acik)
        self.a_code_panel.blockSignals(False)
        self.flash("Code panel hidden." if not acik else "Code panel shown.")

    def toggle_code_panel(self, checked: bool) -> None:
        self.code_panel.setVisible(checked)
        if checked:
            self._restore_splitter_share(self.main_splitter,
                                         self.code_panel, 0.40)
            # `_restore_splitter_share` bagis yapacak bir komsu bulamazsa
            # SESSIZCE doner ve panel 0 piksel kalir: kullanici "actim ama
            # gelmiyor" der. Son bir kez zorla.
            self._force_panel_share(self.main_splitter, self.code_panel, 0.34)
        self.flash("Code panel shown." if checked else "Code panel hidden.")

    @staticmethod
    def _force_panel_share(splitter, widget, ratio: float) -> None:
        """Bilesenin payi hala 0 ise paylari YENIDEN dagitir.

        `_restore_splitter_share` ihtiyatlidir: en buyuk komsudan pay
        koparamiyorsa dokunmadan doner. Bir paneli ACMAK istendiginde bu
        yetmez -- kullanici icin "acilmadi" demektir.
        """
        index = splitter.indexOf(widget)
        if index < 0:
            return
        sizes = splitter.sizes()
        if index >= len(sizes) or sizes[index] > 0:
            return
        toplam = sum(sizes) or splitter.width() or splitter.height()
        if toplam <= 0:
            return
        pay = max(1, int(toplam * ratio))
        kalan = toplam - pay
        digerler = [i for i in range(len(sizes)) if i != index]
        if not digerler:
            return
        # Kalani komsulara ORANLARINI koruyarak dagit; hepsi 0 ise esit bolus.
        eski = sum(sizes[i] for i in digerler)
        for i in digerler:
            if eski > 0:
                sizes[i] = max(1, int(kalan * sizes[i] / eski))
            else:
                sizes[i] = max(1, kalan // len(digerler))
        sizes[index] = pay
        splitter.setSizes(sizes)

    def toggle_sim_panel(self, checked: bool) -> None:
        self.sim_panel.setVisible(checked)
        if checked:
            self._restore_splitter_share(self.state_center,
                                         self.sim_panel, 0.30)
        else:
            self._clear_simulation()
        self.flash("Simulation shown." if checked else "Simulation hidden.")

    def _clear_simulation(self) -> None:
        """Benzetimi durdurur ve tuvaldeki etkin durum vurgusunu siler.

        Paneli gizlemek tek basina yetmiyordu: son etkin durum tuvalde
        vurgulu kaliyor, kullanici onu bir SECIM sanip silmeye ya da
        tasimaya calisiyordu. reset() `active_changed([])` yayinlar,
        tuval de her durumun `is_active` bayragini indirir.
        """
        self.sim_panel.reset(quiet=True)

    def show_reference(self, title: str, detail: str) -> None:
        QMessageBox.information(self, title, "<b>%s</b><p>%s</p>"
                                % (title, detail.replace("\n", "<br>")))

    def show_spec(self) -> None:
        """UML 2.5.1 spesifikasyonunu AYRI bir pencerede acar.

        Belge pakete GOMULMEZ: OMG telifi altindadir ve 18 MB'dir.
        Yerelde varsa acilir, yoksa kullaniciya sorulup ONUN ONAYIYLA
        resmi adresten indirilir (bkz. spec_window.ensure_spec_pdf).
        """
        from .spec_window import SpecWindow, ensure_spec_pdf

        # ES ZAMANLI IKINCI CAGRI ENGELLENIR.
        #
        # Indirme suruyorken menu ogesine yeniden tiklamak ikinci bir
        # indirici baslatirdi; iki is parcacigi AYNI `.part` dosyasina
        # yazar ve sonuc bozulur. Eylem de pasiflestirilir ki kullanici
        # neden tepki alamadigini gorsun.
        if getattr(self, "_spec_busy", False):
            return
        self._spec_busy = True
        self.a_spec.setEnabled(False)
        try:
            yol = ensure_spec_pdf(self)
        finally:
            self._spec_busy = False
            self.a_spec.setEnabled(True)
        if not yol:
            return

        # ONCEKI PENCERE DUZGUN YIKILIR.
        #
        # Yalnizca `close()` cagirip basvuruyu ustune yazmak, Qt nesnesini
        # Python cop toplayicisinin insafina birakiyordu; ust penceresi
        # olmayan bir QMainWindow icin bu, hala gosterilirken silinmek
        # anlamina gelebilir.
        mevcut = getattr(self, "_spec_window", None)
        self._spec_window = None
        if mevcut is not None:
            try:
                mevcut.close()
                mevcut.deleteLater()
            except Exception:                  # noqa: BLE001
                pass
        try:
            pencere = SpecWindow(yol, None)
        except Exception as exc:               # noqa: BLE001
            QMessageBox.critical(
                self, "UML 2.5.1 specification",
                "The PDF viewer could not be started:"
                + chr(10) * 2 + str(exc))
            self.flash_error("The specification viewer could not start.")
            return
        self._spec_window = pencere
        pencere.show()
        pencere.raise_()
        self.flash("UML 2.5.1 specification opened in a separate window.")

    def show_license(self) -> None:
        text = _load_license_text()
        dlg = QDialog(self)
        dlg.setWindowTitle("GNU General Public License v3")
        dlg.resize(720, 560)
        layout = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(mono_font(9))
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.clicked.connect(lambda _b: dlg.accept())
        layout.addWidget(buttons)
        dlg.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self, "About",
            "<h3>%s %s</h3>"
            "<p>A design tool that turns UML 2.5.1 state machines and class "
            "diagrams into <b>MISRA-compliant C11 / C++11</b> code for "
            "embedded targets.</p>"
            "<p>The generated code uses no dynamic memory and no recursion; it "
            "compiles warning-free with <code>-Wall -Wextra -pedantic "
            "-Werror</code>.</p>"
            "<p>A personal side project by <b>Kubilay Közleme</b>, built to "
            "make the work of embedded software engineers simpler: the model "
            "stays the single source of truth, and the firmware that ships is "
            "generated from it rather than hand-written and kept in sync by "
            "memory.</p>"
            "<p>© 2026 Kubilay Közleme — licensed under the GNU GPL v3.</p>"
            % (APP_NAME, APP_VERSION))

    def show_shortcuts(self) -> None:
        rows = [
            ("Double-click", "Edit the element (name, behavior, guard…)"),
            ("V", "Select tool"),
            # HARFLER ELLE YAZILMAZ. Listeler paletle birlikte buyudu ama
            # bu iki satir donmus kalmisti: fork, join, giris/cikis
            # noktasi ve altmakine HIC gorunmuyor, var olmayan bir "T"
            # goruniyor ve sinif tarafinda Interface "E" iken "I"
            # yaziyordu. Tek kaynak paletin kendisidir.
            (" ".join(t[2] for t in STATE_TOOLS if t[0] is not Tool.SELECT),
             "State-mode tools"),
            (" ".join(t[2] for t in CLASS_TOOLS
                      if t[0] is not ClassTool.SELECT),
             "Class-mode tools"),
            ("Esc", "Drop the tool / cancel the transition"),
            ("Del", "Delete the selected elements"),
            ("Arrow keys", "Nudge by 10 px (1 px with Shift)"),
            ("Ctrl + wheel", "Zoom in / out"),
            ("Middle-button drag", "Pan the canvas"),
            ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
            ("Ctrl+S / Ctrl+O", "Save / open"),
            ("Ctrl+E", "Export the generated code"),
            ("F5 / F7", "Regenerate the code / validate the model"),
            ("F6 / F8", "Refresh the repository / open the repository panel"),
            ("Ctrl+Shift+W", "Choose the workspace"),
            ("Ctrl+Shift+G", "Write the generated code into the workspace"),
            ("F9 / F10", "Show or hide the code panel / simulation"),
            ("Ctrl+Shift+F", "Fit the diagram"),
        ]
        html = ("<h3>Shortcuts</h3><table cellpadding='5'>"
                + "".join("<tr><td><b><code>%s</code></b></td><td>%s</td></tr>"
                          % r for r in rows)
                + "</table>")
        QMessageBox.information(self, "Shortcuts", html)

    # ---------------------------------------------------------------- pencere

    def _restore_state(self) -> None:
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        sizes = self.settings.value("splitter")
        if sizes:
            try:
                degerler = [int(v) for v in sizes]
            except (TypeError, ValueError):
                degerler = []
            # ELEMAN SAYISI TUTMALI. Sol sutun ana bolucuye sonradan
            # eklendi; onceki surumden kalan 2 elemanli ayar 3 cocuklu
            # bolucuye uygulanirsa geri kalan cocuk 0 piksel kalir ve sol
            # panel acilista gorunmez olurdu.
            #
            # PAY ORANI DA MAKUL OLMALI. Kaydedilmis ayar ['230','0','1364']
            # ile acilan uygulamada diyagram bolmesi 0 pikseldi ve uc kip de
            # (durum, sinif, depo) onun icinde oldugu icin "hicbir sey
            # acilmiyor" gibi gorunuyordu. Asgari genislik sonradan bolmeyi
            # gorunur yapar ama ORANI duzeltmez: tuval 320 px'e sikisirken
            # kod paneli 1344 px alirdi. Bozuk bir kayit KISMEN duzeltilmez,
            # tamamen atilir.
            if len(degerler) == self.main_splitter.count() \
                    and self._sizes_usable(degerler):
                self.main_splitter.setSizes(degerler)
        # Kaydedilmis pay 0 olabilir (kullanici ayiriciyi sonuna kadar
        # surukleyip cikmistir). Panel "acik" isaretliyken gorunmez kalirsa
        # dugme bozuk sanilir; paneli goruntuye geri getir.
        self._restore_splitter_share(self.main_splitter, self.left_col, 0.18)
        # Kod paneli AYRI: kullanici onu bilerek kapatmis olabilir
        # (ayiriciyi sona surukleyerek ya da F9 ile). O durumda geri
        # getirmek, kapatma isini her acilista bozar.
        if self.settings.value("code_panel_open", True, type=bool):
            self._restore_splitter_share(self.main_splitter,
                                         self.code_panel, 0.40)
        else:
            # PANELI GERCEKTEN GIZLE. Eskiden yalnizca isaret kaldiriliyordu:
            # panel Qt icin "gorunur" kaliyor ama genisligi 0 oluyordu.
            # O durumda menuden acmak `setVisible(True)` cagirir, panel
            # zaten gorunur oldugu icin hicbir sey degismez ve dugme
            # calismiyor sanilir.
            self.code_panel.setVisible(False)
            self.a_code_panel.blockSignals(True)
            self.a_code_panel.setChecked(False)
            self.a_code_panel.blockSignals(False)
        # Simulasyon paneli: varsayilan KAPALI, ama kullanici actiysa
        # tercihi korunur.
        if self.settings.value("sim_panel_open", False, type=bool):
            self.a_sim_panel.blockSignals(True)
            self.a_sim_panel.setChecked(True)
            self.a_sim_panel.blockSignals(False)
            self.sim_panel.setVisible(True)
            self._restore_splitter_share(self.state_center,
                                         self.sim_panel, 0.30)
        else:
            self.sim_panel.setVisible(False)
        # DIYAGRAM BOLMESI EN SON ve KOSULSUZ kurtarilir.
        #
        # Bu, korumasi OLMAYAN tek bolmeydi ve tam da o coktu: kaydedilmis
        # ayar ['230', '0', '1364'] ile acilan uygulamada tuval 0 piksel
        # genisligindeydi. Uc kip de (durum, sinif, depo) bu bolmenin
        # icinde oldugu icin uygulama "hicbir sey acilmiyor" gibi
        # gorunuyordu -- ustelik kullanicinin geri getirmek icin
        # tutabilecegi gorunur bir ayirici da kalmiyordu.
        self._restore_splitter_share(self.main_splitter, self.mode_tabs, 0.45)
        # IZGARA AYARLARI KALICI. Kullanici "Snap to Grid"i kapatip
        # uygulamayi yeniden actiginda ayarin geri acilmasi, dugmenin
        # calismadigi izlenimi veriyordu.
        for eylem, anahtar in ((self.a_snap, "snap_to_grid"),
                               (self.a_align, "align_guides"),
                               (self.a_grid, "show_grid")):
            kayit = self.settings.value(anahtar)
            if kayit is not None:
                acik = kayit in (True, "true", "True", 1, "1")
                eylem.setChecked(acik)
                eylem.triggered.emit(acik)

        tema = self.settings.value("theme", "dark")
        if tema != active_theme():
            self.set_theme(tema)
        self.theme_actions[active_theme()].setChecked(True)

        lang = self.settings.value("language", "c")
        if lang in ("c", "cpp", "puml"):
            self.code_panel.set_language(lang)
            self.lang_actions[lang].setChecked(True)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.git_panel.shutdown()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.main_splitter.sizes())
        # KULLANICININ NIYETI de kaydedilir, yalnizca paylar degil.
        # Pay 0 iki ayri seyden gelebilir: kullanici paneli bilerek
        # kapatmistir, ya da kayit bozuktur. Ayrimi yapmadan acilista
        # paneli kosulsuz geri getirmek, kapatma islemini kalicilastirmaz.
        self.settings.setValue("code_panel_open", self.code_panel_open())
        self.settings.setValue("sim_panel_open",
                               self.a_sim_panel.isChecked())
        self.settings.setValue("language", self.code_panel.current_language())
        self.settings.setValue("theme", active_theme())
        self.settings.setValue("snap_to_grid", self.a_snap.isChecked())
        self.settings.setValue("show_grid", self.a_grid.isChecked())
        self.settings.setValue("align_guides", self.a_align.isChecked())
        if self.workspace is not None:
            try:
                self.workspace.save()
            except WorkspaceError:
                pass
        event.accept()


def _load_license_text() -> str:
    """LICENSE dosyasini kaynak agacindan ya da PyInstaller paketinden okur."""
    candidates = []
    if getattr(sys, "_MEIPASS", None):
        candidates.append(os.path.join(sys._MEIPASS, "LICENSE"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "..", "LICENSE"))
    candidates.append(os.path.join(os.path.dirname(sys.argv[0] or "."), "LICENSE"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            continue
    return ("This program is distributed under the terms of the GNU General "
            "Public License v3.\n"
            "Full text: https://www.gnu.org/licenses/gpl-3.0.txt")


def _open_in_file_manager(path: str) -> None:
    """Klasoru isletim sisteminin dosya yoneticisinde acar."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                       # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def install_crash_guard() -> None:
    """Yakalanmamis istisnalari kullaniciya gosterir; sureci OLDURMEZ.

    PyQt6, bir slot govdesinden sizan Python istisnasinda ``qFatal`` cagirir:
    surec hicbir sey yazmadan aninda sonlanir ve kaydedilmemis butun calisma
    kaybolur. Ticari kullanimda bu kabul edilemez -- hata bildirilir, kullanici
    calismasini kaydedip cikabilir.
    """
    previous = sys.excepthook

    def hook(kind, value, tb) -> None:
        if issubclass(kind, KeyboardInterrupt):
            previous(kind, value, tb)
            return
        try:
            detail = "".join(traceback.format_exception(kind, value, tb))
        except Exception:                     # pragma: no cover - son care
            detail = "%s: %s" % (getattr(kind, "__name__", kind), value)

        # Konsolsuz (--noconsole) pakette sys.stderr NULL'dur; korumasiz bir
        # yazma burada AttributeError firlatir ve excepthook'un kendisi coker
        # -- yani diyalog tam da en cok gerektigi yerde hic acilmaz.
        stream = sys.stderr
        if stream is not None:
            try:
                stream.write(detail)
                stream.flush()
            except Exception:                 # pragma: no cover - son care
                pass

        # QMessageBox ancak bir QApplication varsa kurulabilir.
        try:
            if QApplication.instance() is None:
                return
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Unexpected error")
            box.setText("An unexpected error occurred; the application stays open.\n"
                        "Saving your work and restarting is recommended.")
            box.setInformativeText("%s: %s" % (kind.__name__, value))
            box.setDetailedText(detail)
            box.exec()
        except Exception:                     # pragma: no cover - son care
            pass

    sys.excepthook = hook


def run(argv: Optional[List[str]] = None) -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(SETTINGS_ORG)
    install_crash_guard()
    app = QApplication.instance() or QApplication([])
    app.setStyle(_QuietMnemonicStyle("Fusion"))
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    window = MainWindow()

    # --- calisma alani: once komut satiri, yoksa acilis diyalogu
    cli_root = _workspace_from_argv(argv if argv is not None else sys.argv[1:])
    if cli_root:
        try:
            window.apply_workspace(Workspace.load(cli_root)
                                   if Workspace.is_workspace(cli_root)
                                   else Workspace.create(cli_root))
        except WorkspaceError as exc:
            QMessageBox.warning(window, "Workspace", str(exc))
    else:
        workspace, init_git = pick_workspace(window.recent_workspaces(),
                                             None, allow_cancel=True)
        if workspace is not None:
            window.apply_workspace(workspace, init_git=init_git)

    window.show()
    return app.exec()


def _workspace_from_argv(argv: List[str]) -> str:
    """``--workspace <yol>`` veya ``--workspace=<yol>`` degerini cikarir."""
    for i, arg in enumerate(argv):
        if arg == "--workspace" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--workspace="):
            return arg.split("=", 1)[1]
    return ""
