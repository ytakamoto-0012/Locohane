"""excel/pptx/docx 共用のレンダリング共通モジュール。

動作概要:
  1. OLE（COM）で Office アプリ（Excel/PowerPoint/Word）をヘッドレス起動し、
     ファイルを高品質PDFへエクスポート。
  2. pypdfium2 で PDF ページを画像化。
  3. 白黒境界判定で余白を除去（コンテンツ領域のbboxを算出）。
  4. bbox でクロップ後、目標 DPI に縮尺を合わせる。

前提条件:
  - Windows 環境かつ Microsoft Office がインストールされていること。
  - pywin32, pypdfium2, pillow がインストールされていること。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pypdfium2 as pdfium

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_PROG_ID = {
    "excel": "Excel.Application",
    "pptx": "PowerPoint.Application",
    "docx": "Word.Application",
}

# PDFエクスポート定数
# Excel: xlTypePDF=0, PowerPoint: ppSaveAsPDF=32, Word: wdFormatPDF=17

# 画像化のデフォルト DPI（PDF→画像変換時に使用）
_DEFAULT_RENDER_DPI = 300

# 目標 DPI（余白除去後の縮尺）
_TARGET_DPI = 150

# 白黒境界判定の閾値（ピクセルあたりの黒画素率。これ未満なら白＝余白）
_MARGIN_THRESHOLD = 0.02  # 2%未満の黒画素なら余白とみなす


# ---------------------------------------------------------------------------
# OLE→PDF 変換
# ---------------------------------------------------------------------------


def _convert_office_to_pdf(path: Path, tool: str) -> Path:
    """OfficeファイルをOLE（COM）で開き、一時PDFへエクスポートする。

    tool: "excel" | "pptx" | "docx"
    戻り値: 生成されたPDFのパス
    """
    import pythoncom
    import win32com.client as win32

    prog_id = _PROG_ID[tool]
    pythoncom.CoInitialize()
    app = None
    doc = None
    pdf_path = None
    try:
        app = win32.DispatchEx(prog_id)
        # PowerPointは Presentations.Open 前後を問わず Application.Visible = False の
        # 設定自体がCOMエラーになる（ウィンドウ非表示は Open(WithWindow=False) 側で行う）。
        if tool != "pptx":
            app.Visible = False
        app.DisplayAlerts = 0  # wdAlertsNone

        abs_path = os.path.abspath(path)

        if tool == "excel":
            # Workbook → PDF (xlTypePDF = 0)
            doc = app.Workbooks.Open(abs_path)
            pdf_path = os.path.join(os.path.dirname(abs_path), f"_tmp_{tool}_export.pdf")
            # Quality は XlFixedFormatQuality（xlQualityStandard=0）の整数指定が必要
            doc.ExportAsFixedFormat(0, pdf_path, Quality=0, IncludeDocProperties=True, IgnorePrintAreas=False, OpenAfterPublish=False)
            doc.Close(SaveChanges=False)

        elif tool == "pptx":
            # Presentation → PDF (ppSaveAsPDF = 32)
            doc = app.Presentations.Open(abs_path, ReadOnly=True, Untitled=False, WithWindow=False)
            pdf_path = os.path.join(os.path.dirname(abs_path), f"_tmp_{tool}_export.pdf")
            doc.SaveAs(pdf_path, 32)
            doc.Close()

        elif tool == "docx":
            # Document → PDF (wdFormatPDF = 17)
            doc = app.Documents.Open(abs_path, ReadOnly=False, Revert=True)
            pdf_path = os.path.join(os.path.dirname(abs_path), f"_tmp_{tool}_export.pdf")
            doc.SaveAs2(pdf_path, 17)
            doc.Close(SaveChanges=False)

        else:
            raise ValueError(f"未知のツール: {tool}")

        return Path(pdf_path)

    except Exception:
        raise
    finally:
        if doc is not None:
            try:
                doc.Close()
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# 余白除去（白黒境界判定）
# ---------------------------------------------------------------------------


def _detect_content_bbox(image_path: Path) -> tuple[int, int, int, int] | None:
    """PNG画像から白黒境界判定でコンテンツ領域のbboxを算出。

    上・左から黒画素が現れる位置、下・右から黒画素が現れる位置を検出し、
    コンテンツ領域の (left, top, right, bottom) を返す。

    完全に白（コンテンツなし）の場合は None を返す。
    """
    from PIL import Image

    img = Image.open(image_path).convert("L")  # グレースケール
    pixels = img.load()
    w, h = img.size

    # 行方向（上から）
    top = None
    for y in range(h):
        row_black = sum(1 for x in range(w) if pixels[x, y] < 128)
        if row_black / w >= _MARGIN_THRESHOLD:
            top = y
            break

    if top is None:
        return None  # 全行白

    # 行方向（下から）
    bottom = None
    for y in range(h - 1, -1, -1):
        row_black = sum(1 for x in range(w) if pixels[x, y] < 128)
        if row_black / w >= _MARGIN_THRESHOLD:
            bottom = y
            break

    # 列方向（左から）
    left = None
    for x in range(w):
        col_black = sum(1 for y in range(h) if pixels[x, y] < 128)
        if col_black / h >= _MARGIN_THRESHOLD:
            left = x
            break

    # 列方向（右から）
    right = None
    for x in range(w - 1, -1, -1):
        col_black = sum(1 for y in range(h) if pixels[x, y] < 128)
        if col_black / h >= _MARGIN_THRESHOLD:
            right = x
            break

    if left is None or right is None:
        return None

    return (left, top, right, bottom)


def _crop_image(image_path: Path, bbox: tuple[int, int, int, int], target_dpi: int) -> Path:
    """bbox で画像をクロップし、目標 DPI に縮尺して保存。

    target_dpi: 目標DPI（画像の解像度を調整）
    戻り値: 保存先パス
    """
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    left, top, right, bottom = bbox

    if right <= left or bottom <= top:
        return image_path

    cropped = img.crop((left, top, right + 1, bottom + 1))

    # 目標 DPI に縮尺（元画像は _DEFAULT_RENDER_DPI で描画済み）
    scale = target_dpi / _DEFAULT_RENDER_DPI

    if abs(scale - 1.0) > 0.01:
        new_size = (
            max(int(cropped.width * scale), 1),
            max(int(cropped.height * scale), 1),
        )
        cropped = cropped.resize(new_size, Image.LANCZOS)

    out_path = image_path.with_name(image_path.stem + "_cropped.png")
    cropped.save(out_path, dpi=(target_dpi, target_dpi))
    return out_path


# ---------------------------------------------------------------------------
# PDF→画像レンダリング（pypdfium2）
# ---------------------------------------------------------------------------


def _render_pdf_to_images(pdf_path: Path, start_page: int, max_pages: int, dpi: int, thread_id: str) -> list[dict]:
    """PDFファイルを pypdfium2 で画像化し、リストとして返す。"""
    rendered_dir = Path.cwd() / f"_tmp_{thread_id}" / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)

    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        return []

    total_pages = len(pdf)
    max_pages = min(max(max_pages, 1), 5)
    scale = dpi / 72

    start_idx = max(start_page, 1) - 1
    end_idx = min(start_idx + max_pages, total_pages)

    digest = hashlib.sha1(os.path.abspath(str(pdf_path)).encode("utf-8")).hexdigest()[:8]

    images = []
    for i in range(start_idx, end_idx):
        page_num = i + 1
        filename = f"{digest}_p{page_num}.png"
        out_path = rendered_dir / filename
        try:
            bitmap = pdf[i].render(scale=scale)
            bitmap.to_pil().save(out_path)
        except Exception:
            continue
        images.append(
            {
                "page": page_num,
                "image_path": str(out_path),
                "original_dpi": dpi,
            }
        )

    return images


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------


def render_office_file(
    path: Path,
    tool: str,
    start_page: int = 1,
    max_pages: int = 3,
    dpi: int = 300,
    crop: bool = True,
    target_dpi: int = _TARGET_DPI,
    thread_id: str | None = None,
) -> dict:
    """Officeファイル（excel/pptx/docx）をレンダリングして画像化。

    Parameters
    ----------
    path : Path
        対象の Office ファイルパス
    tool : str
        "excel" | "pptx" | "docx"
    start_page : int
        開始ページ（1始まり）
    max_pages : int
        最大ページ数（最大5にクランプ）
    dpi : int
        PDF→画像変換時のDPI（72〜600にクランプ）
    crop : bool
        True なら白黒境界判定で余白を除去
    target_dpi : int
        余白除去後の目標DPI
    thread_id : str | None
        AGENT_THREAD_ID。None なら "_no_session"

    Returns
    -------
    dict
        レンダリング結果
    """
    if thread_id is None:
        thread_id = os.environ.get("AGENT_THREAD_ID") or "_no_session"

    dpi = min(max(dpi, 72), 600)
    max_pages = min(max(max_pages, 1), 5)

    # 1. OLE → PDF 変換
    pdf_path = _convert_office_to_pdf(path, tool)

    # PDFの総ページ数を取得（PDF→画像化の前に取得）
    try:
        tmp_pdf = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(tmp_pdf)
    except Exception:
        total_pages = 0

    # 2. PDF → 画像化
    images = _render_pdf_to_images(pdf_path, start_page, max_pages, dpi, thread_id)

    if not images:
        return {
            "path": str(path),
            "tool": tool,
            "total_pages": total_pages,
            "start_page": None,
            "end_page": None,
            "dpi": dpi,
            "images": [],
            "crop_applied": False,
        }

    # 3. 余白除去（crop=True の場合）
    crop_applied = False
    if crop:
        for img_info in images:
            img_path = Path(img_info["image_path"])
            bbox = _detect_content_bbox(img_path)
            if bbox is not None:
                cropped_path = _crop_image(img_path, bbox, target_dpi)
                img_info["image_path"] = str(cropped_path)
                img_info["cropped"] = True
                crop_applied = True
            else:
                img_info["cropped"] = False

    # 一時PDFを削除
    try:
        pdf_path.unlink(missing_ok=True)
    except Exception:
        pass

    first_page = images[0]["page"]
    last_page = images[-1]["page"]

    return {
        "path": str(path),
        "tool": tool,
        "total_pages": total_pages,
        "start_page": first_page if images else None,
        "end_page": last_page if images else None,
        "dpi": dpi,
        "target_dpi": target_dpi,
        "images": images,
        "crop_applied": crop_applied,
    }
