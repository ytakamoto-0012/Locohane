"""テキストファイルの行数・単語数・文字数を数えて JSON で出力する。

word-counter スキルの実行スクリプト（progressive disclosure 第3段階）。
run_script ツールから `python count.py <ファイルパス>` の形で呼ばれる。

自己完結（標準ライブラリのみ）。依存なし。
"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: count.py <text-file-path>", file=sys.stderr)
        return 1

    target = Path(sys.argv[1])
    if not target.is_file():
        print(f"ファイルが見つかりません: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8", errors="replace")
    result = {
        "path": str(target),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "chars": len(text),
    }
    # 結果は JSON で標準出力へ（呼び出し側が機械的に読めるように）。
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
