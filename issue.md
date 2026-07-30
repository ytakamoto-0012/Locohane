# Issues - ローカルエージェントシステムの課題一覧

---

## Issue: DB接続切れエラー（no active connection）

### 概要
アプリケーションシャットダウン時、またはllama-serverのレスポンス遅延中に、LangGraphのSQLiteチェックポインター（aiosqlite）のDB接続が閉じられた状態で非同期操作が実行され、`ValueError: no active connection` が発生する。

### 発生ログ
| ログファイル | 発生時刻 | 備考 |
|---|---|---|
| app_20260730_13.log | 14:44:59 | シャットダウン時のDB接続切れ |
| app_20260730_15.log | 15:52:48 | llama-server遅延(13秒)がトリガー |

### 原因
1. アプリケーションシャットダウン時にDB接続が閉じられる
2. 残っていた非同期タスク（on_messageのストリーム処理等）がDB操作を試みる
3. 接続が既に閉じているため `ValueError: no active connection` が発生
4. llama-serverのレスポンス遅延（13秒等）があると、その間に接続が切れる可能性もある

### 発生箇所
- [app.py:153](c:\DT_Python\HTC_AI_Agent\app.py#L153) `_guarded` メソッド
- [app.py:156](c:\DT_Python\HTC_AI_Agent\app.py#L156) `aget_tuple`
- [app.py:162](c:\DT_Python\HTC_AI_Agent\app.py#L162) `aput_writes`
- [app.py:1094](c:\DT_Python\HTC_AI_Agent\app.py#L1094) `on_message`

### 関連設定
| 設定 | 値 | 場所 |
|---|---|---|
| チェックポインタ操作タイムアウト | 15秒 | [app.py:133](c:\DT_Python\HTC_AI_Agent\app.py#L133) |
| 旧接続クローズタイムアウト | 3秒 | [app.py:187](c:\DT_Python\HTC_AI_Agent\app.py#L187) |

### 対策案
シャットダウン時に保留中のすべての非同期タスクを完了（またはキャンセル）してからDB接続を閉じる処理を追加する。

---
