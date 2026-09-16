"""Arayuzu ekransiz (offscreen) calistirarak temel akislari sinar.

    python tools/smoke_test.py

Pencereyi gercekten olusturur, arac kiplerini kullanir, eleman ekler/siler,
geri al/yinele yapar, dilleri degistirir ve uretilen kodu dogrular. Boylece
"aciyor ama tiklayinca cokuyor" sinifindaki hatalar CI'da yakalanir.
"""

from __future__ import annotations

import os

os.environ.setdefault("UMLSTUDIO_SETTINGS_SCOPE", "test")   # kullanici ayarlarina DOKUNMA
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt                     # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from app.core.samples import demo_machine, empty_machine          # noqa: E402
from app.core.validator import has_errors, validate               # noqa: E402
from app.ui.canvas import Tool                                    # noqa: E402
from app.ui.main_window import MainWindow                         # noqa: E402
from app.ui.theme import stylesheet, ui_font                      # noqa: E402

_failures = []


def check(condition: bool, label: str) -> None:
    if condition:
        print("  [ TAMAM ] %s" % label)
    else:
        _failures.append(label)
        print("  [ HATA  ] %s" % label)


def click(canvas, scene_x: float, scene_y: float) -> None:
    """Tuval uzerinde verilen SAHNE noktasina sol tik gonderir."""
    from PyQt6.QtGui import QMouseEvent
    view_pt = canvas.mapFromScene(QPointF(scene_x, scene_y))
    for kind in ("press", "release"):
        ev = QMouseEvent(
            {"press": QMouseEvent.Type.MouseButtonPress,
             "release": QMouseEvent.Type.MouseButtonRelease}[kind],
            QPointF(view_pt), QPointF(view_pt),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        if kind == "press":
            canvas.mousePressEvent(ev)
        else:
            canvas.mouseReleaseEvent(ev)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(9))
    app.setStyleSheet(stylesheet())

    print("== 1. Pencere olusturma ==")
    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    # Kullanicinin kayitli dil tercihi testi etkilemesin; deterministik baslat.
    win.set_language("c")
    # Yerlestirme sonrasi acilan modal ozellik diyalogu ekransiz testte bloklar.
    win.canvas.auto_edit = False
    win.class_canvas.auto_edit = False
    # ORNEK MODELI ACIKCA YUKLE. Uygulama artik acilista demo yuklemiyor
    # (bos sablonla basliyor); ornek yalnizca File > Sample... ile gelir.
    win.load_demo()
    win.load_demo_class()
    win.mode_tabs.setCurrentIndex(0)
    app.processEvents()
    win._rebuild_views()
    # Kod uretimi artik KENDILIGINDEN calismaz; ornegi yukledikten sonra
    # acikca derlenmeli (kullanicinin Build dedigi an).
    win.build()
    app.processEvents()
    check(len(win.canvas.state_items) == len(win.doc.machine.states),
          "tum durumlar cizildi (%d)" % len(win.canvas.state_items))
    check(len(win.canvas.tran_items) == len(win.doc.machine.transitions),
          "tum gecisler cizildi (%d)" % len(win.canvas.tran_items))
    check(win.code_panel.tabs.count() == 3,
          "C icin 3 sekme acildi (.h / .c / _main.c)")

    print("\n== 2. Dogrulama ve kod uretimi ==")
    issues = validate(win.doc.machine)
    check(not has_errors(issues), "ornek modelde dogrulama hatasi yok")
    check("blinky.h" in win._last_files and "blinky.c" in win._last_files,
          "C dosyalari uretildi")
    check("blinky_dispatch" in win._last_files["blinky.h"],
          "baslikta dispatch bildirimi var")

    win.set_language("cpp")
    app.processEvents()
    check("Blinky.hpp" in win._last_files, "C++'a gecince .hpp uretildi")
    check("class Blinky" in win._last_files["Blinky.hpp"], "sinif bildirimi var")

    win.set_language("puml")
    app.processEvents()
    check(any(n.endswith(".puml") for n in win._last_files), "PlantUML uretildi")
    win.set_language("c")
    app.processEvents()

    print("\n== 3. Arac kipleri ile eleman ekleme ==")
    before_states = len(win.doc.machine.states)
    win.set_tool(Tool.STATE)
    click(win.canvas, 60.0, 620.0)
    app.processEvents()
    check(len(win.doc.machine.states) == before_states + 1, "durum eklendi")
    check(win.canvas.tool is Tool.SELECT, "arac Sec kipine dondu")

    new_id = [s.id for s in win.doc.machine.states.values()
              if s.name.startswith("State")][0]
    win.set_tool(Tool.TRANSITION)
    off_item = next(i for i in win.canvas.state_items.values()
                    if i.state.name == "Off")
    new_item = win.canvas.state_items[new_id]
    before_trans = len(win.doc.machine.transitions)
    click(win.canvas, off_item.scene_center().x(), off_item.scene_center().y())
    click(win.canvas, new_item.scene_center().x(), new_item.scene_center().y())
    app.processEvents()
    check(len(win.doc.machine.transitions) == before_trans + 1, "gecis eklendi")

    print("\n== 4. Dogrulayici gercekten hata yakaliyor mu ==")
    app.processEvents()
    issues = validate(win.doc.machine)
    check(any(i.code == "V071" or i.code == "V072" for i in issues),
          "yeni cikmaz durum icin uyari uretildi")

    tid = [t.id for t in win.doc.machine.transitions.values()
           if t.target == new_id][0]
    win.doc.edit("test", lambda m: setattr(m.transitions[tid], "guard", "a > (b"))
    app.processEvents()
    issues = validate(win.doc.machine)
    check(any(i.code == "V041" for i in issues), "dengesiz parantez yakalandi")
    # Bant kod PANELININ ICINDEDIR: panel kapaliyken isVisible() dogal
    # olarak False doner. Test, ayar kapsamindan sizan "panel kapali"
    # tercihine gore rastgele dusuyordu; ne dogruladigini acikca yaz.
    # (Dogrulama bulgulari ayrica alttaki Problems panelinde durur, yani
    # panel kapaliyken de kullanici hatalari gorur.)
    win.a_code_panel.setChecked(True)
    win.toggle_code_panel(True)
    app.processEvents()
    check(win.code_panel.banner.isVisible(),
          "kod paneli acikken hata bandi gorunuyor")
    win.doc.undo_stack.undo()
    app.processEvents()

    print("\n== 5. Geri al / yinele ==")
    count_now = len(win.doc.machine.states)
    win.doc.undo_stack.undo()      # gecis ekleme
    win.doc.undo_stack.undo()      # durum ekleme
    app.processEvents()
    check(len(win.doc.machine.states) == count_now - 1, "geri al durumu kaldirdi")
    win.doc.undo_stack.redo()
    app.processEvents()
    check(len(win.doc.machine.states) == count_now, "yinele durumu geri getirdi")
    check(len(win.canvas.state_items) == len(win.doc.machine.states),
          "sahne modelle esitlendi")

    print("\n== 6. Secim, denetleyici ve silme ==")
    win.canvas.set_selected_ids([new_id])
    app.processEvents()
    check(win.inspector._ids == [new_id], "denetleyici secimi izliyor")
    win.canvas.delete_selection()
    app.processEvents()
    check(new_id not in win.doc.machine.states, "durum silindi")
    check(all(t.target != new_id for t in win.doc.machine.transitions.values()),
          "iliskili gecis de silindi")

    print("\n== 7. Hiyerarsi: bilesik duruma birakma ==")
    run_item = next(i for i in win.canvas.state_items.values()
                    if i.state.name == "Running")
    off_state = next(s for s in win.doc.machine.states.values() if s.name == "Off")
    off_item = win.canvas.state_items[off_state.id]
    win.canvas._pre_drag = win.doc.machine.to_json()
    target = run_item.mapToScene(run_item.content_rect().topLeft()) \
        + QPointF(20.0, 20.0)
    off_item.setSelected(True)
    off_item.setPos(off_item.mapFromScene(target))
    win.canvas._apply_reparenting()
    win.canvas._write_geometry_to_model()
    win.doc.edit_from("tasi", win.canvas._pre_drag)
    app.processEvents()
    check(win.doc.machine.states[off_state.id].parent == run_item.state.id,
          "durum bilesik durumun icine tasindi")
    win.doc.undo_stack.undo()
    app.processEvents()
    check(win.doc.machine.states[off_state.id].parent is None,
          "geri al hiyerarsiyi eski haline getirdi")

    print("\n== 8. Dosya cevrimi ==")
    tmp = tempfile.mkdtemp(prefix="usd_smoke_")
    path = os.path.join(tmp, "test.usm")
    win.doc.save(path)
    check(os.path.exists(path), "diyagram kaydedildi")
    win.doc.replace(empty_machine(), None)
    app.processEvents()
    check(len(win.doc.machine.states) == 2, "bos model yuklendi")
    win.doc.load(path)
    app.processEvents()
    check(len(win.doc.machine.states) == len(demo_machine().states),
          "kaydedilen diyagram geri yuklendi")

    win._last_export_dir = tmp
    for name, text in win._last_files.items():
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    check(os.path.exists(os.path.join(tmp, "blinky.c")), "kod disa aktarilabildi")

    print("\n== 9. Bos model de kod uretebiliyor mu ==")
    win.doc.replace(empty_machine(), None)
    app.processEvents()
    win.build()
    app.processEvents()
    check(not has_errors(validate(win.doc.machine)), "bos model gecerli")
    check("sm.c" in win._last_files, "bos model icin de kod uretildi")

    print("\n== 10. Yakinlastirma / gorunum ==")
    win.canvas.zoom_by(1.2)
    win.canvas.zoom_fit()
    win.canvas.zoom_reset()
    win.toggle_grid(False)
    win.toggle_grid(True)
    win.toggle_snap(False)
    win.toggle_snap(True)
    app.processEvents()
    check(True, "gorunum islemleri hatasiz calisti")

    print("\n== 11. Simulasyon paneli ==")
    win.doc.replace(demo_machine(), None)
    app.processEvents()
    sim = win.sim_panel
    sim.start()
    app.processEvents()
    check(sim.sim is not None and sim.sim.state_name == "Off",
          "simulasyon Off durumunda basladi")
    sim.dispatch("BUTTON")
    app.processEvents()
    check(sim.sim.state_name == "LedOn", "BUTTON -> LedOn (bilesik duruma indi)")
    active = [i for i in win.canvas.state_items.values() if i.is_active]
    check(len(active) == 2, "tuvalde LedOn + Running vurgulandi (%d)" % len(active))
    sim.dispatch("TICK")
    sim.dispatch("TICK")
    app.processEvents()
    check(sim.sim.state_name == "LedOn", "iki TICK sonra yine LedOn")
    sim.dispatch("SHUTDOWN")
    app.processEvents()
    check(sim.sim.is_terminated(), "SHUTDOWN -> final durum")
    check(not sim._event_buttons[0].isEnabled(),
          "final durumda olay dugmeleri kapandi")
    check(len(sim.trace.toPlainText().splitlines()) > 8, "iz kaydi dolduruldu")

    # Simulator ile uretilen C ayni davranmali (referans dogrulama)
    from app.core.simulator import Simulator
    ref = Simulator(win.doc.machine)
    ref.start()
    for ev in ("BUTTON", "TICK", "TICK", "SHUTDOWN"):
        ref.dispatch(ev)
    check(ref.state_name == sim.sim.state_name,
          "panel ve dogrudan simulator ayni sonuca vardi")

    sim.reset()
    app.processEvents()
    check(all(not i.is_active for i in win.canvas.state_items.values()),
          "sifirlama vurgulari temizledi")

    win.doc.mark_clean()
    win.settings.clear()   # gercek kullanicinin ayarlarini kirletme
    win.close()
    app.processEvents()

    print("\n== Ozet ==")
    if _failures:
        print("  %d kontrol basarisiz:" % len(_failures))
        for f in _failures:
            print("    - %s" % f)
        return 1
    print("  Tum arayuz kontrolleri gecti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
