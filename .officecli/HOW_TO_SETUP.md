# .officecli/ のセットアップ手順

`.officecli/` は Locohane のリポジトリには含まれない（`.gitignore` 対象）。サードパーティ製の
[iOfficeAI/OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) から手動で取得して配置している。
別環境で作り直す場合や officecli を更新する場合は、この手順に従うこと。

## 現在の構成

```
.officecli/
  bin/
    officecli.exe          # officecliバイナリ本体 バージョン毎にファイル名が変わるのでファイル名 officecli.exeに変更する。
  skills/                  # 公式リポジトリの skills/ ディレクトリをコピーしたもの
    officecli/SKILL.md      # ← 罠あり。下記「よくある罠」参照
    officecli-xlsx/SKILL.md
    officecli-docx/SKILL.md
    officecli-pptx/SKILL.md
    ...
    README_ja.md            # 公式リポジトリのREADME_ja.md
    SECURITY.md / LICENSE / NOTICE / THIRD-PARTY-NOTICES.txt
```

`config.ini` の `[paths] bin_path` に `./.officecli/bin` を含めることで、`execute_python_code`/
`run_script` が起動する子プロセスの `PATH` に自動で追加され、`officecli` コマンドが解決できる。

## よくある罠: `skills/officecli/SKILL.md` の欠落

公式リポジトリでは `skills/officecli/SKILL.md` は、**リポジトリルート直下の `SKILL.md`**
（officecliの共通ルール・コマンドリファレンス本体、約400行強）への**シンボリックリンク**になっている。

`skills/` ディレクトリだけをコピーする（zip展開やファイルコピーなど、シンボリックリンクを
解決しない方法で取得する）と、リンク先のルート `SKILL.md` が付いてこない。Windows環境では
特にこれが起きやすく、`skills/officecli/SKILL.md` の中身が `../../SKILL.md` という**文字列だけの
14バイトのテキストファイル**になって残ってしまう（見た目はファイルが存在するので気づきにくい）。

**2026-08-01 時点で実際にこの状態になっていたため、リポジトリルートの `SKILL.md` を
`https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/main/SKILL.md` から取得し、
`skills/officecli/SKILL.md` の中身として実体で置き換えて復旧した。**

## 正しいセットアップ手順（チェックリスト）

1. 公式リポジトリの `skills/` ディレクトリを丸ごとコピーする。
2. リポジトリルート直下の `SKILL.md` を取得する:
   - `https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/main/SKILL.md`
   - または公式短縮URL `https://officecli.ai/SKILL.md`
3. 取得した内容を `skills/officecli/SKILL.md` の中身として**実体保存**する
   （シンボリックリンクの再作成はWindows開発者モード等の環境依存要素が絡み壊れやすいため、
   実体コピーを推奨）。
4. `skills/officecli/SKILL.md` を開き、中身が本当にMarkdown本文（数百行）であることを
   目視確認する。数十バイトしかない・`../../SKILL.md` のような文字列しか無い場合はコピー漏れ。
5. バイナリ本体を [Releases](https://github.com/iOfficeAI/OfficeCLI/releases) から取得し、
   `.officecli/bin/` に配置する。
   - Windows (x64) の場合、最新リリースのAssetsに含まれるファイル名は
     `officecli-win-x64.exe`（ARM64版Windowsなら `officecli-win-arm64.exe`）であり、
     **`officecli.exe` という名前では配布されていない**。
     `https://github.com/iOfficeAI/OfficeCLI/releases/latest/download/officecli-win-x64.exe`
     （常に最新版を指す固定URL）からダウンロードできる。
   - ダウンロードしたファイルを **`.officecli/bin/officecli.exe` にリネームして配置する**こと。
     `config.ini [paths] bin_path` でPATHへ追加されるのはディレクトリのみで、
     Locohane・officecli-*スキルの記述はコマンド名 `officecli` を前提にしているため、
     `officecli-win-x64.exe` のままだと `officecli` コマンドとして解決できない。
   - macOS/Linuxの場合も同様に、リリースのファイル名（`officecli-mac-x64` /
     `officecli-mac-arm64` / `officecli-linux-x64` 等、拡張子なし）を
     `officecli`（拡張子なし）にリネームし、実行権限を付与する
     （`chmod +x .officecli/bin/officecli`）。
6. `config.ini` の `[paths] bin_path` に `./.officecli/bin` が含まれていることを確認する。
7. `officecli --version` が通ることを確認する（PATHが正しく通っているかの最終確認）。
   Locohane経由で確認する場合は、`execute_python_code` で以下を実行させてもよい:
   ```python
   import subprocess
   print(subprocess.run(["officecli", "--version"], capture_output=True, text=True).stdout)
   ```

## 今後のバージョン更新時の注意

officecli のバージョンを更新する際は、`skills/` 配下の子スキル（`officecli-xlsx` 等）だけでなく
**ルートの `SKILL.md`（＝ `skills/officecli/SKILL.md`）も忘れずに更新すること**。片方だけ更新すると
共通ルールと個別スキルの内容が食い違う可能性がある。

## 関連ドキュメント

- `execute_python_code`（Pythonのみ、bash/シェルが無い）から officecli を呼び出す具体的な方法は、
  Locohane側の独自スキル `officecli-python-bridge`（`skills/officecli-python-bridge/SKILL.md`）
  にまとめてある。officecli-* スキルを使う際はこちらも参照すること。
