"""metadata モジュールのテスト."""

from pathlib import Path

import pymupdf

from podcast.metadata import _extract_pdf_first_image

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _make_pdf_with_image(path: Path, separation: bool = False) -> None:
    """1枚のグレースケール画像を含む PDF を作る.

    `separation=True` の場合は画像の colorspace を Separation に差し替える。
    Separation は成分数が 1 でも DeviceGray ではないため、そのままでは PNG に
    書き出せない（論文 PDF の図で実際に発生する）。
    """
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 8, 8), False)
    gray.set_rect(gray.irect, (128,))
    page.insert_image(pymupdf.Rect(10, 10, 90, 90), pixmap=gray)

    if separation:
        xref = page.get_images(full=True)[0][0]
        tint_fn = doc.get_new_xref()
        doc.update_object(
            tint_fn, "<< /FunctionType 2 /Domain [0 1] /C0 [1] /C1 [0] /N 1 >>"
        )
        doc.xref_set_key(
            xref, "ColorSpace", f"[/Separation /Black /DeviceGray {tint_fn} 0 R]"
        )

    doc.save(path)
    doc.close()


def test_extract_pdf_image_from_grayscale(tmp_path: Path) -> None:
    pdf_path = tmp_path / "gray.pdf"
    _make_pdf_with_image(pdf_path)

    data = _extract_pdf_first_image(pdf_path)

    assert data is not None
    assert data.startswith(_PNG_MAGIC)


def test_extract_pdf_image_converts_separation_colorspace(tmp_path: Path) -> None:
    """Separation colorspace は RGB へ変換して PNG 化する."""
    pdf_path = tmp_path / "separation.pdf"
    _make_pdf_with_image(pdf_path, separation=True)

    # 前提確認: 変換なしでは PNG 書き出しが失敗する画像であること
    doc = pymupdf.open(pdf_path)
    raw_pix = pymupdf.Pixmap(doc, doc[0].get_images(full=True)[0][0])
    assert raw_pix.colorspace.name not in ("DeviceGray", "DeviceRGB")
    doc.close()

    data = _extract_pdf_first_image(pdf_path)

    assert data is not None
    assert data.startswith(_PNG_MAGIC)
