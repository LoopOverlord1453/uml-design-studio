# Bundled fonts

Drop the **JetBrains Mono** `.ttf` files here and the application picks them
up automatically at start-up — no system installation needed, and the
packaged EXE keeps the same look.

Expected files (any subset works):

    JetBrainsMono-Regular.ttf
    JetBrainsMono-Bold.ttf
    JetBrainsMono-Italic.ttf

Download: https://www.jetbrains.com/lp/mono/  (SIL Open Font License 1.1)

If this folder is empty the UI falls back to Consolas / Segoe UI and keeps
working; only the typeface changes. `Help → About` reports which family is
actually in use.
