# モジュールの効果的な分け方

VBAプロジェクトが育つと「1つの標準モジュールに全Sub/Functionを書き続ける」
状態になりがちで、他人（将来の自分含む）が読めなくなる。モジュール種別ごとの
役割を意識して分けると、[[excel-vba-read]] で読む際にも該当箇所を探しやすくなる。

## モジュール種別と置くべきコードの目安

| 種別 | 置くべきコード | 置くべきでないコード |
|---|---|---|
| `ThisWorkbook`（ブックモジュール） | `Workbook_Open`/`Workbook_BeforeClose`等のブックイベントのみ | 汎用処理（標準モジュールへ委譲する） |
| シートモジュール（`Sheet1`等） | そのシート固有の `Worksheet_Change`/`Worksheet_SelectionChange`等のイベントのみ | 他シートでも使う汎用ロジック |
| 標準モジュール | 業務ロジック本体、ボタンから呼ぶマクロ、ヘルパー関数 | フォーム固有のUI制御コード |
| クラスモジュール | 状態とふるまいをまとめたオブジェクト（後述） | エントリーポイント（`Sub Main`のような開始点） |
| フォームモジュール | そのフォームのコントロールイベントのみ | フォームを介さない純粋な計算ロジック（標準/クラスへ切り出す） |

**イベントハンドラ（`ThisWorkbook`/シートモジュール/フォームモジュール）は
「受け取ったら即座に標準モジュールの処理を1行呼ぶだけ」に留める**のが実務上の
定石。イベントモジュール内にロジックを書き込むと、同じ処理を別の場所
（ボタンクリック、他のイベント等）から再利用したくなった時に重複コピーが
発生しやすい。

```vb
' Sheet1（シートモジュール）
Private Sub Worksheet_Change(ByVal Target As Range)
    modDataValidation.HandleSheetChange Me, Target   ' 標準モジュールへ委譲
End Sub
```

## 標準モジュールの分割単位

機能領域（ドメイン）ごとに分けるのが基本。ファイル/データ入出力・集計計算・
UI制御・定数定義のように**責務で分ける**と、[[excel-vba-read]]でモジュール
一覧を見ただけで目的のコードの見当がつけやすい。

- `modConstants`: `Public Const` の集約（マジックナンバー・固定文字列を1箇所に）
- `modIO`: ファイル読み書き・外部接続（[[com-automation-and-file-io]]参照のコード）
- `modCalc`: 集計・計算ロジック（副作用なし、引数を受け取り値を返す関数中心）
- `modUI`: メッセージ表示・フォーム呼び出しの窓口
- `modMain`（または `modEntry`）: ボタンに紐付ける「入口」だけを集めたモジュール
  （中身は他モジュールの呼び出しのみで、実処理は書かない）

命名は `mod` プレフィックスに限らずプロジェクトの慣習に合わせてよいが、
**「このマクロがどこにあるか」をモジュール名から推測できる一貫した命名規則**
を保つことが重要（標準モジュールが増えるほど効いてくる）。

## クラスモジュールを使うべき場面

VBAは`Type`（ユーザー定義型）でもデータをまとめられるが、以下のような場合は
クラスモジュールの方が保守しやすい:

- **同じ構造のデータが配列やコレクションで複数存在し、それぞれにふるまい
  （メソッド）も必要**な場合（例: 複数の「注文」を扱い、各注文が
  `CalcTotal()` のような自分自身の計算を持つ）。
- **状態を隠蔽したい**場合。標準モジュールの `Public` 変数はプロジェクト内
  どこからでも書き換えられてしまうが、クラスの `Private` 変数は
  `Property Get`/`Property Let` を介してのみアクセスさせられる。

```vb
' クラスモジュール "COrder"
Private mAmount As Currency
Private mQty As Long

Public Property Get Amount() As Currency
    Amount = mAmount
End Property

Public Property Let Amount(v As Currency)
    If v < 0 Then Err.Raise vbObjectError + 1, , "金額は0以上である必要があります"
    mAmount = v
End Property

Public Function Total() As Currency
    Total = mAmount * mQty
End Function
```

`Property Let` 内でバリデーションを一元化できるため、「金額に負数が
セットされる」ような不正状態が発生する箇所をクラス定義の1箇所に閉じ込め
られる（標準モジュールの `Public` 変数直接代入だと、代入している全箇所を
探して検証コードを埋め込む必要が出てくる）。

## WithEvents によるイベント監視の集約

複数のコントロール・複数のオブジェクトのイベントをまとめて扱いたい場合
（[[userforms]]の動的コントロール等）はクラスモジュールに `WithEvents` で
宣言し、標準モジュール側からは意識せず済むようにする。

```vb
' クラスモジュール "CButtonHandler"
Public WithEvents Btn As MSForms.CommandButton

Private Sub Btn_Click()
    MsgBox Btn.Name & " が押されました"
End Sub
```

## 循環参照・相互依存を避ける

`modA` が `modB` の関数を呼び、`modB` も `modA` の関数を呼ぶという相互依存は
デバッグ時にどちらが起点か追いにくくなる。**依存の向きを一方向に揃える**
（例: `modMain` → `modUI` → `modCalc`/`modIO` の順に呼ばれる一方向の階層とし、
下位モジュールが上位モジュールを呼び返さない）ことを意識すると見通しが良い。

## モジュールを分けすぎた場合の弊害

過度に細分化する（1関数1モジュール等）と、[[excel-vba-read]]で一覧を
確認する際にモジュール数が多すぎて逆に見通しが悪くなる。「同じ理由で
一緒に変更されることが多いコード」をまとめる、くらいの粒度が実務上は
扱いやすい。迷ったら上記の種別テーブル（IO/Calc/UI/Constants/Main）程度の
粒度から始め、実際に肥大化したモジュールが出てきたら分割を検討する。
