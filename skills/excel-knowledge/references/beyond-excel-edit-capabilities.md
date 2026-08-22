# excel-editのopで表現できない要求に当たったとき

`excel-edit`の`add_chart`は`type`に`bar`/`line`/`pie`/`scatter`しか受け付けず、
**積み上げ（stacked）棒グラフや複合グラフのような細かいバリエーションには
非対応**。この制約に当たったときの対処を誤ると被害が大きい失敗につながった
実例がある。

## 悪い対処: 生openpyxlへバイパスしてxlsmを直接壊す

「積み上げ棒グラフ＋折れ線グラフ」を求められたセッションで、`add_chart`の
`type`に積み上げ指定が無いことには正しく気づいたものの、その後
`execute_python_code`で`openpyxl.load_workbook`→`wb.save()`という生openpyxl
操作に切り替え、20ターン超にわたって以下を繰り返した。

- `bar_chart.type = "stacked"`（openpyxlに存在しない属性値）
- `Series(value=...)`（正しくは`values=`。以降`chart.type`参照でAttributeError）
- `bar_chart.series[0]`を空リストに対して参照し`IndexError`

さらに、生openpyxlで`.xlsm`を直接`wb.save()`した結果、Excel COMで開けない
ファイル破損が発生した（`edit_excel.py`は`.xlsm`読込時に自動で
`keep_vba=(ext == ".xlsm")`を付けているが、生openpyxlでの`load_workbook`/
`save`を自前で書く場合はこれが保証されない。**マクロが失われる、または
ファイル形式が壊れて「Excel でファイル '...' を開くことができません。
ファイル形式またはファイル拡張子が正しくありません。」というエラーで
開けなくなるリスクがある**）。壊れたファイルはその後の復旧作業でも
[[edit-excel-invocation-contract]]の「edit_excel.pyの誤用」を誘発し、
被害がさらに広がった。同じ失敗類型の別実例（フォント名変更）と、
そこから迷走が長引いた際の抜け出し方は[[raw-openpyxl-xlsm-fallback]]参照。

## 推奨する対処の順序

1. まず`format_table`・`row_styles`・テーマ配色など、**既存opの組み合わせで
   近い見た目を再現できないか**を検討する（完全一致でなくても許容範囲か
   ユーザーに確認する余地がある）。
2. それでも表現できない場合は、無理に生openpyxlへ回避せず、
   「excel-editでは積み上げグラフのような細かい種類指定には対応していない」
   ことをユーザーに伝え、代替案（積み上げなしの通常の棒グラフにする、等）を
   提示してから進める（`add_image`のトリミング非対応時と同じ立ち位置。
   `excel-edit`のSKILL.md「画像・グラフの追加と調整」の禁止事項も参照）。
3. **どうしてもユーザーが生openpyxlでの直接編集を望む場合**は、
   `.xlsm`を対象にするなら`load_workbook(path, keep_vba=True)`を必ず明示し、
   保存前にバックアップ（`shutil.copy2`で別名保存）を取ってから上書きする。
   保存後は`excel-render`で開ける状態か画像確認し、可能なら実際に
   Excel COM等で開けることまで確認してから完了報告する。

## 一般原則

**「ツールのopで表現できない」と分かった時点を、生スクリプトへの
バイパスの合図にしない。** バイパスは正規のop以上に自由度が高い分、
壊れたときの被害（ファイル破損・マクロ消失）も大きい。まず制約を
ユーザーに伝えて期待値をすり合わせる方が、無言で20ターン試行錯誤して
ファイルを壊すより早く安全に着地する。
