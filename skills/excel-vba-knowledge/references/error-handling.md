# エラー処理の定石

## 基本パターン（後片付き保証つき）

VBAには `try/finally` が無いため、`On Error GoTo` + ラベル + `Exit Sub` の組み合わせで
「異常時も後片付け（オブジェクト解放・画面更新の復元等）を必ず通す」形にする。

```vb
Sub DoSomething()
    Dim wb As Workbook
    Dim prevScreenUpdating As Boolean

    On Error GoTo ErrHandler
    prevScreenUpdating = Application.ScreenUpdating
    Application.ScreenUpdating = False

    Set wb = Workbooks.Open("C:\data\input.xlsx")
    ' ... 本処理 ...

CleanExit:
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close SaveChanges:=False
    Application.ScreenUpdating = prevScreenUpdating
    Exit Sub

ErrHandler:
    MsgBox "エラー " & Err.Number & ": " & Err.Description, vbExclamation
    Resume CleanExit
End Sub
```

ポイント:
- `ErrHandler` から `Resume CleanExit` で正常系と同じ後片付けルートに合流させる。
  後片付けコードを正常系・異常系の2箇所に書かない（重複すると片方だけ直し忘れる事故が起きる）。
- 後片付け中（`CleanExit`ブロック）で再度エラーが起きても連鎖で落ちないよう、
  直前に `On Error Resume Next` を置く。

## `On Error Resume Next` は範囲を最小に絞る

「次の1行だけエラーを無視したい」場合に使うが、無視区間が広いと本来検知すべき
エラーまで握りつぶしてしまう。無視したい行の直後で必ず `On Error GoTo 0`
（またはハンドラへ戻す）に戻すこと。

```vb
On Error Resume Next
Set ws = ThisWorkbook.Worksheets("Sheet_NotExist")
On Error GoTo 0
If ws Is Nothing Then
    ' シートが無い場合の分岐
End If
```

シート存在確認・オブジェクトが Nothing になりうる判定など、
「無ければ Nothing のままにして後続で判定する」パターンでよく使う。

## Err オブジェクトはハンドラ内でのみ有効な情報を持つ

`Err.Number` / `Err.Description` はハンドラに入った直後に読み取る。
ハンドラ内で別の処理（別のSub呼び出し等）を挟むとその処理が新たにエラーを
起こした場合に上書きされるため、必要な値は最初に変数へ退避してから使う。

## エラー番号の主な使い分け

- `9`（インデックスが有効範囲にありません）: 存在しないシート名・配列範囲外アクセス。
- `1004`（アプリケーション定義またはオブジェクト定義のエラー）: Range操作の失敗
  （非表示シートへの書き込み、保護シートへの書き込み等）で頻出。原因はメッセージだけでは
  特定できないことが多く、直前の操作（どのRangeに何をしようとしたか）をコメントや
  ログで残しておくと切り分けが早い。
- `91`（オブジェクト変数または With ブロック変数が設定されていません）: `Set` し忘れ、
  または `Find` 系メソッドが見つからず `Nothing` を返した戻り値をそのまま使った場合。
  `Find` の戻り値は必ず `Is Nothing` で判定してから使う。

## カスタムエラーの送出

自作関数が異常を呼び出し元に伝える場合は `Err.Raise` を使う。番号は
`vbObjectError + 任意の正の数`（Excel/VBA予約範囲との衝突を避けるため）。

```vb
Err.Raise vbObjectError + 1001, "ValidateInput", "入力シートが見つかりません"
```
