# ピボットテーブルの扱い方

## オブジェクト階層

`PivotCache`（元データとの接続・集計結果のメモリキャッシュ）と `PivotTable`
（それを表示するレイアウト）は別オブジェクト。1つのキャッシュを複数のピボット
テーブルで共有できる（同じ元データから複数のレイアウトを作る場合、キャッシュを
使い回すとメモリ・更新コストを削減できる）。

```vb
Dim pc As PivotCache
Dim pt As PivotTable
Dim srcRange As Range
Dim destSheet As Worksheet

Set srcRange = ThisWorkbook.Worksheets("Data").Range("A1").CurrentRegion
Set destSheet = ThisWorkbook.Worksheets("Report")

Set pc = ThisWorkbook.PivotCaches.Create( _
    SourceType:=xlDatabase, _
    SourceData:=srcRange)

Set pt = pc.CreatePivotTable( _
    TableDestination:=destSheet.Range("A3"), _
    TableName:="SalesPivot")
```

`SourceData` にはセル範囲だけでなく、テーブル名（[[excel-tables-listobject]]の
`ListObject`）を文字列で渡すこともでき、その場合は元データの行が増減しても
`SourceData` を書き換えずに済む（`SourceData:="Data!SalesTable"` のように
テーブル名指定にしておくのが定石。詳細は下記「テーブルを元データにする」参照）。

## フィールドの配置（行・列・値・フィルタ）

```vb
With pt
    .PivotFields("地域").Orientation = xlRowField
    .PivotFields("商品カテゴリ").Orientation = xlColumnField
    .PivotFields("担当者").Orientation = xlPageField   ' フィルタ欄

    With .PivotFields("売上金額")
        .Orientation = xlDataField
        .Function = xlSum
        .NumberFormat = "#,##0"
        .Name = "合計 / 売上金額"   ' 既定名（"合計 / xxx"）と衝突しないよう明示推奨
    End With
End With
```

値フィールドの `.Name` を明示しないと、同名フィールドを複数回データ欄に置いた際
（合計と平均を両方出す等）に自動採番された既定名（`合計 / 売上金額2` 等）に
なり、後続コードから参照する名前が予測しにくくなる。作成直後に明示的な名前を
付けておくと安全。

## データ更新（RefreshTable）と元データ範囲の追従

元データに行が追加された場合、`pt.RefreshTable` は**値の再集計**はするが、
**元データの範囲自体が広がったことまでは自動追従しない**（`SourceData` を
固定セル範囲で作った場合）。範囲を固定セル範囲にすると、行追加のたびに

```vb
pt.ChangePivotCache ThisWorkbook.PivotCaches.Create( _
    SourceType:=xlDatabase, _
    SourceData:=destSheet.Parent.Worksheets("Data").Range("A1").CurrentRegion)
pt.RefreshTable
```

のようにキャッシュを作り直す必要が出てくる。**元データを [[excel-tables-listobject]]
のテーブル（ListObject）にしておき、`SourceData` にテーブル名を渡す**方が、
行追加時も `RefreshTable` だけで追従するため保守性が高い（新規作成時は
この方式を優先して検討する）。

## 全ピボットテーブルの一括更新

```vb
Dim ws As Worksheet, p As PivotTable
For Each ws In ThisWorkbook.Worksheets
    For Each p In ws.PivotTables
        p.RefreshTable
    Next p
Next ws
```

または `ThisWorkbook.RefreshAll`（ピボットテーブル以外のクエリ等も含めて
全て更新するため、意図せず他の重い外部接続まで更新されないか確認してから使う）。

## フィルタの一括変更（PivotItems の Visible）

チェックボックス式フィルタ（複数選択可のページ/行/列フィルタ）の選択状態は
`PivotField.PivotItems` の `.Visible` を個別に切り替える。**最後の1件を
非表示にしようとするとエラーになる**（フィールドに表示アイテムが0件には
できない制約があるため）ので、全選択→対象だけ表示のように「まず全部Trueに
してから絞る」か、最低1件は残す順序で処理する。

```vb
Dim pi As PivotItem
With pt.PivotFields("地域")
    For Each pi In .PivotItems
        pi.Visible = (pi.Name = "関東" Or pi.Name = "関西")
    Next pi
End With
```

大量アイテムがある場合、1件ずつの `.Visible` 変更は遅い。後述の
`ManualUpdate` を有効にしてからまとめて変更する方が確実に効果がある。
（`EnableMultiplePageItems` はページ（フィルタ）欄のフィールドにチェック
ボックス式の複数選択UIを持たせるためのプロパティで、行・列欄のフィールドには
適用できず、`.Visible` 変更自体の処理速度にも影響しないため、高速化目的では
使わない）

## 更新中の高速化（ManualUpdate）

```vb
pt.ManualUpdate = True
' ... 複数フィールドのVisible切り替え等をまとめて実行 ...
pt.ManualUpdate = False   ' ここで初めて画面へ反映・再計算される
```

フィルタ条件を多数変更する処理では、都度再計算させず最後にまとめて反映させると
大幅に速くなる（[[performance-tips]] のScreenUpdating停止と同じ考え方）。

## PivotTable が既に存在する場合のエラー対策

同名の `TableName` で `CreatePivotTable` を再実行するとエラーになる。
マクロを複数回実行される前提の作り（再実行可能にする）にするなら、
実行前に既存ピボットの有無を確認して削除するか、既存があれば更新のみに
分岐する。

```vb
Dim exists As Boolean, p As PivotTable
For Each p In destSheet.PivotTables
    If p.Name = "SalesPivot" Then exists = True: Exit For
Next p

If exists Then
    destSheet.PivotTables("SalesPivot").RefreshTable
Else
    ' CreatePivotTable ...
End If
```
