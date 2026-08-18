# notion-book-register

iPhone で撮影した書籍画像から ISBN を抽出し、国立国会図書館の書誌情報を参照して Notion の書籍管理 DB に登録するためのツールです。

## 開発

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## Issue / PR 運用

Notion の Issue と GitHub PR は `NBR-<ID>` 形式の Issue キーで連携します。

- ブランチ名または PR タイトルに Issue キーを含める
- Notion Issue の `Name` にも Issue キーを含める
- PR タイトルは `[NBR-8] ISBN検証を追加` のように先頭へキーを付ける
- コミットメッセージにも同じ Issue キーを含める
- PR 本文の「対応 Issue」に Notion Issue URL を貼る
- PR 作成時の GitHub Actions が Notion の `GitHub PR` と `Status` を更新する

Issue を先に作成してから PR を開きます。
