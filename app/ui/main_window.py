"""The main window: two diagram modes (state machine + class) and the code panel.

Layout:
  * Top toolbar: file/edit/view/generation
  * Left vertical toolbar: the tools of the active diagram mode (names under icons)
  * Centre: the "State Diagram" / "Class Diagram" tabs
      - State mode: MODEL TREE + PROPERTIES | SIMULATION (above) + canvas + PROBLEMS
      - Class mode: canvas + PROBLEMS
  * Right: the generated code panel (F9 hides/shows it)
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
    """A style that does not underline the access letters.

    Fusion returns 1 UNCONDITIONALLY for the SH_UnderlineShortcut hint; the
    Windows "hide until Alt is pressed" behaviour does not exist in Fusion, so
    "File", "References" and "Help" always appear underlined. Setting the hint
    to 0 removes only THE LINE: Alt+F and friends keep working, because the
    shortcut matching is a separate mechanism.
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

#: The icon size on the top bar. Kept small for a tidy, Astah-like strip;
#: it also sets the height when the three rows are reduced to one.
#: buradan belirlenir.
TOOLBAR_ICON = 16
#: The point size of the menu buttons on the top bar. The rest of the
#: interface is 9 point; inside the icon-mode strip the menus looked smaller still.
MENU_POINT = 10

#: The organisation / application name the settings are written under.
SETTINGS_ORG = "UmlDesignStudio"
SETTINGS_APP = "UmlStateDiagramTool"


def app_settings() -> QSettings:
    """The application settings -- in tests they go to a SEPARATE store.

    WHY: the interface tests build a real MainWindow and `apply_workspace`
    WRITES the workspace into "recent" and "last_workspace". Once those
    settings went into the user's own store, the temporary folder of a test
    (``.../umlui_9z0xku7g/workspace``, say) leaked into the user's list and met
    them at start-up -- even after the folder had been deleted, as an empty
    workspace.

    When `UMLSTUDIO_SETTINGS_SCOPE` is set it is appended to the application
    name, so what the test processes write never touches the user's settings.
    """
    kapsam = os.environ.get("UMLSTUDIO_SETTINGS_SCOPE", "").strip()
    name = "%s-%s" % (SETTINGS_APP, kapsam) if kapsam else SETTINGS_APP
    return QSettings(SETTINGS_ORG, name)
SM_FILTER = "State machine (*.usm);;JSON (*.json);;All files (*)"
CD_FILTER = "Class diagram (*.ucd);;JSON (*.json);;All files (*)"

def _mnemonic(action, text: str) -> None:
    """Writes the menu text of an action together with its ACCESS LETTER (&).

    Toolbar tooltips must not contain `&`; because Qt's `QAction.text()` value
    is used both in the menu and in the tooltip, the tooltip is kept separately.
    """
    tooltip = action.toolTip()
    action.setText(text)
    if tooltip and "&" not in tooltip:
        action.setToolTip(tooltip)


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
    # Fork/join are only meaningful with ORTHOGONAL states; the letters were
    # free in both palettes (see tools/test_standards.py, shortcut uniqueness).
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

#: The class mode tools.
#:
#: THE NAMES ARE NOT ABBREVIATED. The menu used to say "Assoc." / "Aggreg." /
#: "General."; abbreviating a menu item goes against the interface guidelines
#: and is unreadable even to someone who knows the UML term.
#:
#: The access letter (`I`) clashed with "Initial" in STATE mode: seeing two
#: identical shortcuts in one window, Qt says "Ambiguous shortcut" and NEITHER
#: may work. Interface now uses `E`.
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
    """A SEPARATE top-level window carrying the diagram page.

    While the user edits the model here, the MAIN window stays free: they can
    look at the generated code, at another model, or at the repository tab.

    WHEN THE WINDOW IS CLOSED the page goes back to the main window; otherwise
    the page would be nowhere and the user would think they had lost the model.
    """

    def __init__(self, parent, on_close) -> None:
        super().__init__(parent)
        # A parent IS GIVEN (so it closes with the application) but without the
        # Window flag the child would stay embedded like a panel.
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
        # Minimum window: the minimums of the left column, the diagram and the code
        # panel have to fit together, or Qt clips the rightmost pane.
        self.setMinimumSize(QSize(1240, 700))

        # NO SAMPLE MODEL IS LOADED AT START-UP.
        #
        # The previous version opened both modes with a ready demo model (Blinky /
        # RoomPlan). The result misled the user: with NO model in the workspace --
        # while it even said "No models here yet" -- a nine-state diagram sat on the
        # canvas and the properties panel said "Blinky". The user took it for part of
        # their own project, and when they deleted it, it came back.
        # sildiginde de geri geliyordu.
        #
        # The samples are opened from the menu: File > Sample State Machine / Sample
        # Class Diagram (see a_demo, a_demo_class).
        self.doc = Document(empty_machine(), self, default_name="untitled.usm")
        self.class_doc = Document(empty_class_model(), self,
                                  default_name="untitled.ucd")
        self.settings = app_settings()
        #: The files last GENERATED for the ACTIVE mode (export / write use this).
        #: It goes stale when the model changes but is NOT DELETED: the user must be
        #: able to inspect the old code before building.
        self._last_files: Dict[str, str] = {}
        #: Mode -> the files last generated for that mode. Switching tabs DOES NOT
        #: TRIGGER A REBUILD; the cache is shown. Otherwise going back and forth
        #: between the State and Class tabs would start a generation plus a disk
        #: write every time.
        self._built: Dict[str, Dict[str, str]] = {}
        #: Staleness PER MODE. A single flag would be wrong: editing the state
        #: machine does not make the class diagram code stale, and vice versa.
        #: tersi de gecerlidir.
        self._stale_modes = set()
        #: build() is not re-entrant (because the progress bar calls processEvents,
        #: the user can press Build again).
        self._building = False
        self._last_export_dir = ""
        self.workspace: Optional[Workspace] = None
        #: The section title strips to repaint on a theme change.
        self._section_headers: List[QLabel] = []
        #: Action -> icon factory. The icons are drawn with the theme colours AT
        #: SET-UP; on a theme change they have to be regenerated, or a white icon
        #: becomes invisible on a light background.
        self._action_icons = {}
        #: Diagram pages moved into a SEPARATE window: mode -> QMainWindow.
        self._detached = {}
        #: The placeholder labels left in the tab while a page is detached (theme).
        self._placeholders = []
        #: The state of the panels (code, simulation) when ENTERING the repository tab.
        #: The preference is restored on leaving; None = not in repository mode.
        self._git_panel_state = None

        self._build_ui()
        self._build_actions()
        self._build_menu()
        self._build_toolbars()
        self._connect()

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._rebuild_views)

        #: Holds the progress bar at 100% for a moment, then hides it.
        self._progress_hide = QTimer(self)
        self._progress_hide.setSingleShot(True)
        self._progress_hide.timeout.connect(
            lambda: self.progress.setVisible(False))

        self._restore_state()
        self._sync_mode()
        self._rebuild_views()
        # IT BUILDS ONCE AT START-UP: the user does not expect the code panel to be
        # empty when they open the application. After that it is up to Build.
        QTimer.singleShot(0, self.build)
        QTimer.singleShot(60, self.canvas.zoom_fit)
        QTimer.singleShot(60, self.class_canvas.zoom_fit)

    # set-up

    def _build_ui(self) -> None:
        # -- state machine mode
        self.canvas = DiagramCanvas(self.doc, self)
        self.inspector = Inspector(self.doc, self)
        self.problems = ProblemsPanel(self)
        self.sim_panel = SimulatorPanel(self.doc, self)

        # THE WORKSPACE TREE. A workspace holds more than one model (a system plus
        # its subsystems); because the previous panel showed only the OPEN one, the
        # others were nowhere to be seen in the interface.
        self.ws_tree = WorkspaceTree(self)

        # THE MODEL TREE PANEL WAS REMOVED.
        #
        # The WORKSPACE tree already showed the same information -- and not just the
        # open model but EVERY model in the workspace. With the two trees side by
        # side the user saw the same content twice and the left column wasted space.
        # goruyor, sol sutun geregisiz yer kapliyordu.
        #
        # The left column is NOT INSIDE the mode tabs but BESIDE them: with the model
        # tree gone, the workspace tree is the only navigation aid, and losing it on
        # a switch to the class diagram would leave the user without models.
        # PROPERTIES is only meaningful in state mode (class properties are edited
        # from the double-click dialog), so it is hidden by mode.
        self._ws_section = self._titled("WORKSPACE", self.ws_tree)
        self._props_section = self._titled("PROPERTIES", self.inspector)
        self.left_col = QSplitter(Qt.Orientation.Vertical)
        self.left_col.addWidget(self._ws_section)
        self.left_col.addWidget(self._props_section)
        # The properties form wants MORE vertical room than the workspace tree: the
        # tree is typically a few rows, the form is always full.
        self.left_col.setSizes([330, 560])
        # A minimum width for the PROPERTIES FORM. At 230 px the "Extra includes"
        # label left about 90 px for the field and the content was clipped; the user
        # could not see the #include line they had typed.
        self.left_col.setMinimumWidth(300)

        center_col = QSplitter(Qt.Orientation.Vertical)
        center_col.addWidget(self.sim_panel)
        center_col.addWidget(self.canvas)
        center_col.addWidget(self._titled("PROBLEMS", self.problems))
        # THE CANVAS TAKES THE LARGEST SHARE. Simulation and Problems are
        # information strips; the canvas is the workspace. In the old split
        # (190/560/150) the two of them together took half as much room as the
        # canvas.
        center_col.setSizes([150, 720, 120])
        center_col.setStretchFactor(1, 1)
        self.state_center = center_col

        state_page = center_col

        # -- class diagram mode
        self.class_canvas = ClassCanvas(self.class_doc, self)
        self.class_problems = ProblemsPanel(self)

        class_page = QSplitter(Qt.Orientation.Vertical)
        class_page.addWidget(self.class_canvas)
        class_page.addWidget(self._titled("PROBLEMS", self.class_problems))
        class_page.setSizes([720, 150])
        class_page.setStretchFactor(0, 1)

        # -- repository mode
        self.git_panel = GitPanel(self)

        # -- mode tabs
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setDocumentMode(True)
        # THE DIAGRAM PAGES ARE WRAPPED IN A STACK.
        #
        # The user can move the design window into a SEPARATE window; while that is
        # so, they must still be able to look at the generated code, at another model
        # or at the repository in the main window. REMOVING the page from the tab was
        # not an option: active_mode() looks at the tab INDEX (0=state, 1=class,
        # 2=git) and removing a tab would shift the whole mapping. Instead the tab
        # stays in place and ITS CONTENT is swapped for a placeholder.
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
        # A PANE CANNOT BE DESTROYED BY DRAGGING THE SPLITTER ALL THE WAY.
        #
        # By default QSplitter allows a pane to be reduced to 0 pixels. Once the
        # diagram pane hits 0 there is nothing visible left to grab: the user cannot
        # bring the canvas back, and because the setting is saved the problem repeats
        # at every start-up. A minimum width guarantees every pane stays on screen;
        # the panels can still be hidden COMPLETELY with F9 / F10 (hiding is a
        # different path).
        self.main_splitter.setChildrenCollapsible(False)
        self.mode_tabs.setMinimumWidth(320)
        # ...BUT THE ONLY PANE THAT NEEDS PROTECTING IS THE DIAGRAM PANE.
        #
        # With the ban placed on all three panes the code panel could no longer be
        # closed: the user dragged the right splitter all the way and the panel
        # stayed put. Yet the code panel disappearing is not dangerous -- F9 and the
        # View menu bring it back. In the diagram pane there was nothing left to
        # grab at all (see the note above).
        self.main_splitter.setCollapsible(
            self.main_splitter.indexOf(self.code_panel), True)
        # The diagram pane is protected EXPLICITLY. setChildrenCollapsible(False)
        # alone is enough, but isCollapsible() DOES NOT REFLECT it (Qt only returns
        # the per-widget flag and its default looks like "True"); writing the intent
        # out here makes it both readable and testable.
        self.main_splitter.setCollapsible(
            self.main_splitter.indexOf(self.mode_tabs), False)
        # Closing by dragging and closing from the menu must end in the SAME place:
        # otherwise "Show Code Panel" stays ticked while the panel is off screen and
        # the button looks broken.
        self.main_splitter.splitterMoved.connect(self._sync_code_panel_action)
        # The minimum width of the code panel comes from ITS OWN header strip (see
        # CodePanel._fit_header); a fixed number here would override it and the strip
        # would be clipped again.
        self.setCentralWidget(self.main_splitter)

        # status bar
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

        # The build progress bar -- AT THE FAR RIGHT of the status bar.
        # addPermanentWidget places from right to left, so what is added LAST stays
        # furthest right.
        self.progress = QProgressBar()
        self.progress.setFixedWidth(150)
        self.progress.setFixedHeight(14)
        # No percentage TEXT: one colour cannot be read on both the filled and the
        # empty part (see the QProgressBar note in theme.py). The step name is
        # written on the left of the status bar.
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)

        bar = self.statusBar()
        bar.addWidget(self.lbl_message, 1)
        #: The number of external symbols the user has to write THEMSELVES.
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
        """HIDES the code and simulation panels on the repository tab.

        Both are FOR THE DIAGRAM: the code panel shows the generated source and
        the simulation strip the active state. Both are meaningless on the
        repository tab while taking half the screen -- the commit graph and the
        diff viewer would be squeezed into a narrow strip.

        The user's PREFERENCE is kept: on leaving the tab the panels return to
        the state they were in before.
        """
        if girildi:
            if self._git_panel_state is not None:
                return                      # we are already in repository mode
            # THE RIGHT SOURCE FOR EACH PANEL:
            #
            # * The code panel sits OUTSIDE the tabs and its visibility is reliable
            #     -- except that, dragged all the way, it can be "visible" and 0 pixels
            #     wide; code_panel_open() takes that into account.
            # * The simulation strip is INSIDE the tab. This function runs AFTER the
            #     tab has CHANGED, so with the repository tab active sim_panel.isVisible()
            #     is always False; looking at visibility would always record the panel as
            #     closed and never bring it back. The right source is the tick of the
            #     action.
            self._git_panel_state = (self.code_panel_open(),
                                     self.a_sim_panel.isChecked())
            for action, panel in ((self.a_code_panel, self.code_panel),
                                 (self.a_sim_panel, self.sim_panel)):
                if action.isChecked():
                    action.blockSignals(True)
                    action.setChecked(False)
                    action.blockSignals(False)
                panel.setVisible(False)
            return

        previous = self._git_panel_state
        self._git_panel_state = None
        if previous is None:
            return
        kod_acik, sim_acik = previous
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
        """Wraps the page in a stack so a placeholder can take its place."""
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
        """Moves the active diagram page into a SEPARATE window / brings it back."""
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
        # setCentralWidget TAKES the page out of the stack (it reparents it); to put
        # it back, a SEPARATE reference is kept -- the stack index cannot be trusted,
        # as the placeholder shifts to index 0 once the page has left.
        win.setCentralWidget(page)
        # SHOW THE PAGE EXPLICITLY -- but ONLY the page.
        #
        # setParent() (called from inside setCentralWidget) marks the widget HIDDEN,
        # and that mark IS NOT CLEARED when the parent is shown: the separate window
        # opened completely empty.
        #
        # THE CHILD WIDGETS ARE NOT SHOWN WHOLESALE. The first fix read
        # `for child in page.findChildren(QWidget): child.show()`, and that also
        # opened widgets that WERE MEANT TO STAY HIDDEN -- above all the QRubberBand
        # of the canvas. An invisible selection overlay spread over the canvas, mouse
        # clicks went to it, and to the user it felt as if "the mouse was stuck". Qt
        # only marks the REPARENTED widget as hidden; the children keep their own
        # state and become visible when the parent is shown.
        # 
        page.show()
        win.resize(1100, 780)
        self._detached[mode] = (win, page)
        stack.setCurrentWidget(stack.widget(stack.count() - 1))
        win.show()
        win.raise_()
        # A FULL REPAINT. The back buffer of the reparented widget still carried the
        # old content and a patch from the previous layout stayed in one corner of
        # the window.
        page.update()
        self.active_canvas().viewport().update()
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Design window opened — the main window is free now.")

    def _attach_page(self, mode: str) -> None:
        record = self._detached.pop(mode, None)
        if record is None:
            return
        win, page = record
        stack = self._state_stack if mode == "state" else self._class_stack
        win.on_close = None                 # break the callback LOOP
        page.setParent(None)
        stack.insertWidget(0, page)
        stack.setCurrentIndex(0)
        # As with detaching: setParent() leaves the hidden mark behind.
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
        # The title strips are repainted on a theme change (see set_theme).
        self._section_headers.append(header)
        layout.addWidget(header)

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(widget)
        layout.addWidget(frame, 1)
        return host

    # modes

    def active_mode(self) -> str:
        index = self.mode_tabs.currentIndex()
        if index == 1:
            return "class"
        if index == 2:
            return "git"
        return "state"

    def active_doc(self) -> Document:
        """The active document; on the repository tab it falls back to the state one."""
        return self.class_doc if self.active_mode() == "class" else self.doc

    def active_canvas(self):
        return self.class_canvas if self.active_mode() == "class" else self.canvas

    def _sync_mode(self) -> None:
        """Syncs the toolbars and the shortcuts when the tab changes."""
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
        # The canvas IS NOT VISIBLE on the repository tab. If the shortcuts that act
        # on it stayed enabled there (Ctrl+A, Del), the user could delete the whole
        # diagram without seeing a thing; so they are disabled outside their mode.
        # Undo/Redo BELONG on that list too. The canvas is invisible on the
        # repository tab; pressing Ctrl+Z there silently changed a diagram the user
        # COULD NOT SEE -- with no way of telling what had happened until they
        # looked at it.
        for a in (self.a_undo, self.a_redo, self.a_select_all,
                  self.a_zoom_in, self.a_zoom_out,
                  self.a_zoom_reset, self.a_zoom_fit):
            a.setEnabled(diagram_mode)
        # The CODE and SIMULATION panels CANNOT BE OPENED in repository mode.
        #
        # Closing them on entering the tab was not enough: on the repository screen
        # the user could press F9/F10 and open both again, and the diff viewer was
        # squeezed into a narrow strip once more. The actions are disabled outside
        # their mode; they are re-enabled on leaving the tab.
        self.a_code_panel.setEnabled(diagram_mode)
        self.a_sim_panel.setEnabled(state_mode)
        if not state_mode:
            # The state canvas is hidden on a switch to the class diagram; the
            # simulation highlight must not stay hanging there.
            self._clear_simulation()
        self.a_delete.setEnabled(
            diagram_mode and bool(self.active_canvas().selected_ids()))
        self.set_tool(Tool.SELECT)
        self.set_class_tool(ClassTool.SELECT)
        self._update_counts()

        # PROPERTIES is only meaningful in state mode: class properties are edited
        # from the double-click dialog, and repository mode has no model. The
        # WORKSPACE tree is visible in EVERY mode -- it is the only navigation aid.
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
        # FIT the diagram to the window when the mode changes. Zooming/panning done
        # while the canvas was hidden shifts the view; the user should not have to
        # hunt for the model when they come back to the tab.
        QTimer.singleShot(0, self.active_canvas().zoom_fit)
        # CHANGING TABS TRIGGERS NO GENERATION. Instead the last build output of
        # that mode is shown from the cache; when it has not been built yet the
        # panel says so. So going back and forth between State and Class causes no
        # disk write, and the panel never shows the code of the OTHER mode --
        # otherwise Ctrl+E could export the wrong files.
        self._show_mode_build()
        self._validate_active()

    # actions

    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, icon=None, type_name=None, checkable=False):
            a = QAction(text, self)
            if icon:
                a.setIcon(icon())
                # Keep the factory: the icon is redrawn on a theme change.
                self._action_icons[a] = icon
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if type_name:
                a.setToolTip(type_name)
                a.setStatusTip(type_name)
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
                               type_name="Choose the folder that holds the model and the generated code")
        self.a_workspace_open = act("Reveal Workspace in File Manager",
                                    self.reveal_workspace)
        self.a_write_now = act("Write Generated Code to Workspace",
                               self.write_generated_now, "Ctrl+Shift+G")
        self.a_auto_write = act("Write Automatically After Generation",
                                self.toggle_auto_write, checkable=True)
        self.a_auto_write.setChecked(True)
        self.a_git_refresh = act("Refresh Repository", self.refresh_git, "F6",
                                 type_name="Re-read the git status and history")
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

        # FULL SCREEN. F11 is the standard shortcut; Esc leaves it too (see
        # keyPressEvent below). The checked state IS NOT DERIVED from the window
        # state -- the user may leave through the window manager as well -- so it
        # is synced in changeEvent; otherwise the tick in the menu would lie.
        self.a_fullscreen = act("Full Screen", self.toggle_fullscreen, "F11",
                                None, "Switches the window to full screen (F11)",
                                checkable=True)

        # A RECOVERY PATH. A broken layout (a pane dropped to 0 pixels, say) is
        # saved and so repeats at every start-up; going back to the default with
        # one click in the menu beats forcing the user to delete their settings by
        # hand.
        self.a_reset_layout = act("Reset Layout", self.reset_layout, None,
                                  None, "Restores the default panel sizes")

        # THEME. The choice is stored in QSettings and applied at start-up.
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for key, label in THEMES.items():
            # The submenu is already called "Theme"; repeating it in the item is
            # pointless. An access letter is added as well (D / L).
            a = QAction("&%s" % label, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _c, k=key: self.set_theme(k))
            self.theme_group.addAction(a)
            self.theme_actions[key] = a
        self.theme_actions[active_theme()].setChecked(True)

        # SEPARATE ICONS. With an eye icon on both panel switches they could not
        # be told apart side by side on the strip; because the code panel is a
        # strip on the right, it gets the panel icon and simulation the play triangle.
        self.a_code_panel = act("Show Code Panel", self.toggle_code_panel,
                                "F9", icons.panel_icon,
                                "Hide or show the generated-code panel",
                                checkable=True)
        self.a_code_panel.setChecked(True)

        # THE SIMULATION PANEL IS CLOSED AT START-UP.
        #
        # It took about 190 pixels off the top of the canvas, and most users draw
        # the model first and run it afterwards. The preference IS SAVED: once
        # opened, it comes back open at the next start-up.
        self.a_sim_panel = act("Show Simulation", self.toggle_sim_panel,
                               "F10", icons.play_icon,
                               "Hide or show the simulation strip",
                               checkable=True)
        self.a_sim_panel.setChecked(False)
        self.sim_panel.setVisible(False)

        # BUILD is the ONLY trigger of code generation. Code is no longer generated
        # by itself when the model changes (see _on_model_changed).
        self.a_build = act("Build", self.build, "F5", icons.build_icon,
                           "Validate the model and generate the code "
                           "(F5) — nothing is generated until you ask")
        # DETACHING the design window. While the user edits the model in a separate
        # window they can look at the generated code / another model / the
        # repository in the main window.
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
                          type_name="Open the OMG UML 2.5.1 specification "
                              "in a separate window")
        self.a_about = act("About", self.show_about)
        self.a_shortcuts = act("Shortcuts", self.show_shortcuts, "F1")
        self.a_license = act("License (GPL v3)", self.show_license)

        # state mode tools
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: Dict[Tool, QAction] = {}
        for tool, text, key, icon, type_name in STATE_TOOLS:
            a = QAction(icon(), text, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(type_name)
            a.setStatusTip(type_name)
            a.triggered.connect(lambda _c, t=tool: self.set_tool(t))
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
            self._action_icons[a] = icon
        self.tool_actions[Tool.SELECT].setChecked(True)

        # class mode tools
        self.class_tool_group = QActionGroup(self)
        self.class_tool_group.setExclusive(True)
        self.class_tool_actions: Dict[ClassTool, QAction] = {}
        for tool, text, key, icon, type_name in CLASS_TOOLS:
            a = QAction(icon(), text, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(type_name)
            a.setStatusTip(type_name)
            a.triggered.connect(lambda _c, t=tool: self.set_class_tool(t))
            self.class_tool_group.addAction(a)
            self.class_tool_actions[tool] = a
            self._action_icons[a] = icon
        self.class_tool_actions[ClassTool.SELECT].setChecked(True)

    def _build_menu(self) -> None:
        """Builds the menu bar in the STANDARD layout.

        The rules followed (the common layout of Windows/CUA and Qt apps):

        * Order: File, Edit, View, ... domain menus ..., Help. Repository used
          to sit BETWEEN File and Edit; the File-Edit adjacency is fixed in
          every desktop guideline.
        * EVERY item has a UNIQUE access letter (&) within its menu; that is
          the only way to walk the menu from the keyboard.
        * Commands that open a dialog end with "..."; commands that act
          directly or only show information do not (About, Shortcuts).
        * Section headings are placed with `addSection()`. A "disabled
          QAction" was used before; screen readers announced it as a
          "disabled menu item".
        """
        m = self.menuBar()
        #: The menus moved onto the single-row top bar (see _build_toolbars).
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
        # The VIEW tools sit under Edit as well. An explicit request from the
        # user: "put the tools like undo, redo and fit under edit too". The same
        # action appearing in two menus is common in desktop applications; its
        # shortcut is single.
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
        theme = v.addMenu("&Theme")
        theme.addActions(list(self.theme_actions.values()))

        # -- Tool ---------------------------------------------------------- #
        #
        # On the top strip the tools appear as ICONS only; the only way to learn
        # which icon is which tool was to wait for the tooltip. The menu lists the
        # tools BY NAME and BY SHORTCUT.
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
        # THE LANGUAGE LABELS COME FROM ONE SOURCE (code_panel.LANGUAGES). Written
        # by hand, the labels of the menu and of the right panel drifted apart. The
        # access letters are added here; the text itself is unchanged.
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
        # The specification ITSELF: it makes the references followable.
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
        """A SINGLE-ROW TOP BAR.

        There used to be three separate rows: the menu bar, the main actions and
        the diagram tools -- plus the mode tabs on top of that. Four strips took
        about 120 pixels off the canvas. The user: "remove the 2nd and 3rd top
        bars too, put them in the topmost bar, give me room."
        alan ac."

        Now they are all in ONE QToolBar: the menus on the left (as pop-up
        buttons), then the main actions, then the tools of the active mode. The
        QMenuBar object STAYS but hidden: the menus stay attached to it, and
        shortcuts and code walking `menuBar().actions()` do not break.

        In a narrow window QToolBar shows its own overflow button; nothing
        becomes unreachable.
        """
        bar = QToolBar("Main")
        bar.setObjectName("topBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(TOOLBAR_ICON, TOOLBAR_ICON))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.top_bar = bar

        # -- menus ------------------------------------------------------
        # THE MENU TEXT IS ENLARGED. The user said "make the menu bar a bit bigger,
        # it has got too small": once the strip went into icon mode, the menu
        # buttons dropped to the same small size as the tool icons.
        menu_font = ui_font(MENU_POINT)
        self.menu_buttons: List[QToolButton] = []
        for menu in self._menus:
            btn = QToolButton(bar)
            # THE ACCESS LETTER (&) IS PRESERVED.
            #
            # The real menu bar is hidden and these buttons take its place. The `&`
            # used to be stripped, so menu shortcuts such as Alt+F / Alt+E DID NOT WORK
            # AT ALL -- the only keyboard route into the menu was closed.
            # QAbstractButton sets up an Alt shortcut from the `&` in the text by
            # itself and opens this menu on an InstantPopup button.
            # 
            btn.setText(menu.title())
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.setAutoRaise(True)
            btn.setFont(menu_font)
            # The text of the opened menu must be at the same size; otherwise the
            # button grows and its content stays small.
            menu.setFont(menu_font)
            bar.addWidget(btn)
            self.menu_buttons.append(btn)
        self.menuBar().setVisible(False)

        # THE MAIN ACTIONS: the FREQUENTLY USED ones only.
        #
        # One row is limited: 7 menus + 11 actions + 10 tools do not fit on a 1500
        # px screen and half the tools ended up behind the overflow button -- while
        # the THING IN CONSTANT USE is the TOOLS. Zoom, grid, the panel switches and
        # export left the strip; they all live in the View / Code menus and in their
        # shortcuts.
        bar.addSeparator()
        bar.addActions([self.a_new, self.a_open, self.a_save])
        bar.addSeparator()
        bar.addActions([self.a_undo, self.a_redo])
        bar.addSeparator()
        bar.addActions([self.a_zoom_fit, self.a_build])
        bar.addSeparator()
        # The panel switches stay ON THE STRIP. A menu entry plus a shortcut is not
        # discoverable on its own: once the user closed the code panel there was no
        # visible way to bring it back.
        bar.addAction(self.a_code_panel)
        bar.addAction(self.a_sim_panel)

        # -- diagram tools: they change with the ACTIVE mode -------------------
        # The tools carry their names: the answer to "which tool did I pick" could
        # not be read off an icon.
        self._tool_sep = bar.addSeparator()

        def tool_actions_of(spec, actions):
            out = []
            for tool, _t, _k, _i, _type_name in spec:
                out.append(actions[tool])
            return out

        self._state_tool_actions = tool_actions_of(STATE_TOOLS,
                                                   self.tool_actions)
        self._class_tool_actions = tool_actions_of(CLASS_TOOLS,
                                                   self.class_tool_actions)
        for act in self._state_tool_actions + self._class_tool_actions:
            bar.addAction(act)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        # Filter by mode at start-up too: `_sync_mode` is not called until the
        # first tab change, and the tools of both modes were showing together
        # (two separate 'Select' buttons).
        self._show_tools_for(self.active_mode())
        self._sync_tool_labels()

    def _sync_tool_labels(self) -> None:
        """Shows every tool AS AN ICON.

        In the previous version the SELECTED tool was also written out (for the
        "let me see the tool I picked" request). The result looked bad: the
        strip widened and narrowed on every tool change, the buttons next to it
        shifted, and a single "Select" label sat among the tools like a patch.
        The selected tool is already obvious from its PRESSED look; which tool
        it is is written in the tooltip, in the Tool menu and in the status bar.
        """
        for act in self._state_tool_actions + self._class_tool_actions:
            button = self.top_bar.widgetForAction(act)
            if button is None:
                continue
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _show_tools_for(self, mode: str) -> None:
        """Leaves ONLY the tools of the active mode on the top bar.

        Visibility is set through the ACTION, not through the WIDGET: when
        QToolBar rebuilds its layout it shows the button of the action again, so
        `widgetForAction(...).setVisible(False)` did not hold -- the tools of
        both modes sat on top of each other and two separate "Select" buttons
        appeared on the strip.
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
        # The element chosen in the tree is focused on the ACTIVE canvas. It used
        # to be bound to the state canvas only; on a class diagram, clicking a
        # class in the tree did nothing.
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
        # Fit the diagram when the simulation starts: the active state chain is
        # highlighted and the user should not have to zoom by hand first.
        self.sim_panel.started.connect(
            lambda: QTimer.singleShot(0, self.canvas.zoom_fit))

        self.git_panel.status_changed.connect(self._on_git_status)
        # When a MODEL file is selected in the repository panel, also mark the diff
        # ON THE DIAGRAM.
        self.git_panel.model_diff_requested.connect(self._git_model_diff)

        self.mode_tabs.currentChanged.connect(lambda _i: self._sync_mode())

    # flow

    def _on_model_changed(self) -> None:
        # Refresh on the next loop so no item is deleted during event handling.
        self._rebuild_timer.start(0)
        # CODE GENERATION DOES NOT RUN HERE. All the code used to be regenerated,
        # highlighted and WRITTEN TO DISK 180 ms after every change; every drag or
        # edit gesture started a file write round and the interface stuttered (on a
        # machine with a virus scanner the write time is unpredictable).
        # Generation now runs only when the user says Build.
        self._mark_code_stale()

    def _rebuild_views(self) -> None:
        self.canvas.rebuild()
        self.class_canvas.rebuild()
        # OPEN model nodes in the tree have to show unsaved changes too; closed
        # nodes are left alone (lazy loading).
        self.ws_tree.set_open_models(self.open_models())
        self.inspector.refresh()
        self.sim_panel.rebuild()
        self._update_title()
        self._update_counts()
        # VALIDATION stays live (~0.2-1.3 ms): the Problems panel, the error marks
        # on the canvas and the status bar run off it, and the user has to see an
        # error AT ONCE. The expensive step that touches the disk is CODE
        # GENERATION; that moved onto the Build button.
        self._validate_active()

    def _git_model_diff(self, path: str, taban: str) -> None:
        """Shows the diff of the model selected in the repository panel ON THE DIAGRAM.

        A textual diff is unreadable on a JSON model: moving one box produces
        dozens of lines. The answer to the "what was added, what was removed"
        question the user is asking is the picture itself.

        The diff can only be drawn when the file is OPEN ON THE CANVAS;
        otherwise it is said in the status bar. The comparison base is the side
        the repository panel picked: the index for a staged file, else HEAD.
        """
        try:
            acik = None
            target = os.path.normcase(os.path.abspath(path))
            for doc in (self.doc, self.class_doc):
                if doc.path and os.path.normcase(
                        os.path.abspath(doc.path)) == target:
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
            old = (repo.staged_text(goreli) if taban == "staged"
                    else repo.file_at("HEAD", goreli))
            if not old:
                return
            marks = element_status(old, acik.machine.to_json())
            view = (self.class_canvas if acik is self.class_doc
                     else self.canvas)
            other = (self.canvas if view is self.class_canvas
                     else self.class_canvas)
            other.set_diff_marks(None)
            view.set_diff_marks(marks)
            self.flash("%s — %d added, %d removed, %d changed (vs %s)."
                       % (os.path.basename(path), len(marks["added"]),
                          len(marks["removed"]), len(marks["changed"]),
                          "staged" if taban == "staged" else "HEAD"))
        except Exception as exc:                   # noqa: BLE001
            self.flash_error("Could not compare '%s': %s"
                             % (os.path.basename(path), exc))
            traceback.print_exc()

    def _model_selected(self, path: str) -> None:
        """The SAFE entry point of the `model_selected` signal.

        In PyQt6 an uncaught Python exception in a slot KILLS the process
        (0xC0000409). That happened exactly once: the class canvas had no
        `set_diff_marks`, clicking a model in the tree threw AttributeError and
        the application closed -- taking the unsaved model with it.

        SHOWING a diff is a convenience; its failure must not cost the user
        their work. The problem is not swallowed silently: it is reported in
        the status bar and written to stderr.
        """
        try:
            self._show_model_diff(path)
        except Exception as exc:                   # noqa: BLE001 - deliberate
            try:
                self.canvas.set_diff_marks(None)
                self.class_canvas.set_diff_marks(None)
            except Exception:                      # noqa: BLE001
                pass
            self.flash_error("Could not compare '%s': %s"
                       % (os.path.basename(path), exc))
            traceback.print_exc()

    def _show_model_diff(self, path: str) -> None:
        """Shows the CHANGES of the model selected in the tree on the diagram.

        What the user is asking is "what changed in this file", and they want
        the answer on the DIAGRAM rather than in a textual diff: added blocks
        green, changed ones yellow, DELETED ones dashed red ghosts in place.

        The comparison base is the widest meaningful base:

          * when a HEAD version EXISTS in the repository that is the base -- so
            both uncommitted and unsaved changes appear IN ONE PICTURE;
          * without a repository (or when the file was never committed) the
            base is the version on disk, so only "what I have not saved" shows.

        The diff can only be drawn when the file is OPEN ON THE CANVAS: painting
        another model's diff over the diagram on screen would be misleading.
        """
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                diskteki = fh.read()
        except OSError:
            return

        acik_doc = None
        target = os.path.normcase(os.path.abspath(path))
        for doc in (self.doc, self.class_doc):
            if doc.path and os.path.normcase(os.path.abspath(doc.path)) == target:
                acik_doc = doc
                break

        if acik_doc is None:
            # The diagram is not on screen; there is nothing to paint.
            self.canvas.set_diff_marks(None)
            self.class_canvas.set_diff_marks(None)
            self.flash("%s — open it (double-click) to compare."
                       % os.path.basename(path))
            return

        new = acik_doc.machine.to_json()
        old = self._head_version(path)
        if old is not None:
            # THE TYPO WAS HERE: with HEAD as the base the marks were computed but
            # `None` was sent to the canvas; that is why the "what I have not
            # committed" view WAS NEVER DRAWN.
            source = ("uncommitted changes" if new == diskteki
                      else "uncommitted + unsaved changes")
        else:
            old, source = diskteki, "unsaved changes"

        marks = element_status(old, new)
        # A diff is only meaningful in the mode of that model.
        view = (self.class_canvas if acik_doc is self.class_doc
                 else self.canvas)
        other = self.canvas if view is self.class_canvas else self.class_canvas
        other.set_diff_marks(None)
        view.set_diff_marks(marks)
        total = (len(marks["added"]) + len(marks["removed"])
                  + len(marks["changed"]))
        if total == 0:
            self.flash("%s — no changes (%s)."
                       % (os.path.basename(path), source))
        else:
            self.flash("%s — %d added, %d removed, %d changed (%s)."
                       % (os.path.basename(path), len(marks["added"]),
                          len(marks["removed"]), len(marks["changed"]),
                          source))

    def _head_version(self, path: str):
        """The content of the file at git HEAD; None without a repository/file.

        `Repo.file_at` wants a path RELATIVE TO THE REPOSITORY ROOT; given an
        absolute path, git does not find the file and returns empty.
        """
        panel = getattr(self, "git_panel", None)
        repo = getattr(panel, "repo", None) if panel is not None else None
        if repo is None:
            return None
        try:
            goreli = os.path.relpath(path, repo.root).replace(os.sep, "/")
            if goreli.startswith(".."):
                return None            # outside the repository
            text = repo.file_at("HEAD", goreli)
            return text or None
        except Exception:
            # The repository may be empty (no HEAD) or the file may never have been
            # committed; neither is AN ERROR, both mean "no diff".
            return None

    def _focus_element(self, eid: str) -> None:
        """Focuses the element selected in the tree ON THE ACTIVE CANVAS.

        It also switches to the mode the element belongs to: staying on the
        state diagram tab when the user clicks a class in the tree made no sense.
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
            # The same selection was emitted again (the canvas was redrawn). DO NOT
            # tear the form down: the user may be typing in a field; thanks to its
            # focus guard, refresh() only refreshes when that is safe.
            self.inspector.refresh()
        else:
            self.inspector.show_selection(ids)
        if len(ids) == 1:
            # The selection is marked in the WORKSPACE tree (the model tree was
            # removed; that tree already carries the same information).
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
        # Changing the language is an explicit user request ABOUT THE CODE; a panel
        # showing the code of another language would be misleading.
        self.build()

    def submachine_resolver(self):
        """The function that resolves submachine references.

        THE OPEN DOCUMENT WINS: if the user changed the referenced machine in
        another tab and has not saved it yet, generating code from the old copy
        on disk would disagree with the diagram ON SCREEN.
        """
        from ..core.submachine import workspace_resolver

        acik = {}
        path = getattr(self.doc, "path", "") or ""
        if path and self.workspace is not None:
            try:
                acik[self.workspace.relative(path)] = self.doc.machine
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
            return          # the canvas is not visible; do not change what cannot be seen
        self.active_doc().undo_stack.undo()

    def _redo(self) -> None:
        if self.active_mode() == "git":
            return          # the canvas is not visible; do not change what cannot be seen
        self.active_doc().undo_stack.redo()

    def _focused_text(self):
        """Returns the focused TEXT field in this window, if there is one.

        The code panel is a READ-ONLY editor, and in Qt a read-only text field
        DOES NOT ACCEPT the `ShortcutOverride` event: a window-scoped QAction
        shortcut beats it. An editable field wins, a read-only one loses -- that
        is exactly the difference.

        The consequence was silent DATA LOSS: to copy the generated code the
        user clicked the panel and pressed Ctrl+A, the code was not selected;
        instead everything on the canvas they COULD NOT SEE got selected and Del
        deleted the whole model (9 states, 11 transitions -> 0). The delete
        action is ENABLED by Ctrl+A itself, so it is a two-key gesture.
        """
        from PyQt6.QtWidgets import (QApplication, QLineEdit,
                                     QPlainTextEdit, QTextEdit)
        odak = QApplication.focusWidget()
        if isinstance(odak, (QLineEdit, QPlainTextEdit, QTextEdit)) \
                and self.isAncestorOf(odak):
            return odak
        return None

    def _canvas_focused(self) -> bool:
        """Is the focus inside THE ACTIVE CANVAS (or nowhere)?

        The actions that change the canvas only run then. A call from the menu
        or the toolbar does not move the focus (the buttons are Qt::NoFocus), so
        this condition does not block them.
        """
        from PyQt6.QtWidgets import QApplication
        odak = QApplication.focusWidget()
        if odak is None:
            return True
        canvas = self.active_canvas()
        return odak is canvas or canvas.isAncestorOf(odak)

    def _delete_selection(self) -> None:
        if self.active_mode() == "git":
            return          # the canvas is not visible; do not delete what cannot be seen
        if not self._canvas_focused():
            # The focus is in another panel: the code editor, the workspace tree, a
            # property field... Del belongs there, not to the canvas.
            return
        self.active_canvas().delete_selection()

    # generation

    # -- 1) VALIDATION: cheap, runs on every model change ------------------- #

    def _validate_active(self) -> List[Issue]:
        """Validates the model of the active mode and updates the indicators.

        It GENERATES no code and DOES NOT TOUCH the disk. So drag/edit gestures
        start no file write, while the user still sees an error at once.
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

    # -- 2) GENERATION: expensive, runs ONLY on Build ----------------------- #

    def _generate_files(self, issues: List[Issue]) -> Optional[Dict[str, str]]:
        """Generates the code of the active mode. On an error returns None and writes to the panel."""
        if has_errors(issues):
            n_err = sum(1 for i in issues if i.is_error)
            # DO NOT LEAVE stale files behind: otherwise Ctrl+E / Ctrl+Shift+G would
            # silently write the previous (or the other mode's) code to disk.
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
            # The state machine generators are given A SUBMACHINE RESOLVER.
            return generator(model, resolve=self.submachine_resolver())
        except CodegenError as exc:
            self._drop_build()
            self.code_panel.show_blocked("Code generation failed: %s" % exc)
            return None
        except Exception as exc:                       # an unexpected condition
            self._drop_build()
            self.code_panel.show_blocked(
                "Unexpected generation error: %s\n%s"
                % (exc, traceback.format_exc(limit=3)))
            return None

    # -- 3) BUILD: on user request, with a progress bar --------------------- #

    #: The build steps: (percentage, the text to show in the status bar).
    BUILD_STEPS = (
        (10, "Validating model…"),
        (45, "Generating %s…"),
        (75, "Formatting source…"),
        (95, "Writing to workspace…"),
        (100, "Build finished."),
    )

    def build(self) -> bool:
        """Validates the model, generates the code, fills the panel, writes to the workspace.

        THE SINGLE ENTRY POINT. It runs when the user says Build (F5), changes
        the language or changes mode; it does NOT run on a model change.

        The steps run in order ON THE MAIN THREAD, NOT on a background one. In
        the measurements generation takes 1-6 ms (see tools/); adding a thread
        would require a deep copy of the model and create the risk of a silent
        inconsistency between the copy and the live model -- unacceptable when
        the generated code is used in critical places. The only unpredictable
        step is WRITING TO DISK; that is where the bar really earns its place.
        """
        if self._building:
            return False                      # block re-entry
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
        """The cache key: (mode, LANGUAGE).

        The language goes into the key too; otherwise, after the user switched to
        C++ and pressed Build, returning to the other tab would STILL show C code
        in the panel with a 'C++' heading, and Ctrl+E would write the wrong files.
        """
        return (self.active_mode(), self.code_panel.current_language())

    def _drop_build(self) -> None:
        """Discards the generated files and THE CACHE for the active mode+language."""
        self._last_files = {}
        self._built.pop(self._build_key(), None)

    def _show_mode_build(self) -> None:
        """Puts the CACHED build output of the active mode into the panel."""
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
        """Marks the mode of the CHANGED MODEL as stale.

        Because the code no longer refreshes by itself, the user has to know
        which model version the source on screen belongs to; otherwise they
        could export old code believing it correct.

        Which DOCUMENT changed is found from the sender of the signal: the user
        can change the state machine while on the class tab (with an undo, say),
        so looking at the ACTIVE mode would be wrong.
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
        # Give the event loop a turn so the bar is REALLY painted. The user asked
        # to "show the build process"; without a repaint between the steps the bar
        # would appear only once, at the end.
        QApplication.processEvents()

    def _end_progress(self, ok: bool) -> None:
        if not ok:
            self.progress.setValue(0)
            self.progress.setVisible(False)
            return
        # On a fast build the bar would last no longer than a blink; it is held at
        # 100% briefly so the user sees the build HAS FINISHED.
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

        # SAY WHAT THE USER HAS TO WRITE.
        #
        # The entry/exit/do/effect/guard bodies in the model are C/C++ texts
        # written by the user; the calls inside them are not generated. Seeing
        # that only at link time as an "undefined reference" is late and obscure.
        # It is documented in the generated headers, but the user should know
        # without opening the file.
        self._report_required(files)

    def _report_required(self, files: Dict[str, str]) -> None:
        """After a build, writes the symbols that have to be supplied externally."""
        try:
            if self.active_mode() != "state":
                self.lbl_external.setText("")
                return
            from ..codegen.ir import build_ir
            needed = build_ir(self.doc.machine).required_functions()
        except Exception:                          # noqa: BLE001
            self.lbl_external.setText("")
            return
        if not needed:
            self.lbl_external.setText("")
            self.lbl_external.setToolTip("")
            return
        line_break = chr(10)
        self.lbl_external.setText("⚙ %d external" % len(needed))
        self.lbl_external.setStyleSheet("color: %s;" % C.WARN)
        self.lbl_external.setToolTip(
            "You must implement these yourself; the generator does not:"
            + line_break
            + line_break.join("  %s      used by: %s"
                              % (r.summary(), ", ".join(r.sites))
                              for r in needed)
            + line_break * 2
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

    #: The SEVERITY of a status bar message -> colour.
    FLASH_COLOR = {"error": C.RED, "warning": C.WARN, "ok": C.GREEN}

    def flash(self, text: str, kind: str = "info") -> None:
        """Writes a message into the status bar, coloured BY SEVERITY.

        Every message used to be in the plain text colour: "Code generation
        failed" and "Code panel shown" looked the same. The user asked for the
        error / warning / critical texts to stand out.
        """
        self.lbl_message.setText(text)
        color = self.FLASH_COLOR.get(kind)
        self.lbl_message.setStyleSheet(
            ("color: %s; font-weight: bold;" % color) if kind == "error"
            else ("color: %s;" % color) if color else "")

    def flash_error(self, text: str) -> None:
        """Reports a failed operation in RED."""
        self.flash(text, "error")

    def flash_warning(self, text: str) -> None:
        """Reports a condition that wants attention in AMBER."""
        self.flash(text, "warning")

    def commit_pending_edits(self) -> None:
        """Commits the field being typed in the PROPERTIES panel to the model.

        The fields write their value into the model only ON FOCUS LOSS. Because
        the save, export and shutdown paths read the model directly, an
        operation performed while the cursor sat in a field would not see the
        text the user had just typed: the old value went into the file and the
        application said 'Saved'. Dropping the focus triggers the commit.
        """
        focus = QApplication.focusWidget()
        if focus is not None and self.inspector.isAncestorOf(focus):
            focus.clearFocus()

    #  workspace

    def recent_workspaces(self) -> List[str]:
        stored = self.settings.value("recent_workspaces", [])
        if isinstance(stored, str):
            stored = [stored]
        return normalise_recent(list(stored or []))

    def set_recent_workspaces(self, paths: List[str]) -> None:
        """Stores the recent list -- used when the user forgets an entry.

        Writing happens AT ONCE, not when the dialog is accepted: forgetting a
        folder is a decision of its own, and pressing Cancel afterwards must
        not bring the entry back at the next start-up.
        """
        self.settings.setValue("recent_workspaces", list(paths))
        self.settings.sync()

    def choose_workspace(self) -> bool:
        """Opens the workspace dialog; applies the workspace if one is chosen."""
        workspace, init_git = pick_workspace(self.recent_workspaces(), self,
                                             allow_cancel=True,
                                             on_forget=self.set_recent_workspaces)
        if workspace is None:
            return False
        self.apply_workspace(workspace, init_git=init_git)
        return True

    def apply_workspace(self, workspace: Workspace,
                        init_git: bool = False) -> None:
        """Adopts the workspace: paths, git panel, recent list."""
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
        """{absolute path: model} -- the OPEN documents.

        The tree must show the version of an open model that is BEING EDITED,
        not the one ON DISK; otherwise unsaved states do not appear there.
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
        """Opens the model double-clicked in the tree, in the right mode."""
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
        # Fit the opened model to the window; the user should not look at an empty
        # canvas at the zoom level of the previous model.
        QTimer.singleShot(40, self.active_canvas().zoom_fit)
        self.flash("Opened: %s" % os.path.basename(path))

    def _on_model_removed(self, path: str) -> None:
        """CLOSES the canvas when a model deleted from the tree IS OPEN.

        The previous version kept the content of the document and cleared only
        the `path` field. The result misled the user: the file left the
        workspace while the diagram stayed on the canvas, and the title still
        said "sm.usm*" -- the name of a file that NO LONGER EXISTS. A model
        that is not in the workspace should not be displayed.

        There is no risk of data loss: deletion always goes through a
        confirmation dialog (see workspace_tree.remove_selected) and the dialog
        says the open model WILL BE CLOSED.
        """
        target = os.path.normcase(os.path.abspath(path))
        for doc, empty in ((self.doc, empty_machine),
                         (self.class_doc, empty_class_model)):
            if not doc.path:
                continue
            if os.path.normcase(os.path.abspath(doc.path)) != target:
                continue
            doc.replace(empty(), None)          # the canvas empties, the path is cleared
        self.refresh_workspace_tree()
        # The generated code belonged to the deleted model too; do not leave it stale.
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
        """Writes the generated files into the workspace (when one is open)."""
        if self.workspace is None or not self.workspace.auto_write or not files:
            return
        self._write_files(files, announce=False)

    def write_generated_now(self) -> None:
        self.commit_pending_edits()
        # CURRENT code has to be written to disk, so it is built first.
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

    #  repository

    def refresh_git(self) -> None:
        self.git_panel.refresh()

    def show_git_tab(self) -> None:
        self.mode_tabs.setCurrentIndex(2)

    def _on_git_status(self, text: str) -> None:
        self.lbl_git.setText(("⎇ " + text) if text else "")

    # file

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
        """File > Examples: a SEPARATE model for every tool.

        The menu text carries the name of the example and the tooltip which
        tools it teaches plus the relevant UML 2.5.1 clause, so the user can
        read from the menu which example to open and why.
        """
        gal = parent_menu.addMenu("&Examples")
        self._menus_examples = gal
        for heading, items, mode in (("&State Machine", STATE_EXAMPLES, "state"),
                                   ("&Class Diagram", CLASS_EXAMPLES, "class")):
            submenu = gal.addMenu(heading)
            for example in items:
                action = QAction(example.title, self)
                tooltip = "%s\n%s\nUML 2.5.1 §%s" % (
                    example.teaches, example.summary, example.reference)
                action.setToolTip(tooltip)
                action.setStatusTip("%s — %s" % (example.teaches, example.summary))
                action.triggered.connect(
                    lambda _c=False, o=example, k=mode: self.load_example(o, k))
                submenu.addAction(action)
            submenu.setToolTipsVisible(True)
        gal.setToolTipsVisible(True)

    def load_example(self, example, mode: str) -> None:
        """Loads an example from the gallery and switches to the right mode."""
        belge = self.class_doc if mode == "class" else self.doc
        if not self._confirm_discard(belge):
            return
        self.mode_tabs.setCurrentIndex(1 if mode == "class" else 0)
        belge.replace(example.build(), None)
        if mode == "state":
            self.set_tool(Tool.SELECT)
        view = self.class_canvas if mode == "class" else self.canvas
        QTimer.singleShot(40, view.zoom_fit)
        self.flash("%s — %s" % (example.title, example.teaches))

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

        # The mode is decided by the CONTENT, not the extension: '.json' can carry
        # either type, and a file loaded into the wrong mode would turn into an empty
        # model and erase the original content on the first save.
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
        """Notes the model file last used in the workspace."""
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
        # A newly saved file MUST APPEAR in the WORKSPACE tree: otherwise, even
        # though the save succeeded, the user believes the model did not go into
        # the workspace.
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

    # export

    def export_code(self) -> None:
        self.commit_pending_edits()
        # The exported code has to MATCH THE MODEL; silently writing the stale
        # output of a previous build is not acceptable.
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

    # other

    def select_all(self) -> None:
        text = self._focused_text()
        if text is not None:
            # When the user pressed Ctrl+A while in a TEXT field they want to select
            # the text. Because Qt cannot do that itself in a read-only code panel
            # (see _focused_text_field), it is done here.
            text.selectAll()
            return
        if self.active_mode() == "git":
            return          # the canvas is not visible
        if not self._canvas_focused():
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
        """F11 / menu: goes full screen and returns to the PREVIOUS state on exit.

        Calling `showNormal()` would be wrong: if the window was MAXIMISED
        before going full screen, the user would get it back un-maximised.
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
        """Syncs the tick in the menu when the window state changes FROM OUTSIDE."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            action = getattr(self, "a_fullscreen", None)
            if action is not None and action.isChecked() != self.isFullScreen():
                action.blockSignals(True)
                action.setChecked(self.isFullScreen())
                action.blockSignals(False)

    def keyPressEvent(self, event) -> None:
        """Esc leaves full screen (it uses the same path as F11)."""
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.a_fullscreen.setChecked(False)
            self.toggle_fullscreen(False)
            return
        super().keyPressEvent(event)

    def set_theme(self, name: str) -> None:
        """Switches between the dark and the light theme.

        THREE STEPS are needed and all three are mandatory:

          1. ``apply_theme`` -- turns the ``C`` attributes to the new palette.
          2. The application style sheet IS REAPPLIED; because the style sheet
             embeds the colours INTO TEXT when it is built, changing ``C``
             alone is not enough.
          3. The widgets that read their colours AT SET-UP (the canvas brushes
             and pens, the panels using an inline ``setStyleSheet``) are rebuilt
             with ``retheme()``. Skip this step and the interface moves to the
             new theme while the canvas stays on the old background.
        """
        new = apply_theme(name)
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

        # The section title strips (WORKSPACE / PROPERTIES / PROBLEMS).
        for header in self._section_headers:
            header.setStyleSheet(
                "background: %s; color: %s; border-bottom: 1px solid %s;"
                % (C.PANEL_DARK, C.TEXT_DIM, C.BORDER))

        # THE ICONS ARE REDRAWN. The icons are drawn once with the set-up theme
        # colours and embedded in the QAction; unless they are regenerated on a
        # theme change they stay white (that is, invisible) on a light background
        # -- the toolbar looks empty.
        for action, factory in self._action_icons.items():
            action.setIcon(factory())

        # The canvas scene background and the items are rebuilt from the model.
        self.canvas.rebuild()
        self.class_canvas.rebuild()
        self._rebuild_views()

        act = self.theme_actions.get(new)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.flash("%s theme applied." % THEMES[new])

    def toggle_grid(self, checked: bool) -> None:
        self.canvas.show_grid = checked
        self.class_canvas.show_grid = checked
        self.canvas.viewport().update()
        self.class_canvas.viewport().update()
        self.flash("Grid: %s." % ("visible" if checked else "hidden"))

    #: The SMALLEST share the diagram pane may take in the main splitter.
    #: Below that the canvas stops being workable.
    MIN_DIAGRAM_SHARE = 0.22

    def _sizes_usable(self, sizes) -> bool:
        """Are the saved splitter shares workable.

        Checking for zero alone is not enough: 1 px is also "not zero" but
        unusable. The diagram pane -- the one all three modes sit in -- has to
        take a noticeable part of the total.
        """
        total = sum(sizes)
        if total <= 0:
            return False
        if any(v < 0 for v in sizes):
            return False
        index = self.main_splitter.indexOf(self.mode_tabs)
        if index < 0 or index >= len(sizes):
            return False
        return (sizes[index] / float(total)) >= self.MIN_DIAGRAM_SHARE

    @staticmethod
    def _restore_splitter_share(splitter, widget, ratio: float) -> None:
        """Gives a widget a VISIBLE share inside the splitter.

        QSplitter distributes the share of a hidden widget among its neighbours,
        and the share stays 0 when it is shown again. The widget becomes
        "visible" but takes no room on screen; to the user the button looks
        broken. The same happens when the user drags the splitter all the way
        and closes the window: the saved 0 share is restored at the next start.
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
        """Restores the panel sizes to the default.

        It brings hidden panels back too: when the user says "reset the layout"
        they expect WHAT THEY SAW AT FIRST START, not some half-way state.

        """
        self.a_code_panel.setChecked(True)
        self.code_panel.setVisible(True)
        # The simulation panel is CLOSED AT FIRST START; "reset" closes it too.
        # Leaving it open would take the reset to a state DIFFERENT from the first
        # start.
        self.a_sim_panel.setChecked(False)
        self.sim_panel.setVisible(False)
        self._clear_simulation()

        total = max(self.main_splitter.width(), 900)
        self.main_splitter.setSizes([int(total * 0.16),
                                     int(total * 0.52),
                                     int(total * 0.32)])
        self.left_col.setSizes([330, 560])
        self.state_center.setSizes([150, 720, 120])
        self.flash("Layout reset.")

    def code_panel_open(self) -> bool:
        """Is the code panel REALLY on screen.

        `isVisible()` is not enough: dragged all the way, the widget stays
        "visible" while its width is zero.
        """
        index = self.main_splitter.indexOf(self.code_panel)
        if index < 0 or not self.code_panel.isVisible():
            return False
        sizes = self.main_splitter.sizes()
        return index < len(sizes) and sizes[index] > 0

    def _sync_code_panel_action(self, *_args) -> None:
        """Syncs the menu tick with reality when the splitter is dragged."""
        acik = self.code_panel_open()
        if acik == self.a_code_panel.isChecked():
            return
        # CHANGING the tick would trigger the action (toggle_code_panel would hand
        # out a share again and the splitter the user dragged would spring back).
        self.a_code_panel.blockSignals(True)
        self.a_code_panel.setChecked(acik)
        self.a_code_panel.blockSignals(False)
        self.flash("Code panel hidden." if not acik else "Code panel shown.")

    def toggle_code_panel(self, checked: bool) -> None:
        self.code_panel.setVisible(checked)
        if checked:
            self._restore_splitter_share(self.main_splitter,
                                         self.code_panel, 0.40)
            # When `_restore_splitter_share` finds no neighbour to donate, it returns
            # SILENTLY and the panel stays 0 pixels: the user says "I opened it and
            # nothing came". Force it one last time.
            self._force_panel_share(self.main_splitter, self.code_panel, 0.34)
        self.flash("Code panel shown." if checked else "Code panel hidden.")

    @staticmethod
    def _force_panel_share(splitter, widget, ratio: float) -> None:
        """Redistributes the shares when the share of a widget is still 0.

        `_restore_splitter_share` is cautious: when it cannot take a share from
        the largest neighbour it returns untouched. That is not enough when a
        panel is asked to OPEN -- to the user it means "it did not open".
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
        share = max(1, int(total * ratio))
        kalan = total - share
        digerler = [i for i in range(len(sizes)) if i != index]
        if not digerler:
            return
        # Share the rest among the neighbours KEEPING THEIR RATIOS; split evenly if all are 0.
        old = sum(sizes[i] for i in digerler)
        for i in digerler:
            if old > 0:
                sizes[i] = max(1, int(kalan * sizes[i] / old))
            else:
                sizes[i] = max(1, kalan // len(digerler))
        sizes[index] = share
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
        """Stops the simulation and clears the active state highlight on the canvas.

        Hiding the panel alone was not enough: the last active state stayed
        highlighted on the canvas and the user took it for a SELECTION and tried
        to delete or move it. reset() emits `active_changed([])` and the canvas
        lowers the `is_active` flag of every state.
        """
        self.sim_panel.reset(quiet=True)

    def show_reference(self, title: str, detail: str) -> None:
        QMessageBox.information(self, title, "<b>%s</b><p>%s</p>"
                                % (title, detail.replace("\n", "<br>")))

    def show_spec(self) -> None:
        """Opens the UML 2.5.1 specification in a SEPARATE window.

        The document IS NOT EMBEDDED in the package: it is under OMG's copyright
        and 18 MB. It is opened when present locally; otherwise the user is
        asked and, WITH THEIR CONSENT, it is downloaded (spec_window.ensure_spec_pdf).
        """
        from .spec_window import SpecWindow, ensure_spec_pdf

        # A SECOND SIMULTANEOUS CALL IS BLOCKED.
        #
        # Clicking the menu item again while a download is running would start a
        # second downloader; two threads write to the SAME `.part` file and the
        # result is corrupt. The action is disabled as well, so the user can see
        # why nothing responds.
        if getattr(self, "_spec_busy", False):
            return
        self._spec_busy = True
        self.a_spec.setEnabled(False)
        try:
            path = ensure_spec_pdf(self)
        finally:
            self._spec_busy = False
            self.a_spec.setEnabled(True)
        if not path:
            return

        # THE PREVIOUS WINDOW IS TORN DOWN PROPERLY.
        #
        # Calling `close()` and overwriting the reference left the Qt object to the
        # mercy of Python's garbage collector; for a QMainWindow without a parent
        # that can mean being deleted while it is still shown.
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
            window = SpecWindow(path, None)
        except Exception as exc:               # noqa: BLE001
            QMessageBox.critical(
                self, "UML 2.5.1 specification",
                "The PDF viewer could not be started:"
                + chr(10) * 2 + str(exc))
            self.flash_error("The specification viewer could not start.")
            return
        self._spec_window = window
        window.show()
        window.raise_()
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
            # THE LETTERS ARE NOT WRITTEN BY HAND. The lists grew with the palette but
            # these two lines had stayed frozen: fork, join, the entry/exit points and
            # the submachine were NOT SHOWN at all, a "T" that does not exist was, and
            # on the class side Interface said "I" while it is "E". The single source
            # is the palette itself.
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

    # window

    def _restore_state(self) -> None:
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        sizes = self.settings.value("splitter")
        if sizes:
            try:
                values = [int(v) for v in sizes]
            except (TypeError, ValueError):
                values = []
            # THE ITEM COUNT HAS TO MATCH. The left column was added to the main
            # splitter later; a 2-item setting left over from an earlier version,
            # applied to a splitter with 3 children, leaves the remaining child at 0
            # pixels and the left panel invisible at start-up.
            #
            # THE SHARE RATIO HAS TO BE SENSIBLE TOO. With the saved setting
            # ['230','0','1364'] the diagram pane opened at 0 pixels, and because all
            # three modes (state, class, repository) live inside it, it looked as if
            # "nothing opens". The minimum width makes the pane visible afterwards but
            # does not fix the RATIO: the canvas would be squeezed into 320 px while
            # the code panel took 1344. A corrupt record is not PARTIALLY repaired, it
            # is discarded entirely.
            if len(values) == self.main_splitter.count() \
                    and self._sizes_usable(values):
                self.main_splitter.setSizes(values)
        # The saved share may be 0 (the user dragged the splitter all the way and
        # left). A panel ticked "open" but invisible makes the button look broken;
        # bring the panel back into view.
        self._restore_splitter_share(self.main_splitter, self.left_col, 0.18)
        # The code panel is DIFFERENT: the user may have closed it deliberately (by
        # dragging the splitter to the end or with F9). Bringing it back would undo
        # that decision at every start-up.
        if self.settings.value("code_panel_open", True, type=bool):
            self._restore_splitter_share(self.main_splitter,
                                         self.code_panel, 0.40)
        else:
            # REALLY HIDE THE PANEL. Only the tick used to be cleared: the panel stayed
            # "visible" as far as Qt was concerned while its width was 0. Opening it
            # from the menu then called `setVisible(True)`, nothing changed because it
            # was already visible, and the button looked broken.
            # calismiyor sanilir.
            self.code_panel.setVisible(False)
            self.a_code_panel.blockSignals(True)
            self.a_code_panel.setChecked(False)
            self.a_code_panel.blockSignals(False)
        # The simulation panel: CLOSED by default, but the preference is kept once
        # the user has opened it.
        if self.settings.value("sim_panel_open", False, type=bool):
            self.a_sim_panel.blockSignals(True)
            self.a_sim_panel.setChecked(True)
            self.a_sim_panel.blockSignals(False)
            self.sim_panel.setVisible(True)
            self._restore_splitter_share(self.state_center,
                                         self.sim_panel, 0.30)
        else:
            self.sim_panel.setVisible(False)
        # THE DIAGRAM PANE IS RESCUED LAST and UNCONDITIONALLY.
        #
        # It was the only pane WITHOUT protection, and it was exactly the one that
        # collapsed: with the saved setting ['230', '0', '1364'] the canvas opened
        # 0 pixels wide. Because all three modes (state, class, repository) live
        # inside this pane, the application looked as if "nothing opens" -- and
        # there was no visible splitter left for the user to grab and bring it
        # back.
        self._restore_splitter_share(self.main_splitter, self.mode_tabs, 0.45)
        # THE GRID SETTINGS PERSIST. When the user switched "Snap to Grid" off and
        # reopened the application, the setting coming back on gave the impression
        # that the button did not work.
        for action, key in ((self.a_snap, "snap_to_grid"),
                               (self.a_align, "align_guides"),
                               (self.a_grid, "show_grid")):
            record = self.settings.value(key)
            if record is not None:
                acik = record in (True, "true", "True", 1, "1")
                action.setChecked(acik)
                action.triggered.emit(acik)

        theme = self.settings.value("theme", "dark")
        if theme != active_theme():
            self.set_theme(theme)
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
        # THE USER'S INTENT is saved too, not just the shares. A 0 share can come
        # from two different things: the user closed the panel deliberately, or the
        # record is corrupt. Bringing the panel back unconditionally at start-up
        # without telling them apart would never let a close stick.
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
    """Reads the LICENSE file from the source tree or from the PyInstaller bundle."""
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
    """Opens the folder in the file manager of the operating system."""
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
    """Shows uncaught exceptions to the user; it DOES NOT KILL the process.

    PyQt6 calls ``qFatal`` on a Python exception escaping a slot body: the
    process ends at once without writing anything and all unsaved work is lost.
    That is unacceptable in commercial use -- the error is reported and the user
    can save their work and leave.
    """
    previous = sys.excepthook

    def hook(kind, value, tb) -> None:
        if issubclass(kind, KeyboardInterrupt):
            previous(kind, value, tb)
            return
        try:
            detail = "".join(traceback.format_exception(kind, value, tb))
        except Exception:                     # pragma: no cover - last resort
            detail = "%s: %s" % (getattr(kind, "__name__", kind), value)

        # In a console-less (--noconsole) package sys.stderr is NULL; an unguarded
        # write throws AttributeError here and the excepthook itself crashes --
        # that is, the dialog never opens exactly where it is needed most.
        stream = sys.stderr
        if stream is not None:
            try:
                stream.write(detail)
                stream.flush()
            except Exception:                 # pragma: no cover - last resort
                pass

        # A QMessageBox can only be built when a QApplication exists.
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
        except Exception:                     # pragma: no cover - last resort
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

    # --- workspace: the command line first, otherwise the start-up dialog
    cli_root = _workspace_from_argv(argv if argv is not None else sys.argv[1:])
    if cli_root:
        try:
            window.apply_workspace(Workspace.load(cli_root)
                                   if Workspace.is_workspace(cli_root)
                                   else Workspace.create(cli_root))
        except WorkspaceError as exc:
            QMessageBox.warning(window, "Workspace", str(exc))
    else:
        workspace, init_git = pick_workspace(
            window.recent_workspaces(), None, allow_cancel=True,
            on_forget=window.set_recent_workspaces)
        if workspace is not None:
            window.apply_workspace(workspace, init_git=init_git)

    window.show()
    return app.exec()


def _workspace_from_argv(argv: List[str]) -> str:
    """Extracts the value of ``--workspace <path>`` or ``--workspace=<path>``."""
    for i, arg in enumerate(argv):
        if arg == "--workspace" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--workspace="):
            return arg.split("=", 1)[1]
    return ""
