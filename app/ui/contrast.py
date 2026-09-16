"""Secili satiri her temada okunur kilan cizim temsilcisi.

Agac ve liste ogeleri ANLAM tasiyan renklerle boyanir: uyari sarisi, hata
kirmizisi, gecis grisi, sozde-durum soluklugu... Bu renkler oge uzerinde
``setForeground`` ile sabitlenir ve Qt onlari SECIM durumunda da kullanir.
Sonuc: koyu mavi secim zemininin uzerinde koyu gri bir metin, yani gorunmez
bir satir -- ustelik hangi ogenin secili oldugu hic belli olmaz.

``ContrastDelegate`` secim (ve fare uzerindeyken vurgu) durumunda on plan
rengini temanin ZITLIK rengiyle degistirir ve yaziyi KALIN yapar. Boylece
secili oge iki temada da tek bakista ayirt edilir.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from .theme import C


class ContrastDelegate(QStyledItemDelegate):
    """Secili / vurgulu satirin metnini zemine zit renkte ve kalin cizer."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        state = option.state
        selected = bool(state & QStyle.StateFlag.State_Selected)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        if not (selected or hovered):
            return

        colour = QColor(C.SELECTED_TEXT if selected else C.TEXT_BRIGHT)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Normal):
            option.palette.setColor(group, QPalette.ColorRole.HighlightedText,
                                    colour)
            option.palette.setColor(group, QPalette.ColorRole.Text, colour)
        if selected:
            font = option.font
            font.setBold(True)
            option.font = font


def apply_contrast(*views) -> None:
    """Verilen gorunumlere temsilciyi baglar (ayni ornek paylasilmaz).

    Her gorunum KENDI temsilcisini tutar: Qt temsilcinin sahipligini almaz ve
    paylasilan bir ornek, gorunumlerden biri yok edilince digerlerinde askida
    gosterici birakabilir.
    """
    for view in views:
        if view is None:
            continue
        delegate = ContrastDelegate(view)
        view.setItemDelegate(delegate)
        # Referansi widget uzerinde tut: yalnizca yerel degiskende kalirsa
        # Python tarafinda toplanir ve satirlar varsayilan cizime doner.
        view._contrast_delegate = delegate
