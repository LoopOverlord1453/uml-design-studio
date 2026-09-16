"""Uygulama simgesini (.ico) uretir - EXE paketlemesi icin.

    python tools/make_icon.py

app/ui/icons.py'deki app_icon cizimini koyu yuvarlak zemin uzerinde
16/24/32/48/64/128/256 px boyutlarinda cizer ve docs/app.ico dosyasina
COK COZUNURLUKLU ICO olarak yazar. ICO dizini elle kurulur: Qt'nin ico
yazicisi dosya basina tek kare yazar, art arda write() cagirmak kareleri
eklemez. Tek kareli bir ICO'da Windows gorev cubugu simgesini 256 px'ten
olceklemek zorunda kalir ve simge bulanik gorunur.
"""

from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (QBuffer, QIODevice, QPointF,    # noqa: E402
                          QRectF, Qt)
from PyQt6.QtGui import (QBrush, QColor, QImage, QPainter,  # noqa: E402
                         QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import QApplication                   # noqa: E402

from app.ui.theme import C                                 # noqa: E402

SIZES = (256, 128, 64, 48, 32, 24, 16)


def render(size: int) -> QImage:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = size / 40.0

    # koyu yuvarlak zemin
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(C.PANEL_DARK)))
    p.drawRoundedRect(QRectF(1 * s, 1 * s, 38 * s, 38 * s), 8 * s, 8 * s)

    # iki durum kutusu + gecis oku (app_icon ile ayni kompozisyon)
    p.setPen(QPen(QColor(C.ACCENT), 2.0 * s))
    p.setBrush(QBrush(QColor(C.STATE_FILL)))
    p.drawRoundedRect(QRectF(5 * s, 8 * s, 16 * s, 11 * s), 3 * s, 3 * s)
    p.drawRoundedRect(QRectF(20 * s, 22 * s, 16 * s, 11 * s), 3 * s, 3 * s)

    p.setPen(QPen(QColor(C.ORANGE), 2.4 * s, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(13 * s, 19 * s), QPointF(26 * s, 22.5 * s))
    p.setBrush(QBrush(QColor(C.ORANGE)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(QPolygonF([QPointF(28.5 * s, 23.5 * s),
                             QPointF(21.5 * s, 22.5 * s),
                             QPointF(24.5 * s, 17.5 * s)]))
    p.end()
    return pm.toImage()


def _png_bytes(image: QImage) -> bytes:
    """Goruntuyu PNG olarak bayta cevirir."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def _dib_bytes(image: QImage) -> bytes:
    """Goruntuyu ICO'nun bekledigi 32 bit DIB karesine cevirir.

    ICO icindeki DIB'ler BITMAPFILEHEADER TASIMAZ, yuksekligi IKI KATI yazilir
    (renk + maske) ve satirlar ALTTAN USTE dizilir. 32 bit alfa kullanildigi
    icin AND maskesi tumuyle sifirdir; yine de bicim geregi yer kaplar.
    """
    img = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = img.width(), img.height()

    rows = []
    for y in range(height - 1, -1, -1):        # alttan uste
        row = bytearray()
        for x in range(width):
            pixel = img.pixel(x, y)            # 0xAARRGGBB
            row += bytes(((pixel) & 0xFF,          # B
                          (pixel >> 8) & 0xFF,     # G
                          (pixel >> 16) & 0xFF,    # R
                          (pixel >> 24) & 0xFF))   # A
        rows.append(bytes(row))
    xor_data = b"".join(rows)

    mask_stride = ((width + 31) // 32) * 4      # 1 bpp, 4 bayta hizali
    and_data = b"\x00" * (mask_stride * height)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        width,              # biWidth
        height * 2,         # biHeight (renk + maske)
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression = BI_RGB
        len(xor_data) + len(and_data),
        0, 0, 0, 0,         # cozunurluk / palet alanlari
    )
    return header + xor_data + and_data


def write_ico(path: str, images) -> None:
    """Cok cozunurluklu ICO yazar.

    Qt'nin ICO yazicisi dosya basina TEK kare yazar; art arda write() cagirmak
    kareleri eklemez, dosyayi yeniden olusturur. Windows kucuk simgeleri (gorev
    cubugu, baslik cubugu) 256 px'ten olceklemek zorunda kalirsa bulanik gorunur,
    bu yuzden ICO dizini burada elle kurulur.

    256 px kare PNG olarak saklanir (Vista+ bicimi, dosyayi kucuk tutar);
    kucuk kareler her Windows surumunun okudugu DIB bicimindedir.
    """
    frames = []
    for image in images:
        payload = _png_bytes(image) if image.width() > 64 else _dib_bytes(image)
        frames.append((image.width(), image.height(), payload))

    offset = 6 + 16 * len(frames)
    directory = b""
    for width, height, payload in frames:
        directory += struct.pack(
            "<BBBBHHII",
            width if width < 256 else 0,
            height if height < 256 else 0,
            0,                  # palet yok
            0,                  # ayrilmis
            1,                  # duzlem
            32,                 # bit/piksel
            len(payload),
            offset,
        )
        offset += len(payload)

    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(frames)))   # ICONDIR
        fh.write(directory)
        for _w, _h, payload in frames:
            fh.write(payload)


def main() -> int:
    _app = QApplication.instance() or QApplication([])
    out_dir = os.path.join(ROOT, "docs")
    os.makedirs(out_dir, exist_ok=True)
    ico_path = os.path.join(out_dir, "app.ico")
    png_path = os.path.join(out_dir, "app_256.png")

    images = [render(sz) for sz in SIZES]
    images[0].save(png_path, "PNG")
    write_ico(ico_path, images)

    print("yazildi: %s (%d bayt, %d kare: %s)"
          % (ico_path, os.path.getsize(ico_path), len(images),
             ", ".join(str(s) for s in SIZES)))
    print("yazildi: %s (%d bayt)" % (png_path, os.path.getsize(png_path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
