# 独自スキル群をMCPサーバーとして配布する方法

`skills/`（Agent Skills仕様準拠、詳細は [`skills/SKILLS_README.md`](skills/SKILLS_README.md)）に
溜めたSKILL.md群を、他のMCPクライアント（Claude Code、Claude Desktop、Cursor等）から
発見・取得できるようにする方法をまとめる。

**注意: これは「MCPサーバーへ接続する」機能（`README.md` の「MCPサーバー接続」節、
`src/mcp_client.py`）とは逆方向の話。** そちらはLocohaneが外部MCPサーバーの
ツールを**使う側**（クライアント）。このドキュメントはLocohaneのスキルを
外部へ**配る側**（サーバー）にする方法であり、現状Locohaneにこの機能の実装は無い。

---

## 1. MCPの基礎（前提知識）

MCP（Model Context Protocol）は通信規格（JSON-RPCベース）に過ぎず、中身は自由に設計できる。
サーバーが公開できる部品は3種類:

| 部品 | 何をLLMに渡すか | 例 |
|---|---|---|
| Tools | 呼び出し可能な関数（LLMが実行を指示し、結果が返る） | DB検索、API呼び出し、スクリプト実行 |
| Resources | 読み込めるデータ（受け取った側が中身を読むだけ） | ファイル内容、ドキュメント |
| Prompts | 定型の指示テンプレート | — |

今回やりたいのは **SKILL.mdをそのまま「読めるデータ」として配る（Resources方式）**。
Tools方式（スキルの実処理をMCPの関数として直接呼び出せるようにする）は別物で、
`scripts/`配下のロジックを1入力1出力の関数として書き起こす作業が別途必要になる
（本ドキュメントの対象外）。

Resources方式の性質上、**MCPサーバー自身はSKILL.mdのテキストを右から左に渡すだけで、
実際にスクリプトを実行するのは受け取った側のAIエージェント**である。したがって
配布先の環境にも、そのスキルが前提とする実行環境（Python、`officecli.exe`等）が
揃っている必要がある（6節参照）。

---

## 2. なぜFastMCPのSkills Providerが使えるか

**FastMCPはAnthropic公式ではない。** 公式なのはMCPのプロトコル仕様
（[modelcontextprotocol.io](https://modelcontextprotocol.io/specification)）と、
それを素朴に実装した公式SDK（PyPI: `mcp`。Locohaneが`src/mcp_client.py`で
クライアントとして使っているのもこれ）まで。[FastMCP](https://gofastmcp.com/)
（PyPI: `fastmcp`）は公式SDKの上に被さるサードパーティ製ラッパーだが、
Python製MCPサーバー実装のデファクト標準として広く使われており、一部機能は
公式SDK側にも逆輸入された経緯がある。

FastMCP 3.0（2026年1月リリース）で追加された`SkillsDirectoryProvider`は
FastMCP独自の付加機能（公式`mcp`パッケージ単体には無い）で、ディレクトリを
渡すだけで配下の全`SKILL.md`を自動スキャンし、MCPリソースとして公開してくれる。

Locohaneの`skills/<name>/SKILL.md`（frontmatter必須、Agent Skills仕様準拠、
`skills/SKILLS_README.md` 6節）はこのプロバイダーがそのまま前提とする形式であり、
**新たにコードを書き起こす必要がない**。これが1節で「配布方式」を選ぶ最大の理由。

### 2-1. 他のMCPサーバー実装ライブラリという選択肢

MCPは仕様が公開されているだけなので、実装ライブラリは言語ごとに複数ある。
Python限定でも以下のような選択肢があり、FastMCP一強というわけではない。

| ライブラリ | 位置づけ |
|---|---|
| 公式SDK（`mcp`パッケージ） | Anthropic公式。プロトコルの生実装。Locohaneが`src/mcp_client.py`で使っているのもこれ |
| **FastMCP**（PrefectHQ） | 非公式だが事実上の標準。全MCPサーバー実装の約7割がベースに使っているとされる |
| FastAPI-MCP | 既存のFastAPIアプリをそのままMCPサーバー化したい場合向け |

FastMCPの「デコレータで書ける高レベルAPI」自体の設計思想は2024年に公式Python SDKにも
取り込まれているが、**本ドキュメントで使う`SkillsDirectoryProvider`はFastMCP独自の
付加機能であり、公式`mcp`パッケージ単体には無い**。今回FastMCPを選ぶのは、この
Skills Provider機能がSKILL.md配布という目的に対して実装コストを最小化できるため。
Locohaneは既にChainlit内でFastAPIを部分的に使っているため、将来「配布だけでなく
本格的にツールも公開したい」となった場合はFastAPI-MCPも比較対象になりうる。

---

## 3. 実装方法（動作確認済み）

以下はWeb調査だけの案ではなく、実際にLocohaneの実行環境
（`C:\DT_Python\Python311\env_local_agent_system\Scripts\python.exe`）に
`fastmcp==3.4.7`をインストールし、`skills/`（24スキル、131ファイル）に対して
実行して動作確認したコード。プロジェクト直下に `mcp_skills_server.py` として
実在する（そのまま `python mcp_skills_server.py` で起動できる）。

### 3-1. 依存追加

```
pip install fastmcp==3.4.7
```

`requirements.txt` には追記済み（`fastmcp==3.4.7`。プロジェクト直下
`CLAUDE.md` の「新しいpipライブラリのインストールが必要な時は、必ず
requirements.txtを更新する」ルールに従った）。

### 3-2. サーバー本体（.env等の機密ファイル除外つき）

**重要な前提: `SkillsDirectoryProvider`が使う`scan_skill_files()`は
`skill_dir.rglob("*")`でスキルフォルダ配下を無条件かつ再帰的に全スキャンする
（FastMCP 3.4.7のソース `fastmcp/server/providers/skills/_common.py` で確認済み）。
拡張子フィルタ・隠しファイル除外・`.gitignore`的な仕組みは一切無い。**
Locohaneの`web-search`スキルは`scripts/.env`にTAVILY_API_KEYを置く設計
（`skills/SKILLS_README.md`）のため、`skills/`をそのまま`roots`に渡すと
**APIキーがそのまま配布されてしまう**。これを避けるため、配布直前に
機密ファイルを除いた一時コピーを作ってから`roots`に渡す。

`mcp_skills_server.py`（プロジェクト直下、実在するファイル）:

```python
"""Locohaneの skills/ 配下のスキルを、MCPリソース（配布方式）として公開するサーバー。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS_SRC = PROJECT_ROOT / "skills"

# 配布対象から除外するファイル名／ディレクトリ名。
# .env系: APIキー等の機密情報を含みうるもの（新しいスキルに追加した場合はここにも追記すること）。
# __pycache__: 配布に無意味なPythonコンパイルキャッシュ。
EXCLUDE_NAMES = (".env", ".env.local", ".env.*", "__pycache__")


def build_publish_dir() -> Path:
    """機密ファイルを除いた配布用コピーを一時ディレクトリに作成して返す。"""
    publish_dir = Path(tempfile.mkdtemp(prefix="locohane_mcp_skills_"))
    for skill_dir in sorted(SKILLS_SRC.iterdir()):
        if not skill_dir.is_dir():
            continue
        shutil.copytree(
            skill_dir,
            publish_dir / skill_dir.name,
            ignore=shutil.ignore_patterns(*EXCLUDE_NAMES),
        )
    return publish_dir


mcp = FastMCP("locohane-skills")
mcp.add_provider(
    SkillsDirectoryProvider(
        roots=build_publish_dir(),
        supporting_files="resources",
    )
)


if __name__ == "__main__":
    mcp.run()
```

`supporting_files`は"template"（既定）と"resources"の2択だが、ソースコード
（`skill_provider.py`）を読むと**どちらでも`_manifest`（全ファイルのパス・
サイズ・ハッシュ一覧、JSON）は無条件で公開される**。違いは`list_resources()`
に個別ファイルが最初から並ぶかどうかだけで、"template"でも`_manifest`で
パスさえ知れば`skill://{name}/{path}`のワイルドカードURI経由で個別ファイルを
読めてしまう。**つまり"template"は一覧を隠すだけで、アクセス制御にはならない。**
機密ファイル対策は`supporting_files`の値ではなく、上記のような配布前フィルタで
行う必要がある。

公開されるリソースURIの形式（例: `pdf-tools`スキルの場合）:

```
skill://pdf-tools/SKILL.md
skill://pdf-tools/_manifest
skill://pdf-tools/references/notes.md
skill://pdf-tools/scripts/render_pdf_pages.py
```

**注意: これで配られるのは`scripts/xxx.py`の"ソースコード（テキスト）"であり、
実行結果ではない。** MCPサーバー自身はコードを一切実行しない。受け取った側の
クライアントが中身をファイルへ保存し、自分の実行環境で`python xxx.py`を
実行して初めて動く（1節参照）。「MCPサーバーがその場でスクリプトを実行して
結果を返す」ようにしたい場合は、`@mcp.tool()`で別途ラップする必要があり、
それは1節でいうTools方式（本ドキュメントの対象外）になる。

### 3-3. サーバーとしての実行方法

**単体起動**（プロジェクト直下で）:

```
C:\DT_Python\Python311\env_local_agent_system\Scripts\python.exe mcp_skills_server.py
```

実行するとstdioトランスポートで待ち受け続けるフォアグラウンドプロセスが
起動する（標準入出力でJSON-RPCをやり取りするため、ターミナルに文字が
流れているように見えるのが正常。ログはstderrに出る）。**このプロセス自体を
単独で終了させたい場合はCtrl+Cで止める。** ターミナルを閉じずに待機させて
おくか、クライアント側の設定（4節）から自動起動させる運用が基本になる。

**クライアント側からの自動起動（通常の使い方）**: MCPのstdioトランスポートは
「クライアントがサーバーをサブプロセスとして起動する」設計のため、通常は
上記コマンドを手動で実行しっぱなしにする必要はない。4節の
`claude mcp add`やClaude Desktopの設定ファイルに`command`/`args`として
`python mcp_skills_server.py`を登録しておけば、クライアント（Claude Code /
Claude Desktop）が接続時に自動でこのプロセスを起動し、切断時に終了させる。

**実際に別プロセスとして起動し、stdio経由で接続できることを確認済み**
（プロジェクト直下で以下を実行。`Client`にファイルパスを渡すとFastMCPが
自動的に`python mcp_skills_server.py`をサブプロセス起動して接続する）:

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("mcp_skills_server.py") as client:
        print(await client.ping())                       # -> True
        print(len(await client.list_resources()))         # -> 114

asyncio.run(main())
```

確認結果: `ping()`が`True`を返し、別プロセスとして起動した
`mcp_skills_server.py`から114件のリソースが取得できることを確認した
（起動時にFastMCPのバナーとログがstderrへ出力される）。

### 3-4. in-memoryでの動作確認（実施済み）

FastMCPの`Client`はサーバーオブジェクトをそのまま渡すとin-memoryで接続できる
（サブプロセス起動もネットワークも不要、単体テスト向け）。以下を実際に実行して確認した:

```python
import asyncio
from fastmcp import Client
from mcp_skills_server import mcp

async def main():
    async with Client(mcp) as client:
        resources = await client.list_resources()
        print(len(resources))  # -> 114

asyncio.run(main())
```

確認結果:

- 除外処理なしでは131件、`.env`/`.env.local`/`__pycache__`除外後は**114件**の
  リソースが公開される。
- `client.list_resources()`の結果に`.env`系ファイルが**含まれないこと**を確認済み
  （`ENV_LEAKS=[]`）。
- `client.read_resource("skill://pdf-tools/SKILL.md")`でSKILL.mdの本文
  （frontmatter含む）がUTF-8のテキストとして正しく取得できることを確認済み。

コマンドラインからMCP Inspector（ブラウザのデバッグUI）で確認したい場合:

```
fastmcp dev mcp_skills_server.py
```

（この方法自体は今回未実施。上記のin-memory Client方式で同等の検証は完了している）

---

## 4. クライアント側の接続設定

### Claude Code

```
claude mcp add --transport stdio locohane-skills -- python mcp_skills_server.py
```

Windowsでは `--` を含む形だと引数のパス変換で崩れる既知の不具合があるため、
`claude mcp add-json` でJSONを直接渡す方式の方が安全（Anthropic公式ドキュメント）。

### Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json` に追記:

```jsonc
{
  "mcpServers": {
    "locohane-skills": {
      "command": "python",
      "args": ["C:\\DT_Python\\Locohane\\mcp_skills_server.py"]
    }
  }
}
```

設定後はClaude Desktopを完全に再起動する。

### プログラムから直接取得する場合

```python
from pathlib import Path
from fastmcp import Client
from fastmcp.utilities.skills import list_skills, sync_skills
from mcp_skills_server import mcp

async with Client(mcp) as client:
    skills = await list_skills(client)          # -> 20件（office_shared共通モジュール置き場はSKILL.mdが無く対象外）
    await sync_skills(client, Path("./downloaded_skills"))
```

**Windowsでの既知の制約（実際に発生・回避確認済み）**: `sync_skills`/`download_skill`
（`fastmcp/utilities/skills.py`）は内部で`file_path.write_text(content.text)`を
encoding指定なしで呼んでおり、Windows既定のcp932では日本語（Locohaneの
SKILL.mdは全て日本語）を含むファイルの保存時に`UnicodeEncodeError`で
落ちる。回避するには実行前に`PYTHONUTF8=1`環境変数を設定する:

```
set PYTHONUTF8=1
python your_sync_script.py
```

---

## 5. 既存のMCPサーバー接続（クライアント側）との違い

| | クライアント側（既存, `src/mcp_client.py`） | サーバー側（本ドキュメント） |
|---|---|---|
| 役割 | 外部MCPサーバーのツールをLocohaneが**使う** | Locohaneのスキルを外部が**使う** |
| 設定ファイル | `.locohane/settings.json` | なし（`mcp_skills_server.py`が実体） |
| 実装状況 | 実装済み（未検証） | 実装済み・動作確認済み（`mcp_skills_server.py`、114リソース公開・`.env`非公開・SKILL.md読込を確認） |

両者は独立しており、どちらか一方を実装してももう一方には影響しない。

---

## 6. 制約・注意点

- **配布されるのはテキスト（SKILL.mdおよび`supporting_files="resources"`時は
  `scripts/`配下の`.py`ソースコードを含む全ファイル）であり、実行環境そのものではない**。
  `officecli`のような外部CLIツール本体は配らない。受け取った側が自前でPython・
  `officecli.exe`等の実行環境を用意できていなければ、スクリプトのソースコードを
  受け取っても実行できない。
- Locohaneのスキルの多くは「`python <script>.py <args...>` の形式で呼び出す」
  という前提でSKILL.mdが書かれている（`skills/SKILLS_README.md` 4-0節）。これは
  可搬性を意識した設計であり、`supporting_files="resources"`で`scripts/`一式が
  丸ごと配られるため、受け取り側は取得したファイルをそのまま同じ相対配置で
  保存すれば動く可能性が高い。
- `excel-vba-edit`のようにWindows COM（Excel本体）に依存するスキルは、
  受け取り側もWindows＋Office環境でなければ実行できない。
- `web-search`スキルの`scripts/.env`（TAVILY_API_KEY）は`mcp_skills_server.py`の
  `EXCLUDE_NAMES`で配布対象から除外済み（3-2節、動作確認済み）。受け取り側は
  `web-search`スキル自体は使えるが、TAVILY_API_KEYは別途自分で用意する必要がある。
  新しいスキルに`.env`系ファイルを追加した場合は`EXCLUDE_NAMES`への追記を忘れないこと。
- `sync_skills`/`download_skill`（FastMCP側のクライアントユーティリティ）は
  Windows環境で日本語ファイルの保存時に`UnicodeEncodeError`になる既知の制約が
  ある（4節参照、`PYTHONUTF8=1`で回避）。自前で`client.read_resource()`を呼んで
  `encoding="utf-8"`を明示して保存する場合はこの制約を受けない。

---

## 7. 参考

- [Model Context Protocol 公式仕様](https://modelcontextprotocol.io/specification)
- [FastMCP 公式ドキュメント](https://gofastmcp.com/)
- [FastMCP Skills Provider](https://gofastmcp.com/servers/providers/skills)
- [FastMCP ソースコード（`fastmcp/server/providers/skills/`、v3.4.7）](https://github.com/PrefectHQ/fastmcp) — `pip download fastmcp-slim==3.4.7`で取得し実装詳細を直接確認した
- [Agent Skills specification](https://agentskills.io/specification)
- [Claude Code: Connect to MCP servers](https://code.claude.com/docs/en/mcp)
