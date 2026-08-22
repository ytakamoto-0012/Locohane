# バージョン・プラットフォーム依存の注意点

社内配布・複数環境で動かすマクロは「自分の手元のExcelで動く」だけでは不十分で、
配布先のビット数・バージョン・OS（Windows/Mac）差を意識する必要がある。

## 32bit / 64bit（PtrSafe と LongPtr）

Windows API（`Declare`）を使うコードは、Office 2010以降で32bit/64bit両対応に
するなら `PtrSafe` と `LongPtr` が必須。

```vb
#If VBA7 Then
    Private Declare PtrSafe Function GetTickCount Lib "kernel32" () As LongPtr
#Else
    Private Declare Function GetTickCount Lib "kernel32" () As Long
#End If
```

- `VBA7`（VBEのバージョン。Office 2010以降で真）と `Win64`（実行環境が64bit
  Officeかどうか）は別条件。`PtrSafe`宣言自体は`VBA7`で必要、ポインタ型は
  `Win64`で`LongPtr`が実質8バイト・`Win32`側だと4バイトのLongと同義になる、
  という2段構えのコンパイル条件分岐になる。
- **古いWeb上のサンプルコード（`Declare Function`のみで`PtrSafe`が無いもの）は
  64bit Officeでコンパイルエラーになる。** ユーザーが古いコードを流用している
  場合、まずこれを疑う。
- 64bit Office自体は2010年以降存在するが、**Office 2010/2013/2016では既定で
  32bit版がインストールされ、Office 2019・Microsoft 365以降は既定が64bit版に
  変わった**という経緯がある。社内の配布先には両世代が混在しうるため、
  32bit/64bitどちらの環境かは実行時まで分からない前提で、両対応の条件分岐を
  書いておくのが安全。

## 新しい関数・機能が使えるバージョン

- **動的配列関数**（`FILTER`/`UNIQUE`/`SORT`/`SORTBY`/`XLOOKUP`等）は
  **買い切り版Excel 2019には存在しない**が、**Excel 2021（買い切り版）には
  Microsoft 365と同様に含まれている**（2021年の買い切り版から、それまで
  365限定だった主要な新関数が取り込まれる方針にMicrosoftが変更したため）。
  一方 **`LAMBDA`はExcel 2021には含まれず**、Microsoft 365、またはより新しい
  買い切り版のExcel 2024以降が必要（2026年時点の情報）。バージョンごとの
  対応表は流動的なので、配布先バージョンが古い可能性がある場合は都度
  最新情報を確認すること。`Application.WorksheetFunction.Filter`等を
  VBAから呼ぶコードは、対象バージョンに関数が無いと実行時エラー
  （`438`）になる。配布先のバージョンが不明な場合、これらの
  関数への依存は避けるか、事前にバージョン判定して代替ロジックに分岐する。
- `ListObject`（テーブル機能、[[excel-tables-listobject]]参照）は
  **Excel 2007以降**。`.xls`（97-2003形式）で開かれたブックにはテーブル
  機能自体が存在しない。
- リボンのカスタマイズは Excel 2007 で `CommandBars` から Ribbon XML
  （`customUI.xml`）ベースに刷新された。**Excel 2003以前をターゲットにした
  `CommandBars` の追加/削除コードは2007以降でも動作はするが非推奨のAPI**で、
  リボンUIそのものへの統合はできない。

## ファイル形式（拡張子）による制約

- `.xls`（97-2003形式）: 最大行数 65,536 行・最大列数 256 列。
  `.xlsx`/`.xlsm`（2007以降）は 1,048,576 行・16,384 列。これを超える
  範囲を扱うブックを `.xls` で保存しようとすると「互換性チェック」の
  警告ダイアログは表示されるが、見落として続行するとダイアログの外の
  行・列のデータが警告どおり削除される（保存自体はエラーにならず成功
  してしまう）ため、ダイアログの内容を確認せず`OK`を押す運用は避ける。
- マクロを含むブックは `.xlsm`（または `.xlsb`）で保存する必要がある。
  `.xlsx` で `SaveAs` しても**保存自体はエラーにならず成功する**が、
  「VBAプロジェクトを保存できません」という警告ダイアログが出た上で
  **VBAプロジェクトが失われた状態で`.xlsx`として保存される**（`.xlsm`側の
  元ファイルを残していない限りマクロは復元できない）。`Application.DisplayAlerts`
  を`False`にしていると警告ダイアログ自体が出ないままマクロが失われるため、
  マクロ入りブックを`.xlsx`として保存するコードは書かない、または
  `SaveAs` の `FileFormat` 引数を明示的に`.xlsm`系に指定して事故を防ぐ。

```vb
wb.SaveAs fileName, FileFormat:=xlOpenXMLWorkbookMacroEnabled  ' .xlsm
```

## 参照設定ライブラリのバージョン差（早期バインディング時）

[[common-gotchas]] で触れた早期バインディングの問題はバージョン差でも起きる。
「参照設定」で追加したライブラリ（例: `Microsoft Excel 16.0 Object Library`）は
Officeのバージョンによって番号が異なり、開発環境より古いバージョンのExcelで
そのファイルを開くと **`参照エラー`（コンパイルエラー）** になる。
配布用マクロは遅延バインディング（`CreateObject`/`Object`型）を優先する方針は
[[common-gotchas]] を参照。

## Windows と Mac の違い

Mac版Excelでも基本的なVBA構文は共通だが、以下は非互換または挙動が異なる:

- **Windows API（`kernel32`等のDLLを呼ぶ`Declare`）は使えない**（Mac側に
  同名のDLLが存在しないため）。Mac固有の処理が必要な場合はAppleScriptを
  呼び出す`AppleScriptTask`（Office 2016以降で推奨、外部の`.applescript`
  ファイルを`~/Library/Application Scripts/`配下に置いて実行する方式）を
  使う。旧来の`MacScript`関数は非推奨（deprecated）扱いなので新規コードでは
  避ける。
- `FileSystemObject`（`Scripting.FileSystemObject`）は**Mac未対応**。
  ファイル操作は `Dir`/`Open...For Input/Output` 等の組み込みステートメントや
  上記の`AppleScriptTask`経由のAppleScript連携を使う必要がある。
- パス区切り文字が異なる（Windows: `\`、Mac: `:` または `/`。
  `Application.PathSeparator` で実行環境の区切り文字を取得できるため、
  パス結合は決め打ちの`"\"`ではなくこれを使うと両対応しやすい）。
- `Shell` 関数・COMオートメーション（`CreateObject("Excel.Application")`等)は
  Mac版には無い（Macにはそもそも「COM」という概念自体が無い）。

Mac対応が必須要件かどうかは早い段階でユーザーに確認する（Windows専用の
社内マクロであれば上記は気にしなくてよいことが多いが、判断せず決め打ちしない）。

## バージョン判定の書き方

```vb
Select Case Val(Application.Version)
    Case Is >= 16   ' Excel 2016以降（365含む。2016系はメジャーバージョンが同じ16のため
                     ' これだけでは365の新関数の有無までは判定できない）
        ' ...
    Case Else
        ' 旧バージョン向けフォールバック
End Select
```

`Application.Version` はメジャーバージョン番号（"16.0"等）を返すのみで、
Microsoft 365かどうか・ビルド番号までは判別できない（Excel 2016以降は
2019・2021・365のいずれも"16.0"を返すため、バージョン番号だけでは
これらを区別できない）。**FILTER/LAMBDA等、対応バージョンが関数ごとに
異なる新しい関数の有無は、バージョン番号ではなく`On Error`で該当関数の
呼び出しを試みて失敗を検知する（機能検出）方が確実**なことが多い。

```vb
Dim hasFilter As Boolean
On Error Resume Next
Dim testResult As Variant
testResult = Application.WorksheetFunction.Filter(Array(1, 2), Array(True, False))
hasFilter = (Err.Number = 0)
On Error GoTo 0
```

## マクロセキュリティ設定・信頼できる場所

バージョンに関わらず、マクロ有効ブックはユーザー側のトラストセンター設定
（マクロの有効/無効、信頼できる場所）に実行が左右される。VBAコード側からは
この設定自体を変更できない（[[events-and-loops]]の`Workbook_Open`の項も参照）。
配布時は「信頼できる場所への配置」または「実行時の有効化手順」をユーザー
マニュアル側で案内する必要がある旨、コードの限界として伝えておく。
