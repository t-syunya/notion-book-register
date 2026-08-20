# notion-book-register

iPhone で撮影した書籍画像から ISBN を抽出し、国立国会図書館の書誌情報を参照して Notion の書籍管理 DB に登録するためのツールです。

## 開発

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## Notion 書き込み

`NOTION_API_KEY` または `NOTION_TOKEN` に Notion Integration のトークンを設定すると、
`NotionClient` から「Notion 本棚」ページ内の本棚データソースへ書籍ページを作成できます。

```python
from notion_book_register import Book, NotionClient

client = NotionClient.from_env()
page = client.create_book_page(
    Book(
        isbn13="9784297135782",
        title="Python Testing",
        authors=("Author A",),
        publisher="Publisher",
        published_date="2026",
        ndl_url="https://ndl.example/books/1",
    ),
    genre="技術書",
)
print(page.url)
```

書き込み先データソースIDは `2bddc1bd-5d17-8199-8910-000b299eb538` です。
対象の Notion 側スキーマに合わせて、`作品名` は title、`状態` と `ジャンル` は select、
`memo` は rich text として送信します。

## 実装順

Notion の `Project Issues` の親子関係、優先度、外部依存の少なさから、次の順で進めます。

1. ISBN-13 の正規化とチェックデジット検証を実装する
2. 内部 Book モデルを実装する
3. 書籍管理 DB の項目とマッピングを定義する
4. NDL API クライアントを実装する
5. NDL 書誌レスポンスを内部 Book モデルへ正規化する
6. Notion API で書籍ページを新規作成する
7. ISBN 重複時の登録防止処理を実装する
8. VLM プロバイダ抽象化と API クライアントを実装する
9. 画像から ISBN-13 を JSON で抽出するプロンプトを実装する
10. ISBN 検索失敗時のタイトル・著者検索フォールバックを実装する
11. 画像アップロード用 API エンドポイントを実装する
12. ショートカットへ登録成功・失敗結果を返す
13. iPhone ショートカットで撮影画像を API へ送信する

最初に純粋関数と内部モデルを固め、外部 API、Notion 書き込み、画像アップロード API の順で境界を広げます。

## Issue / PR 運用

Notion の Issue と GitHub PR は `NBR-<ID>` 形式の Issue キーで連携します。

- ブランチ名または PR タイトルに Issue キーを含める
- Notion Issue の `Name` にも Issue キーを含める
- PR タイトルは `[NBR-8] ISBN検証を追加` のように先頭へキーを付ける
- コミットメッセージにも同じ Issue キーを含める
- PR 本文の「対応 Issue」に Notion Issue URL を貼る
- PR 作成時の GitHub Actions が Notion の `GitHub PR` と `Status` を更新する

Issue を先に作成してから PR を開きます。
