# notion-book-register

iPhone で撮影した書籍画像から ISBN を抽出し、国立国会図書館の書誌情報を参照して Notion の書籍管理 DB に登録するためのツールです。

## 開発

Python 3.14 と依存関係は uv で管理します。開発環境の構築時は次を実行してください。

```bash
uv sync
```

```bash
uv run python -m unittest discover -s tests
```

Lint とフォーマット確認:

```bash
uv run ruff format --check .
uv run ruff check .
```

## VLM ISBN 抽出

`OPENAI_API_KEY` に OpenAI API キーを設定すると、`OpenAiVlmClient` から画像中の
ISBN-13 を抽出できます。モデルは `OPENAI_VLM_MODEL` で上書きでき、既定値は
`gpt-5-mini` です。

```python
from notion_book_register import OpenAiVlmClient

client = OpenAiVlmClient.from_env()
with open("book-cover.jpg", "rb") as image_file:
    result = client.extract_isbn13(image_file.read(), mime_type="image/jpeg")
print(result.isbn13)
```

## Notion 書き込み

`NOTION_API_KEY` または `NOTION_TOKEN` に Notion Integration のトークンを設定すると、
`NotionClient` から本棚データソースへ書籍ページを作成できます。
書き込み先は `NOTION_BOOKSHELF_DATA_SOURCE_ID` に UUID 形式のデータソースIDとして設定します。
Integration には対象データソースを共有し、重複チェックのための Read content と
ページ作成のための Insert content capability を付与してください。

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

`create_book_page` は登録前に `memo` の `ISBN: <ISBN-13>` を検索し、同じ ISBN のページが
存在する場合は新規作成せず既存ページを返します。戻り値の `created` が `False` の場合は
既存ページです。重複チェックを行わずに作成したい場合は `prevent_duplicates=False` を指定します。
ただし、この重複チェックは検索してから作成する非原子的な処理です。同じ ISBN の登録が並列で
実行された場合は、Notion 側に重複ページが作成される可能性があります。

対象の Notion 側スキーマに合わせて、`作品名` は title、`状態` と `ジャンル` は select、
`memo` は rich text として送信します。

## NDL 検索フォールバック

ISBN検索で書誌が見つからない場合は、既知のタイトルと著者を使って再検索できます。

```python
from notion_book_register import NdlClient

client = NdlClient()
response = client.search_by_isbn_with_fallback(
    "9784297135782",
    title="Python Testing",
    author="Author A",
)
```

フォールバックはISBN検索の結果が0件の場合だけ実行されます。通信失敗や不正なレスポンスを
検索結果なしとして扱わず、`NdlApiError` をそのまま返します。

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
