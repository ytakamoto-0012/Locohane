# ユーザーフォームのテクニック

## 表示方法: vbModal と vbModeless

```vb
UserForm1.Show vbModal      ' 既定。閉じるまで裏の操作をブロック
UserForm1.Show vbModeless   ' 非モーダル。表示したままシート操作を続けられる
```

非モーダルにすると、フォームを開いたままシートを触ってその変化をフォーム側で
拾う（進捗表示・リアルタイムプレビュー等）ことができるが、フォームを閉じずに
マクロが終了する経路があると、Excel終了時までフォームが残り続けることがあるため
`Unload` を確実に呼ぶ設計にする。

## 初期化データの受け渡し（Public プロパティ経由）

フォームモジュールに直接パラメータを渡す構文は無いため、`Public` プロパティ
または `Public` 変数をフォーム側に用意し、呼び出し元から `Show` の前にセットする。

```vb
' UserForm1 側
Public TargetSheetName As String

Private Sub UserForm_Initialize()
    Me.Caption = "対象: " & TargetSheetName
End Sub
```

```vb
' 呼び出し元
With New UserForm1
    .TargetSheetName = "Data"
    .Show vbModal
End With
```

`New UserForm1` で毎回新しいインスタンスを生成する書き方にすると、グローバルな
`UserForm1`（既定インスタンス）の状態が前回表示時のまま残る事故を避けられる
（同じフォームを複数回表示する処理で値が引き継がれてしまうバグの典型原因）。

## OK/キャンセルの結果を呼び出し元へ返す（キャンセルフラグパターン）

```vb
' UserForm1 側
Public Cancelled As Boolean

Private Sub btnOK_Click()
    Cancelled = False
    Me.Hide   ' Unloadではなく Hide（呼び出し元がプロパティを読めるように残す）
End Sub

Private Sub btnCancel_Click()
    Cancelled = True
    Me.Hide
End Sub

Private Sub UserForm_Initialize()
    Cancelled = True   ' 右上×ボタンで閉じられた場合の既定値
End Sub
```

```vb
' 呼び出し元
Dim frm As New UserForm1
frm.Show vbModal
If Not frm.Cancelled Then
    ' frm の入力値を使って処理
End If
Unload frm
```

ボタンクリック側で `Unload Me` してしまうと、`Show vbModal` の直後の行に
制御が戻った時点で既にフォームのコントロール値・プロパティが破棄されて
読み取れない。**結果を読み終わるまでは `Hide` に留め、呼び出し元が読み終わった
後で `Unload`** する。

## 右上×ボタン（QueryClose）のハンドリング

ユーザーがタイトルバーの×で閉じた場合も `Cancelled` を確定させたいときは
`QueryClose` イベントで拾う。

```vb
Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = vbFormControlMenu Then
        Cancelled = True
    End If
End Sub
```

## 入力検証はコントロールの Exit / フォームの OK 押下時の両方で

`TextBox` の `Exit` イベントで即時検証すると、ユーザーが値を空にしたまま
別のボタン（キャンセル等）を押した場合にも検証が走ってしまい体験が悪くなる
ことがある。**「その場で入力形式だけ軽くチェック」は `Exit`、「全項目そろって
いるかの最終確認」は OK ボタン押下時にまとめて行う**、と役割を分けるのが定石。

```vb
Private Sub txtAmount_Exit(ByVal Cancel As MSForms.ReturnBoolean)
    If Not IsNumeric(Me.txtAmount.Value) And Len(Me.txtAmount.Value) > 0 Then
        MsgBox "数値を入力してください"
        Cancel = True   ' フォーカスをこのコントロールに留める
    End If
End Sub
```

`Cancel = True` にするとフォーカス移動自体をキャンセルできる（ユーザーは
修正するまで次のコントロールへ移れない）。多用すると使い勝手を損なうため、
必須項目や致命的な形式違反のみに絞るのが無難。

## ListBox / ComboBox への一括データ投入

行ごとに `.AddItem` するより、`List` プロパティへ2次元配列を渡す方が速く
コードも短い（考え方は [[performance-tips]] の配列一括読み書きと同じ）。

```vb
Me.ListBox1.ColumnCount = 3
Me.ListBox1.List = Range("A2:C100").Value
```

行選択と対応データの紐付けが必要な場合、表示に使わない列も`ColumnCount`に
含めて `ColumnWidths` で幅0にして隠す（内部IDを持たせるテクニック）か、
`.List(選択行, 列)` で選択後にシート側の対応行を逆引きする設計にする。

## コントロールを動的に増やす（Controls.Add）

固定数のコントロールで足りない場合（可変件数の入力行等）は実行時に追加できる。

```vb
Dim ctl As MSForms.Control
Set ctl = Me.Controls.Add("Forms.TextBox.1", "txtDynamic" & i, True)
ctl.Top = baseTop + i * 20
ctl.Left = 10
ctl.Width = 100
```

動的コントロールのイベント（Click等）を拾いたい場合は、クラスモジュールで
`WithEvents` を使ったイベントハンドラのコレクションを別途用意する必要があり、
標準モジュールの単純なコードでは対応できない点に注意（複雑になりやすいため、
可変件数が数件程度ならフォーム自体を複数回表示する設計の方が単純なことも多い）。

## 既定インスタンスの状態が残るのは「Unloadを挟まなかった」場合（誤解に注意）

```vb
UserForm1.Show vbModal
Unload UserForm1
UserForm1.Show vbModal   ' Unloadを挟んでいるので TextBox1.Value 等は初期状態に戻る
```

`Unload` を挟むと既定インスタンスはメモリ上から破棄され、次に `UserForm1` を
参照した時点で新しいインスタンスが生成されて `UserForm_Initialize` が再度走る。
そのため上のコードでは、コントロール値も独自の `Public`/`Private` 変数も
**前回値を引き継がない**（初期状態にリセットされる）。「Unloadした後も値が
残る」というのはよくある誤解なので注意する。

本当に注意すべきなのは逆のケースで、**`Unload` を挟まずに `Hide` のまま
次の `Show` を呼ぶと、既定インスタンスは破棄されていないため前回の値が
そのまま残ってしまう**（前述の「初期化データの受け渡し」節で触れた事故の
典型パターン）。`New UserForm1` で都度新規インスタンス化するか、`Show` の
前に明示的なリセット処理を入れると挙動を予測しやすくなる。
