---
name: excel-vba-knowledge
description: Excel VBAのコーディング作法・定石・よくある落とし穴について、Webや過去の解決実績から蓄積したローカルの知識ベース（references/配下のノート）を参照するスキル。ユーザーが「VBAでこう書きたい」「このVBAコードの書き方を教えて」「このエラー（On Error/1004/91等）の原因は」「VBAが遅い/固まる」「マクロを閉じてもExcelプロセスが残る」「ピボットテーブルをVBAで操作したい」「テーブル（ListObject）の行を追加/取得したい」「ユーザーフォームでの値の受け渡し/OK・キャンセルの作り方」「モジュールをどう分ければいいか」「32bit/64bit・xls/xlsm・Mac版で動くか」「マクロが実行できない/急に動かなくなった」「実行時エラーの原因を切り分けたい」「マクロが固まる・応答なしになる」など、VBAの実装方法・エラー原因・パフォーマンス・イベント処理・COM自動化・ピボットテーブル・テーブル操作・ユーザーフォーム・モジュール設計・バージョン/プラットフォーム互換性・トラブルシューティングの相談をしてきたときに使う。openpyxl/excel-edit/excel-render等、VBA以外のExcelファイル操作の落とし穴はexcel-knowledgeスキルの担当。excel-vba-read/excel-vba-editで実際のVBAコードを読み書きする前後の設計・デバッグ段階で使うことが多い。
license: MIT
metadata:
  author: ytakamoto
  version: "1.0"
---

# excel-vba-knowledge

Excel VBAのコーディング作法・定石・よくある落とし穴をまとめたローカル知識ベース。
スクリプトは持たず、`read_skill_file` で
`references/` 配下のノートを読むだけのスキル。

## 使い方

1. このSKILL.mdの本文（`read_skill` で既に読んでいる）にある下記「索引」から、
   相談内容に近いトピックを選ぶ。複数該当してもよい。
2. `read_skill_file` で該当ノートを読む。**`relative_path` には必ず
   `excel-vba-knowledge/references/<ファイル名>` のようにスキルフォルダ名を
   先頭に含めること**（`references/error-handling.md` のようにスキルフォルダ名を
   省略すると見つからない）。
3. ノート内に `[[別のノート名]]` という参照があれば、必要に応じてそちらも読む
   （例: `[[error-handling]]` は `references/error-handling.md` を指す）。
4. ノートの内容はそのままコピペ提示するのではなく、ユーザーの状況（対象のシート名・
   変数名・実際のエラー番号など）に当てはめて具体的なコード例として提示する。
5. 索引に該当するトピックが無い、またはノートを読んでも解決しない場合は、
   その旨をユーザーに伝えたうえで `web-search` スキルでの調査を検討する
   （このスキル自身はWeb検索を行わない。既知の定石をまとめたローカルの
   知識ベースであり、最新情報・個別事情に強いWeb検索とは役割が異なる）。
6. 実際にVBAコードを読む・書き換える作業は `excel-vba-read` / `excel-vba-edit`
   スキルの担当。このスキルは「書く前に定石を確認する」「エラーが出たときに
   原因の当たりを付ける」ための下調べに使う。

## 索引

| トピック | ファイル | 内容 |
|---|---|---|
| エラー処理 | `excel-vba-knowledge/references/error-handling.md` | On Error の定石、Err オブジェクト、エラー番号（9/1004/91等）の意味、Err.Raiseでの自作エラー送出 |
| パフォーマンス最適化 | `excel-vba-knowledge/references/performance-tips.md` | ScreenUpdating/Calculation/EnableEventsの一時停止、配列一括読み書き、Select/Activateを避ける、Findのオプション明示 |
| よくある落とし穴 | `excel-vba-knowledge/references/common-gotchas.md` | Range/Cellsとシート省略の罠、Dimの暗黙Variant、早期/遅延バインディング、配列のLBound、文字列比較、Value/Value2/Textの違い |
| イベントプロシージャ | `excel-vba-knowledge/references/events-and-loops.md` | Worksheet_Changeの無限ループ対策、Targetが複数セルの場合、Workbook_Open、EnableEventsの影響範囲 |
| 他アプリ操作・ファイルI/O | `excel-vba-knowledge/references/com-automation-and-file-io.md` | COMオブジェクト解放とゾンビプロセス対策、FileSystemObject、Workbooks.Openのダイアログ抑止、パスの扱い |
| ピボットテーブル | `excel-vba-knowledge/references/pivot-tables.md` | PivotCache/PivotTableの階層、フィールド配置、RefreshTable、PivotItemsのVisible切り替え、ManualUpdateでの高速化 |
| テーブル（ListObject） | `excel-vba-knowledge/references/excel-tables-listobject.md` | DataBodyRangeが空時にNothingになる罠、ListRows.Add、列名からのIndex解決、AutoFilter、ピボットの元データにする方法 |
| ユーザーフォーム | `excel-vba-knowledge/references/userforms.md` | vbModal/vbModeless、Publicプロパティでの値受け渡し、OK/キャンセルのHide/Unloadパターン、QueryClose、ListBoxへの一括投入、動的コントロール |
| モジュールの分け方 | `excel-vba-knowledge/references/module-organization.md` | 標準/クラス/シート/フォームモジュールの役割分担、イベントハンドラは委譲のみに留める、クラスモジュールを使うべき場面、WithEvents、循環依存の回避 |
| バージョン・プラットフォーム差 | `excel-vba-knowledge/references/version-and-platform-differences.md` | 32bit/64bit（PtrSafe/LongPtr）、365限定の動的配列関数、.xls/.xlsm/.xlsbの制約、参照設定のバージョン差、Windows/Mac非互換（FileSystemObject/Shell/COM不可）、バージョン判定・機能検出の書き方 |
| トラブルシューティング（症状別診断） | `excel-vba-knowledge/references/troubleshooting.md` | マクロが実行できない/コンパイルエラー/実行時エラー番号からの原因の絞り込み、急に動かなくなった場合の切り分け、固まる・応答なし、文字化け、Debug.Print/ブレークポイント等のデバッグ手順 |

## ノートの追加・更新について（開発者向け）

このスキルの `references/` は、実際に解決したVBAの問題やWeb調査で得た知見を
**開発者（人間、またはこのプロジェクトを保守するClaude Code）が事前に**
書き足していく運用を想定している。

Locohaneの実行時LLM（このスキルを使う本体）は、スクリプト実行系ツールの
書き込み先が作業ディレクトリ配下に限定されるサンドボックス制約があり、
`skills/` 配下（このスキル自身のフォルダ）へはチャット実行中に書き込めない。そのため、**チャット中に得た新しい知見をこの
知識ベースへ自動で書き戻す機能は無い**。会話の中で有用な新知見が得られた場合は、
ユーザーまたは開発者に「このノウハウを`excel-vba-knowledge`に追記してよいか」を
確認し、後日（アプリの外から）ファイルを追加する運用にすること。

新しいノートを追加する手順:

1. `references/<トピックを表す短い英語スラッグ>.md` を作成し、既存ノートと同じ
   Markdown形式（見出し・コード例・関連ノートへの`[[名前]]`リンク）で書く。
2. 上記「索引」の表に1行追加する（トピック名・ファイルパス・内容の要約）。
3. アプリ再起動は不要（`read_skill_file` はファイルをそのつど読むため）。
   ただし索引に載っていないノートはLLMから見つけてもらえないので、
   必ず索引への追記とセットで行う。
