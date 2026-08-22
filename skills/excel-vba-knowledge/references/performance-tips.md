# パフォーマンス最適化の定石

大量データ・多数セルを扱うマクロが遅い場合、まずここに挙げる定番対策から疑う。

## 1. 画面更新・自動計算・イベントを処理中だけ止める

```vb
Dim prevScreenUpdating As Boolean, prevCalc As XlCalculation, prevEvents As Boolean
prevScreenUpdating = Application.ScreenUpdating
prevCalc = Application.Calculation
prevEvents = Application.EnableEvents

Application.ScreenUpdating = False
Application.Calculation = xlCalculationManual
Application.EnableEvents = False

On Error GoTo Restore
' ... 本処理 ...

Restore:
Application.ScreenUpdating = prevScreenUpdating
Application.Calculation = prevCalc
Application.EnableEvents = prevEvents
If Err.Number <> 0 Then Err.Raise Err.Number, Err.Source, Err.Description
```

**必ず処理前の値を変数に退避してから戻す。** `xlCalculationAutomatic` に固定で
戻すと、元々手動計算モードだったブックで意図せず自動計算に変えてしまう副作用がある。
また `Restore` ラベルを必ず通す設計（[[error-handling]] のCleanExitパターン参照）に
しないと、エラー発生時に画面更新が止まったまま・イベントが無効なままユーザーへ
返ってしまう。

## 2. セルを1つずつ読み書きしない — 配列で一括転送

セルごとのループ（`Cells(i, j).Value = ...` を数千回）は非常に遅い。
`Range.Value` は2次元配列を一括で読み書きできるので、まず配列に読み込み、
配列上で処理し、最後に一括で書き戻す。

```vb
Dim data As Variant
data = Range("A1:D10000").Value   ' 1回の読み取りで2次元配列(1-based)へ

Dim i As Long
For i = LBound(data, 1) To UBound(data, 1)
    data(i, 4) = data(i, 2) * data(i, 3)  ' 配列上で計算（シートには触れない）
Next i

Range("A1:D10000").Value = data   ' 1回の書き込みで反映
```

体感で数十倍〜数百倍速くなることも珍しくない。`Range.Value` で読み込んだ配列は
**1始まり（1-based）** になる点に注意（通常のVBA配列は既定0始まり）。

## 3. Select / Activate を使わない

```vb
' 遅い・不安定（アクティブシート依存）
Sheets("Sheet1").Select
Range("A1").Select
Selection.Value = 1

' 速い・確実
Sheets("Sheet1").Range("A1").Value = 1
```

`Select`/`Activate` はシート切り替えの描画コストがかかる上、対象がアクティブシート・
アクティブブックに依存するため、複数ブック・複数シートをまたぐ処理では誤操作の
原因にもなる。オブジェクトを直接指定して操作する。

## 4. Findメソッドの検索範囲・オプションを明示する

`Range.Find` は前回の検索条件（`LookIn`/`LookAt`等）を引き継ぐ仕様があるため、
毎回オプションを明示しないと環境・実行順序によって挙動が変わることがある。

```vb
Set found = Range("A:A").Find(What:="キー", LookIn:=xlValues, LookAt:=xlWhole)
```

## 5. ステータスバーで進捗を出す場合は更新頻度を絞る

`Application.StatusBar` の更新自体にもコストがあるため、ループの毎回ではなく
「100件ごと」等、間引いて更新する。

## 6. Excel終了後もプロセスが残る問題（COM自動化時）

VBA単体の話ではないが、他のExcelインスタンスをCOMで操作するマクロ（ブックを
`CreateObject("Excel.Application")` で別プロセス起動する等）でパフォーマンス
劣化やゾンビプロセス化が起きる場合は [[com-automation-and-file-io]] を参照。
