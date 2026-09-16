# UML Design Studio

Draw a UML 2.5.1 **state machine** or **class diagram**, press **Build**, and get
MISRA-aware **C11 / C++11** you can drop straight into an MCU project — plus a
working integration example, a simulator, and built-in version control.

![UML Design Studio — state machine mode](docs/screen-state-light.png)

---

## Why this exists

On most embedded projects the state machine lives in two places: a diagram in a
design document, and a hand-written `switch` statement in firmware. The moment
someone fixes a bug in the code, the diagram becomes a lie — and the next person
to read it is misled.

This tool removes the second copy. **The diagram is the source.** The firmware is
generated from it, deterministically, so the two can never drift apart. Along the
way you get things that are tedious to do by hand: correct hierarchical
run-to-completion semantics, history and choice/junction handling, MISRA-conscious
output, and a model that can be reviewed in a diff like any other source file.

---

## For embedded engineers

You keep writing firmware the way you already do. The tool only replaces the
state-machine or class skeleton.

**1 — Model your logic.** States, transitions, guards and actions. Guard and
action bodies are plain C/C++ expressions that call *your* functions.

**2 — Point it at your context.** In model settings you give a **context type**
(e.g. `blinky_ctx_t`) and any `#include`s. Inside every entry/exit/do/guard/effect
body, `ctx` (your struct) and `me` / `this` (the machine instance) are available:

```c
entry   /  ctx->blink_count = 0U;
guard   [  ctx->error_count > 3U  ]
effect  /  ctx->error_count++;
```

**3 — Build (F5).** You get `blinky.h`, `blinky.c` and a `blinky_main.c`
integration template. Add the first two to your build; the template shows the
wiring and is yours to throw away.

**4 — Drive it from your super-loop or RTOS task:**

```c
blinky_t sm;
blinky_ctx_t ctx = {0};

blinky_ctor(&sm, &ctx);
blinky_start(&sm);                          /* initial transition + entry */
blinky_dispatch(&sm, BLINKY_EVENT_BUTTON);  /* run-to-completion          */
blinky_do(&sm);                             /* doActivity chain           */
blinky_is_in(&sm, BLINKY_STATE_RUNNING);    /* includes parent states     */
```

```cpp
blinky::Blinky sm(&ctx);
sm.start();
sm.dispatch(blinky::Blinky::Event::Button);
```

**5 — Commit the model next to the code.** The generated files are byte-identical
for the same model, so a diff shows real design changes only.

The functions your actions call are **not** generated — they are listed for you in
the header, so a missing driver call is a compile error rather than a silent gap.

---

## What the generated code guarantees

* No dynamic memory, no recursion, no exceptions/RTTI
* All tables `static const` / `constexpr` — they live in flash, not RAM
* Reentrant: several instances of the same machine are fine
* Compiles clean under `-Wall -Wextra -pedantic -Werror -Wconversion -Wshadow`
* MISRA C:2012 / MISRA C++:2008 notes — including deliberate deviations —
  documented at the top of every generated file
* Doxygen comments on every function, in English
* Reproducible: no timestamps, same model → same bytes

---

## Install

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Or just run `run.bat`, which finds the virtual environment itself. Developed and
tested on Python 3.13 with PyQt6.

> **Windows note.** If Python came from the Microsoft Store, `pip install PyQt6`
> can fail on path length. The in-project virtual environment above avoids it.

To build a standalone executable:

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm UML-Design-Studio.spec
```

---

## The interface

The window is one workspace with three tabs — **State Diagram**, **Class Diagram**
and **Repository (Git)** — and four working areas:

* **Left** — the `WORKSPACE` tree (every model in the folder and everything inside
  it) and `PROPERTIES` for the selected element.
* **Centre** — the canvas, with the **SIMULATION** strip above it: `Start`,
  `Reset`, `do()`, one button per event, live guard switches, and a step-by-step
  run-to-completion trace.
* **Right** — the generated sources, one tab per file, with a language selector
  (C / C++ / PlantUML), `Copy` and `Export…`.
* **Bottom** — `PROBLEMS`. Every finding cites the **UML 2.5.1 clause and page**
  it comes from, and clicking a row selects the offending element on the canvas.

Validation runs continuously as you edit; code is written to disk only on
**Build** (`F5`), so dragging things around never touches the filesystem.

Useful keys: `F5` build · `F7` validate · `F8` repository · `F9` code panel ·
`F10` simulation · `F1` all shortcuts.

### Class diagrams

Classes, abstract classes and `«interface»`, with association, aggregation,
composition, generalization, realization and dependency — generating struct +
vtable C, or C++ with virtual dispatch.

![Class diagram mode](docs/screen-class-light.png)

### Version control, built in

The third tab is a git client scoped to your workspace: commit graph with
branches and merges, staging area, and a unified diff — so the model and the
firmware it produced are committed together.

![Repository tab](docs/screen-git-light.png)

---

## Simulation and validation

The simulator runs your diagram **before** any code is generated, using the same
intermediate representation and the same algorithm as the generators — equivalence
is re-checked on every test run, so what you see on the canvas is what the
firmware will do.

Validation knows the symbols the generator will actually emit, so name collisions
(`LedOn` vs `Led_On` landing on the same constant) are reported as errors instead
of becoming a broken build.

---

## Tests

```bash
.venv\Scripts\python.exe tools\check_all.py
```

The suite generates code, compiles it with `-Werror`, runs it, and compares the
behaviour trace against a Python reference and a fuzz run. It also checks UML
2.5.1 conformance clause by clause, the full feature surface through the real UI,
and the workspace/git layer against real temporary repositories. Steps that need
`gcc`/`g++` or `git` are skipped, not failed, when those are missing.

---

## Project layout

```
main.py                 entry point
app/core/               model, validation, simulation, workspace, git   (no Qt)
app/codegen/            C / C++ / PlantUML generators
app/ui/                 canvas, panels, dialogs, theme
examples/               blinky.usm + compilable support files
tools/                  tests, verification, packaging helpers
```

---

## Status and feedback

Version 2.0.0. This tool was developed with the help of **Claude (Anthropic)**,
and while it is backed by a fairly thorough automated test suite, it is a young
project and bugs are absolutely possible — in the UI, in the generated code, or in
a corner of the UML semantics.

If you run into one, please open an issue with the model file and what you
expected to happen. Bug reports, debugging notes and pull requests are genuinely
welcome and nothing will be taken personally — a reported problem is far more
useful than a silent workaround.

---

## References and license

The **References** menu inside the app lists the sources this tool is built on:
OMG UML 2.5.1, MISRA C:2012, MISRA C++:2008 / AUTOSAR C++14, ISO C11 / C++11,
Samek's table-driven HSM approach, GoF *Design Patterns*, and Douglass's *Design
Patterns for Embedded Systems in C*.

Licensed under the **GNU General Public License v3** — full text in
[LICENSE](LICENSE), and in the app under **Help → License**.
