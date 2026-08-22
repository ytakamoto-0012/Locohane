# よくある落とし穴

## Range と Cells の使い分け・シート省略の罠

`Cells(i, j)` は列も行も数値で指定できるためループと相性が良いが、
`With` を使わずシート指定を省略すると**アクティブシートに対して実行される**。
複数シートを扱うマクロでこれが原因の誤爆（意図しないシートを書き換える）が多発する。

```vb
' 危険: どのシートに対する操作か省略時は不明瞭（アクティブシート依存）
Cells(1, 1).Value = "x"

' 安全: With でシートを明示
With ThisWorkbook.Worksheets("Data")
    .Cells(1, 1).Value = "x"
    .Range("B1:B10").ClearContents
End With
```

`.Cells` の手前のピリオドを書き忘れると `With` の効果が及ばずアクティブシートに
飛ぶ（コンパイルエラーにならないため気づきにくい）。

## Dim の暗黙 Variant とスコープ

```vb
Dim i, j As Long   ' i は Variant、j だけ Long（VBAはカンマ区切りで型指定が個別）
```

1行にまとめて宣言する場合、**型は変数ごとに書かないと先頭側が Variant になる**。
意図せず Variant のままループカウンタに使うと、型不一致のパフォーマンス低下や
比較演算での予期しない挙動につながる。

## 早期バインディング と 遅延バインディング（CreateObject）

- 早期バインディング: VBEの「参照設定」でライブラリ（例: `Microsoft Scripting Runtime`）
  を追加し `Dim fso As Scripting.FileSystemObject` のように型を直接書く。
  補完・型チェックが効くが、**配布先PCで同じライブラリのバージョン/インストール状況が
  異なると `参照エラー`（コンパイルエラー）になる**。
- 遅延バインディング: `Dim fso As Object` + `Set fso = CreateObject("Scripting.FileSystemObject")`。
  実行時にオブジェクトを解決するため配布先の参照設定に依存しないが、補完が効かず
  タイプミスに気づきにくい。

**社内配布するマクロは基本、遅延バインディングを使う**（実行環境のライブラリ有無を
制御できないため）。開発中だけ参照設定で早期バインディングし、完成後に
`CreateObject` へ置き換える運用も多い。

## 配列の下限（LBound）は常に0とは限らない

`Range.Value` で取得した配列は1始まり（[[performance-tips]] 参照）だが、
`Array(1,2,3)` や `Dim arr(5)` のような通常のVBA配列は**モジュール先頭の
`Option Base` 宣言（既定は0）**に依存する。`Option Base 1` が書かれたモジュールを
コピペで混在させると `LBound` の想定がずれてインデックスエラーの原因になる。
決め打ちでインデックスを書かず、必ず `LBound(arr)`〜`UBound(arr)` でループする。

## 文字列比較の大小文字・全半角

既定の `=` 比較は `Option Compare` 設定に依存する（既定は `Binary` で大文字小文字を
区別）。ユーザー入力やセル値の比較で大文字小文字を無視したい場合は
`StrComp(a, b, vbTextCompare) = 0` を使うか、モジュール先頭に
`Option Compare Text` を宣言する（モジュール単位で影響が及ぶ点に注意）。

## Range.Value と Range.Value2 と Range.Text の違い

- `.Value`: 表示形式を考慮した型付きの値（日付はDate型等）。既定でこれを使えばよい。
- `.Value2`: 日付や通貨をDouble等の生値で返す（Currency/Date型を経由しない分わずかに高速。
  大量セル読み取りの最適化余地として覚えておく）。
- `.Text`: セルに**表示されている文字列そのまま**（書式適用後）。値の取得目的では
  使わない（列幅不足で `###` になっている場合等、意図しない文字列を拾う）。

## 遅延評価されない Do While / Loop の無限ループ

条件変数をループ内で更新し忘れる典型ミスに加え、`Range.Find` を使ったループで
「最初に見つけたセルまで一周したら停止する」処理を書く際、開始位置の記録を
忘れると無限ループになりやすい。

```vb
Dim firstAddr As String
Set c = Range("A:A").Find("キー")
If Not c Is Nothing Then
    firstAddr = c.Address
    Do
        ' ... c を使った処理 ...
        Set c = Range("A:A").FindNext(c)
    Loop While Not c Is Nothing And c.Address <> firstAddr
End If
```
