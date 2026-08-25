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
import sys
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

# PDF→画像化キャプチャDPI（docx-render/pptx-renderと同じ基準値に統一）
_CAPTURE_DPI = 300

# 目標DPI（クロップ後の最終出力解像度）。既定ではキャプチャDPIと同値のため
# ダウンスケールは事実上no-opになる（縮尺が必要なケースのみ_crop_imageが動く）。
_TARGET_DPI = 300

# 白黒境界判定の閾値（ピクセル値。これ未満なら黒＝コンテンツとみなす）
_DARK_PIXEL_THRESHOLD = 128

# --- フィット印刷の縮尺補正（3.2節で使用） ---
# 用紙サイズ(pt)の対応表。xlPaperSize定数のうち代表的なもののみ。
# 未対応の値の場合はスケール計算をスキップする（=A4等を勝手に仮定しない）。
_PAPER_SIZE_PT: dict[int, tuple[float, float]] = {
    1: (612.0, 792.0),  # xlPaperLetter
    5: (612.0, 1008.0),  # xlPaperLegal
    8: (841.89, 1190.55),  # xlPaperA3
    9: (595.28, 841.89),  # xlPaperA4
    11: (419.53, 595.28),  # xlPaperA5
    12: (728.50, 1031.81),  # xlPaperB4
    13: (516.22, 728.50),  # xlPaperB5
}

_FIT_SCALE_FLOOR = 0.10  # Excel自体の縮小印刷下限（これ未満は指定不可でページが分割される）
_BOOST_TRIGGER_SCALE = 0.5  # この値を下回ったらDPIブーストを検討する
_BOOST_DPI_CEIL = 900  # ブースト後キャプチャDPIの上限（暴走防止）


# ---------------------------------------------------------------------------
# OLE→PDF 変換
# ---------------------------------------------------------------------------


def _sheet_required_scale(ws) -> float:
    """シートの内容（セル使用範囲＋図形）が、フィット印刷でどこまで縮小を要求
    されるか（Excel自体の10%下限を適用する前の理論値）を返す。1.0なら縮小不要。
    用紙サイズが対応表に無い場合や算出できない場合は1.0（＝補正なし）を返す。
    """
    try:
        used = ws.UsedRange
        content_left = used.Left
        content_top = used.Top
        content_right = used.Left + used.Width
        content_bottom = used.Top + used.Height

        # 図形（オートシェイプ・グラフ等）はUsedRangeに含まれないため、
        # 存在する場合はbboxをUsedRangeとの和集合に広げる。
        shapes = ws.Shapes
        for i in range(1, shapes.Count + 1):
            shp = shapes.Item(i)
            content_left = min(content_left, shp.Left)
            content_top = min(content_top, shp.Top)
            content_right = max(content_right, shp.Left + shp.Width)
            content_bottom = max(content_bottom, shp.Top + shp.Height)

        content_w = content_right - content_left
        content_h = content_bottom - content_top
        if content_w <= 0 or content_h <= 0:
            return 1.0

        ps = ws.PageSetup
        paper = _PAPER_SIZE_PT.get(int(ps.PaperSize))
        if paper is None:
            return 1.0  # 未対応の用紙サイズは補正をスキップ（A4等を仮定しない）
        paper_w, paper_h = paper
        if int(ps.Orientation) == 2:  # xlLandscape
            paper_w, paper_h = paper_h, paper_w

        printable_w = paper_w - ps.LeftMargin - ps.RightMargin
        printable_h = paper_h - ps.TopMargin - ps.BottomMargin
        if printable_w <= 0 or printable_h <= 0:
            return 1.0

        return min(printable_w / content_w, printable_h / content_h, 1.0)
    except Exception:
        return 1.0


def _convert_office_to_pdf(path: Path, tool: str, thread_id: str) -> tuple[Path, dict[str, float]]:
    """OfficeファイルをOLE（COM）で開き、一時PDFへエクスポートする。

    tool: "excel" | "pptx" | "docx"
    戻り値: (生成されたPDFのパス, シート名→required_scaleの辞書)。
        excel以外はscale辞書は空dict。
    """
    import pythoncom
    import win32com.client as win32

    # office_shared/excel_common.py（呼び出し元のrender_excel.py等が既にsys.pathへ
    # 追加済み）から、Office COMプロセスのPID追跡ヘルパーをimportする。
    # 関数名はExcel由来だが、Hwnd取得の仕組み自体はWord/PowerPointのApplication
    # オブジェクトでも同様に動く（両方ともHwndプロパティを持つ）。
    from excel_common import record_excel_pid, release_excel_pid, wait_for_process_exit

    prog_id = _PROG_ID[tool]
    pythoncom.CoInitialize()
    app = None
    doc = None
    pdf_path = None
    recorded_pid = None
    scale_by_sheet: dict[str, float] = {}
    try:
        app = win32.DispatchEx(prog_id)
        # run_scriptの外部タイムアウト等でPythonプロセスごと強制終了されると
        # 下のfinally節が実行されずCOMプロセスだけが残留することがあるため、
        # 起動直後に自セッションのPIDレジストリへ記録しておく（正常終了時は
        # 実終了を確認した上でfinallyで取り除く。残留した場合は
        # excel-vba-editスキルのedit_vba.py --recover-locksで後始末できる）。
        recorded_pid = record_excel_pid(path, app)
        # PowerPointは Presentations.Open 前後を問わず Application.Visible = False の
        # 設定自体がCOMエラーになる（ウィンドウ非表示は Open(WithWindow=False) 側で行う）。
        if tool != "pptx":
            app.Visible = False
        app.DisplayAlerts = 0  # wdAlertsNone

        abs_path = os.path.abspath(path)

        # 中間生成物のPDFは元ファイルのフォルダを汚さないよう、他の一時出力
        # （_render_pdf_to_images の画像など）と同じ `_tmp_<thread_id>/` 配下に
        # 保存する。基準は run_script の cwd（ユーザー指定 work_dir になりうり
        # 自動削除対象外）ではなく、常に default_workdir（AGENT_DEFAULT_WORKDIR）。
        # default_workdir の保持日数ベースの自動削除で確実に消える。
        base_dir = Path(os.environ.get("AGENT_DEFAULT_WORKDIR") or "./data/temp")
        out_dir = base_dir / f"_tmp_{thread_id}" / "pdf_export"
        out_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:8]
        pdf_path = str(out_dir / f"{tool}_{digest}_export.pdf")

        if tool == "excel":
            # Workbook → PDF (xlTypePDF = 0)
            doc = app.Workbooks.Open(abs_path)
            for ws in doc.Worksheets:
                try:
                    scale_by_sheet[ws.Name] = _sheet_required_scale(ws)
                except Exception:
                    scale_by_sheet[ws.Name] = 1.0
                try:
                    page_setup = ws.PageSetup
                    page_setup.Zoom = False
                    page_setup.FitToPagesWide = 1
                    page_setup.FitToPagesTall = 1
                except Exception:
                    continue
            doc.ExportAsFixedFormat(0, pdf_path, Quality=0, IncludeDocProperties=True, IgnorePrintAreas=False, OpenAfterPublish=False)
            doc.Close(SaveChanges=False)
            doc = None

        elif tool == "pptx":
            # Presentation → PDF (ppSaveAsPDF = 32)
            doc = app.Presentations.Open(abs_path, ReadOnly=True, Untitled=False, WithWindow=False)
            doc.SaveAs(pdf_path, 32)
            doc.Close()
            doc = None

        elif tool == "docx":
            # Document → PDF (wdFormatPDF = 17)
            doc = app.Documents.Open(abs_path, ReadOnly=False, Revert=True)
            doc.SaveAs2(pdf_path, 17)
            doc.Close(SaveChanges=False)
            doc = None

        else:
            raise ValueError(f"未知のツール: {tool}")

        return Path(pdf_path), (scale_by_sheet if tool == "excel" else {})

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
        if recorded_pid is not None and wait_for_process_exit(recorded_pid):
            release_excel_pid(recorded_pid)
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# 余白除去（白黒境界判定）
# ---------------------------------------------------------------------------


def _detect_content_bbox(image_path: Path) -> tuple[int, int, int, int] | None:
    """PNG画像からコンテンツ領域のbbox（left, top, right, bottom）を算出する。

    グレースケール化し、閾値未満（暗い）ピクセルの外接矩形をPILの
    getbbox()で直接求める。

    旧実装は上下・左右を別々に「黒画素数 ÷ 画像の幅（上下判定）/高さ
    （左右判定）」の比率で判定していたため、コンテンツがシートの一部
    （例: 上部のみ）に偏っている場合、左右判定の比率が画像全高で薄まって
    しまい、実際のコンテンツ幅よりはるかに狭いbboxになるバグがあった。
    getbbox()は行・列を独立に判定しないため、コンテンツの偏りに影響
    されず正確な外接矩形を返す。

    完全に白（コンテンツなし）の場合は None を返す。
    """
    from PIL import Image

    img = Image.open(image_path).convert("L")  # グレースケール
    mask = img.point(lambda p: 255 if p < _DARK_PIXEL_THRESHOLD else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return None  # 全面白（コンテンツなし）

    left, top, right, bottom = bbox
    # getbbox()は半開区間 [left, right) x [top, bottom) を返す。呼び出し側
    # _crop_image の crop((left, top, right+1, bottom+1)) と整合させるため、
    # 包含的な最終インデックス（right-1, bottom-1）に変換して返す。
    return (left, top, right - 1, bottom - 1)


def _crop_image(image_path: Path, bbox: tuple[int, int, int, int], target_dpi: int, source_dpi: int) -> Path:
    """bbox で画像をクロップし、目標 DPI に縮尺して保存。

    target_dpi: 目標DPI（画像の解像度を調整）
    source_dpi: 元画像の実際の描画DPI（_render_pdf_to_images に渡した dpi）
    戻り値: 保存先パス
    """
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    left, top, right, bottom = bbox

    if right <= left or bottom <= top:
        return image_path

    cropped = img.crop((left, top, right + 1, bottom + 1))

    # 目標 DPI に縮尺（元画像は source_dpi で描画済み）
    scale = target_dpi / source_dpi

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


def _render_pdf_to_images(pdf_path: Path, dpi: int, thread_id: str) -> list[dict]:
    """PDFファイルの全ページを pypdfium2 で画像化し、リストとして返す。"""
    base_dir = Path(os.environ.get("AGENT_DEFAULT_WORKDIR") or "./data/temp")
    rendered_dir = base_dir / f"_tmp_{thread_id}" / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)

    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        return []

    total_pages = len(pdf)
    scale = dpi / 72

    digest = hashlib.sha1(os.path.abspath(str(pdf_path)).encode("utf-8")).hexdigest()[:8]

    images = []
    for i in range(total_pages):
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
    crop: bool = True,
    target_dpi: int = _TARGET_DPI,
    thread_id: str | None = None,
) -> dict:
    """Officeファイル（excel/pptx/docx）の全ページをレンダリングして画像化。

    Parameters
    ----------
    path : Path
        対象の Office ファイルパス
    tool : str
        "excel" | "pptx" | "docx"
    crop : bool
        True なら白黒境界判定で余白を除去
    target_dpi : int
        余白除去後の目標DPI
    thread_id : str | None
        AGENT_EXEC_TMP_NAME（無ければAGENT_THREAD_ID）。None なら "_no_session"

    Returns
    -------
    dict
        レンダリング結果
    """
    if thread_id is None:
        thread_id = os.environ.get("AGENT_EXEC_TMP_NAME") or os.environ.get("AGENT_THREAD_ID") or "_no_session"

    # 1. OLE → PDF 変換（excelのみ、シートごとの必要縮尺も同時に取得）
    pdf_path, scale_by_sheet = _convert_office_to_pdf(path, tool, thread_id)

    # 1.5 縮尺に応じたキャプチャDPI・目標DPIの動的ブーストと、分割警告の生成
    capture_dpi = _CAPTURE_DPI
    effective_target_dpi = target_dpi
    warnings: list[str] = []
    if scale_by_sheet:
        worst_scale = min(scale_by_sheet.values())
        if worst_scale < _BOOST_TRIGGER_SCALE:
            boost_factor = _BOOST_TRIGGER_SCALE / max(worst_scale, 0.01)
            capture_dpi = min(round(_CAPTURE_DPI * boost_factor), _BOOST_DPI_CEIL)
            effective_target_dpi = min(round(target_dpi * boost_factor), _BOOST_DPI_CEIL)
        split_sheets = [name for name, s in scale_by_sheet.items() if s < _FIT_SCALE_FLOOR]
        if split_sheets:
            msg = (
                "警告: シート " + ", ".join(split_sheets) + " は列幅・行高さの合計が用紙1ページに収めるための下限スケール"
                "(10%)を下回っています。1ページに収まらず複数ページに分割され"
                "ています。excel-editのset_column_width（単位は文字幅、目安1〜60）"
                "で列幅を見直してください。"
            )
            warnings.append(msg)
            print(msg, file=sys.stderr)

    # PDFの総ページ数を取得（PDF→画像化の前に取得）
    try:
        tmp_pdf = pdfium.PdfDocument(str(pdf_path))
        total_pages = len(tmp_pdf)
    except Exception:
        total_pages = 0

    # 2. PDF → 画像化（全ページ）
    images = _render_pdf_to_images(pdf_path, capture_dpi, thread_id)

    if not images:
        result = {
            "path": str(path),
            "tool": tool,
            "total_pages": total_pages,
            "start_page": None,
            "end_page": None,
            "dpi": capture_dpi,
            "images": [],
            "crop_applied": False,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    # 3. 余白除去（crop=True の場合）
    crop_applied = False
    if crop:
        for img_info in images:
            img_path = Path(img_info["image_path"])
            bbox = _detect_content_bbox(img_path)
            if bbox is not None:
                cropped_path = _crop_image(img_path, bbox, effective_target_dpi, capture_dpi)
                if cropped_path != img_path:
                    try:
                        img_path.unlink(missing_ok=True)
                    except Exception:
                        pass
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

    result = {
        "path": str(path),
        "tool": tool,
        "total_pages": total_pages,
        "start_page": first_page if images else None,
        "end_page": last_page if images else None,
        "dpi": capture_dpi,
        "target_dpi": effective_target_dpi,
        "images": images,
        "crop_applied": crop_applied,
    }
    if warnings:
        result["warnings"] = warnings
    return result
