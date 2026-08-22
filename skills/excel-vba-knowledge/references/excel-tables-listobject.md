# テーブル（ListObject）の扱い方

「挿入」→「テーブル」で作る構造化参照テーブルはVBAでは `ListObject` として
操作する。セル範囲を都度 `CurrentRegion` 等で特定するより、行の増減に自動追従
する分バグが少なく、[[pivot-tables]] の元データとしても相性が良い。

## 階層と主要プロパティ

```vb
Dim tbl As ListObject
Set tbl = ThisWorkbook.Worksheets("Data").ListObjects("SalesTable")

tbl.Range          ' ヘッダー行〜合計行まで含む全体（見出し含む）
tbl.HeaderRowRange  ' 見出し行のみ
tbl.DataBodyRange   ' データ行のみ（見出し・合計行を除く）。行が0件だとNothing
tbl.ListColumns("金額").DataBodyRange  ' 特定列のデータ行のみ
```

**`DataBodyRange` はデータが1行も無い（見出しのみの）状態だと `Nothing` を
返す。** 存在確認せずに `.Rows.Count` 等を呼ぶと実行時エラーになるため、
空テーブルを扱う可能性があるコードでは必ず `Is Nothing` を確認する。

```vb
If Not tbl.DataBodyRange Is Nothing Then
    Dim r As Long
    For r = 1 To tbl.DataBodyRange.Rows.Count
        ' ...
    Next r
End If
```

## 行の追加

```vb
Dim newRow As ListRow
Set newRow = tbl.ListRows.Add
newRow.Range(1, tbl.ListColumns("商品名").Index).Value = "新商品"
newRow.Range(1, tbl.ListColumns("金額").Index).Value = 1000
```

`ListRows.Add` は第1引数 `Position` を省略すると既定で**末尾**に追加される
（特定の位置に挿入したい場合は `Position` に相対位置を指定する）。第2引数
`AlwaysInsert` は挿入位置ではなく、テーブル直下の行を常にシフトするかどうか
を制御するもの（省略時は `True` と同じ動作＝直下の行を1行ずつ下にシフト。
`False` を指定し、かつ直下の行が空であれば、その行を巻き込まずにテーブルの
方だけを拡張する）。列位置を決め打ちの数値（`newRow.Range(1, 3)` 等）で書くと、
後から列を並べ替えた際にコードとズレるため、`tbl.ListColumns("列名").Index`
で列名から解決する方が保守性が高い。

## 列名から配列で一括読み書き（列並べ替えに強い書き方）

```vb
Dim data As Variant
data = tbl.DataBodyRange.Value   ' 2次元配列、1始まり（[[performance-tips]]参照）

Dim priceCol As Long
priceCol = tbl.ListColumns("金額").Index   ' テーブル内での列番号（1始まり）

Dim r As Long
For r = LBound(data, 1) To UBound(data, 1)
    data(r, priceCol) = data(r, priceCol) * 1.1
Next r

tbl.DataBodyRange.Value = data
```

列を `Cells(r, 3)` のように絶対列番号で決め打ちしない。テーブルの列順は
ユーザーが自由に入れ替えられるため、`ListColumns("列名").Index` を都度
取得する（ループの外で1回だけ取得してキャッシュしておけば速度上も問題ない）。

## フィルタ（AutoFilter）とソート

```vb
tbl.Range.AutoFilter Field:=tbl.ListColumns("地域").Index, Criteria1:="関東"
```

`Field` はテーブル内での列番号（`ListColumns.Index`）であり、シート全体の
列番号ではない点に注意。フィルタ解除は `tbl.AutoFilter.ShowAllData`
（`tbl.Range.AutoFilter` を条件なしで再実行すると逆にフィルタ自体が解除される
ことがあるため、解除専用にはこちらを使う）。

```vb
If tbl.AutoFilter.FilterMode Then tbl.AutoFilter.ShowAllData
```

## テーブル名の取得と存在確認

シートをまたいで複数テーブルを扱う場合、ブック全体のテーブル一覧は
シートごとに `Worksheet.ListObjects` を走査する必要がある
（`Workbook.ListObjects` のようなブック直下の一括プロパティは無い）。

```vb
Function FindTable(wb As Workbook, tableName As String) As ListObject
    Dim ws As Worksheet, t As ListObject
    For Each ws In wb.Worksheets
        For Each t In ws.ListObjects
            If t.Name = tableName Then
                Set FindTable = t
                Exit Function
            End If
        Next t
    Next ws
    Set FindTable = Nothing
End Function
```

## ピボットテーブルの元データにする

```vb
Set pc = ThisWorkbook.PivotCaches.Create( _
    SourceType:=xlDatabase, _
    SourceData:="SalesTable")   ' テーブル名を文字列で渡す
```

セル範囲ではなくテーブル名を渡すと、行追加でテーブル範囲が自動拡張された際に
ピボット側の `SourceData` を書き換えずに `RefreshTable` だけで追従する
（詳細・注意点は [[pivot-tables]] 参照）。

## 構造化参照（数式内）とVBAの関係

シート上の数式で使う構造化参照（`=SUM(SalesTable[金額])` 等）はVBA側からは
直接の対応構文が無く、`tbl.ListColumns("金額").DataBodyRange` 等で同等の範囲を
取得してから `WorksheetFunction.Sum(...)` を呼ぶ形になる。数式文字列として
構造化参照をそのままセルに書き込む（`Range("C1").Formula = "=SUM(SalesTable[金額])"`）
ことは可能で、テーブル名が変わらない前提であればこちらの方が保守はしやすい。
