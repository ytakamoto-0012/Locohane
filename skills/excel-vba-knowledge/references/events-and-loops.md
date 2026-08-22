# イベントプロシージャの落とし穴

## Worksheet_Change / Workbook_SheetChange の無限ループ

イベントハンドラ内で同じシートのセルを書き換えると、その書き換え自体が
再度 `Worksheet_Change` を発火させ、無限ループやスタックオーバーフローに陥る。
ハンドラの先頭で `Application.EnableEvents = False` にし、必ず戻す。

```vb
Private Sub Worksheet_Change(ByVal Target As Range)
    If Target.Address <> Range("B2").Address Then Exit Sub

    Application.EnableEvents = False
    On Error GoTo Restore
    Range("C2").Value = Target.Value * 2
Restore:
    Application.EnableEvents = True
    If Err.Number <> 0 Then Err.Raise Err.Number, Err.Source, Err.Description
End Sub
```

**`On Error` を挟まずに単純に `EnableEvents = True` を末尾に書いただけだと、
処理中に例外が起きた場合に `EnableEvents` が False のまま抜けてしまい、
以降そのブックのイベントが一切発火しなくなる**（ユーザーから見ると「マクロが
突然反応しなくなった」というバグ報告になりがちで、原因究明に時間がかかる）。
必ず後片付け（[[error-handling]] のCleanExitパターン）を通す。

## Target が複数セル（範囲）の場合を考慮する

`Worksheet_Change` の `Target` はユーザーが複数セルへ一括貼り付け・
オートフィル・行削除等をした場合、**単一セルではなく範囲**になる。
`Target.Value` を単一値として決め打ちで使うコードは、複数セル貼り付け時に
実行時エラーまたは意図しない値（左上セルの値のみ）を拾う。

```vb
Private Sub Worksheet_Change(ByVal Target As Range)
    Dim cell As Range
    If Intersect(Target, Range("B2:B100")) Is Nothing Then Exit Sub

    Application.EnableEvents = False
    On Error GoTo Restore
    For Each cell In Intersect(Target, Range("B2:B100"))
        cell.Offset(0, 1).Value = cell.Value * 2
    Next cell
Restore:
    Application.EnableEvents = True
    If Err.Number <> 0 Then Err.Raise Err.Number, Err.Source, Err.Description
End Sub
```

`Intersect(Target, 監視したい範囲)` で「変更されたセルのうち監視対象に含まれる
ものだけ」を安全に絞り込める（`Is Nothing` チェックを忘れると
Intersectが該当なしのときにエラーになる）。

## Workbook_Open と ThisWorkbook モジュールの注意

`Workbook_Open` は必ず `ThisWorkbook` モジュールに書く（標準モジュールに
書いても発火しない）。マクロ有効ブックを配布する場合、セキュリティ設定で
マクロが無効化されていると当然発火しない点もユーザーへの案内が必要になる
（`Application.AutomationSecurity` で制御できるのはコード側からの制御のみで、
ユーザーのトラストセンター設定そのものは変更できない）。

## Application.EnableEvents はブック単位ではなくアプリケーション全体

`Application.EnableEvents = False` は実行中のExcelインスタンス全体に効く。
複数ブックを同時に開いて処理するマクロで、あるブックの処理中に無効化した
つもりが他ブックのイベントも巻き添えで止まる（意図した抑制範囲とズレる）
ことがあるため、影響範囲を意識する。
