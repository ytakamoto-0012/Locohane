# 他アプリ操作・ファイルI/Oの定石

## COMオブジェクトの解放とゾンビプロセス対策

`CreateObject("Excel.Application")` 等で別プロセスのアプリケーションを操作する
マクロは、オブジェクト変数を `Nothing` にし忘れる・操作順序を誤ると、
画面に何も表示されないまま `EXCEL.EXE` プロセスだけがバックグラウンドに
残り続ける（タスクマネージャーで確認しないと気づきにくい典型トラブル）。

```vb
Dim xlApp As Object, xlBook As Object
Set xlApp = CreateObject("Excel.Application")
xlApp.Visible = False

On Error GoTo CleanExit
Set xlBook = xlApp.Workbooks.Open("C:\data\target.xlsx")
' ... 操作 ...

CleanExit:
On Error Resume Next
If Not xlBook Is Nothing Then xlBook.Close SaveChanges:=False
xlApp.Quit
Set xlBook = Nothing
Set xlApp = Nothing
```

ポイント:
- **`Quit` を呼んだ後で必ず変数を `Nothing` にする**（順序が逆でも動くことが
  多いが、`Quit`せず`Nothing`だけにするとプロセスが残ることがあるため
  `Close`→`Quit`→`Nothing`の順を徹底する）。
- ネストした子オブジェクト（`Worksheets`/`Range`等）を一時変数に受けている場合、
  それらも使い終わったら `Nothing` にするのが安全（特にループ内で毎回
  取得する `Range`/`Cells` を変数に貯め込むと解放漏れの温床になる）。
- 例外発生時に `CleanExit` を通らないコードだと、エラーダイアログの裏で
  Excelプロセスが残り続ける。[[error-handling]] のCleanExitパターンを
  必ず適用する。

## FileSystemObject でのファイル操作

テキストファイルの読み書き・ファイル存在確認・フォルダ操作には
`Scripting.FileSystemObject`（遅延バインディング推奨、理由は
[[common-gotchas]] 参照）を使う。

```vb
Dim fso As Object
Set fso = CreateObject("Scripting.FileSystemObject")

If fso.FileExists(path) Then
    Dim ts As Object
    Set ts = fso.OpenTextFile(path, 1) ' 1 = ForReading
    Dim content As String
    content = ts.ReadAll
    ts.Close
End If
```

`OpenTextFile` の第4引数（Format）は `Tristate` 値で、省略時は `TristateFalse`（0）＝
ANSI（Shift-JIS環境ではShift-JIS）になり、UTF-8ではない点に注意。
`-1`（`TristateTrue`）を指定すると **UTF-16（Unicode）** として読み込まれるだけで、
**UTF-8を正しく読み込めるわけではない**（UTF-8ファイルにこの引数を使うと文字化けする）。
`FileSystemObject.OpenTextFile` にはUTF-8を直接扱う手段がないため、UTF-8のテキストを
読む場合は `ADODB.Stream`（`Charset = "UTF-8"` を明示できる）を使うのが確実。

## Workbooks.Open のダイアログ抑止

外部ファイルを開く際、リンク更新確認・形式変換確認等のダイアログが
マクロを止めてしまうことがある。バッチ処理では明示的に抑止する。

```vb
Application.DisplayAlerts = False
Set wb = Workbooks.Open(path, UpdateLinks:=0, ReadOnly:=True)
Application.DisplayAlerts = True
```

`DisplayAlerts` も `EnableEvents` と同様アプリケーション全体に効くグローバル
設定であり、必ず処理後に戻す（[[performance-tips]] の退避・復元パターン参照）。

## パスの扱い

文字列結合でパスを組み立てる場合、区切りの `\` の重複・欠落に注意する。

```vb
Dim fullPath As String
fullPath = folder & IIf(Right(folder, 1) = "\", "", "\") & fileName
```

UNCパス（`\\server\share\...`）やネットワークドライブは `FileSystemObject` の
一部メソッド（`GetAbsolutePathName` 等）で挙動が環境依存になることがあるため、
配布先の環境（ローカルドライブかネットワーク共有か）が不明な場合は
`Dir()` 関数での存在確認も併用すると安全。
