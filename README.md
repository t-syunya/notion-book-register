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

既定のVLMはZ.AIの `GLM-4.6V-Flash` です。`GLM_API_KEY`（または `ZAI_API_KEY`）を設定すると、
画像からISBN-13を抽出できます。モデルは `GLM_VLM_MODEL` で上書きできます。
GLM利用時はJPEGまたはPNG、5 MiB未満、縦横6000px以下の画像を使用してください。

`VLM_PROVIDER=openai` を設定するとOpenAIへ切り替えられます。その場合は `OPENAI_API_KEY` が必要で、
モデルは `OPENAI_VLM_MODEL` で指定します。両プロバイダー共通で上書きしたい場合だけ
`VLM_MODEL` を使用します。

```python
from notion_book_register import GlmVlmClient

client = GlmVlmClient.from_env()
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
フォールバックで別版の書誌が見つかった場合も、登録・重複判定には画像から読み取ったISBNを使用します。

## 画像登録 API

画像をbase64で送信し、VLMによるISBN抽出、NDL書誌検索、Notion登録を一度に実行できます。
APIは既定で `127.0.0.1:8000` を使用し、Bearer token認証を必須とします。

```bash
export GLM_API_KEY="..."
export NOTION_API_KEY="..."
export NOTION_BOOKSHELF_DATA_SOURCE_ID="..."
export BOOK_REGISTER_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run notion-book-register-api
```

`POST /v1/books` は従来形式の互換endpointです。iPhoneショートカットでは、成功・失敗を
一貫したJSON形式で受け取れる `POST /v2/books` へ `application/json` を送信します。

```json
{
  "image": "<base64 encoded image>",
  "mime_type": "image/jpeg",
  "title": "ISBN検索失敗時だけ使うタイトル（省略可）",
  "author": "ISBN検索失敗時だけ使う著者（省略可）",
  "genre": "技術書"
}
```

`title` と `author` は片方だけ指定できません。画像サイズの既定上限は10 MiBで、
`BOOK_REGISTER_MAX_IMAGE_BYTES` から変更できます。外部公開する場合は
`BOOK_REGISTER_HOST` を明示し、TLS終端を備えたリバースプロキシの背後で実行してください。
稼働確認には認証不要の `GET /healthz` を利用できます。

ショートカットからは、HTTP statusに加えて `ok` と `message` を確認します。新規登録時は
次の形式です。重複時はHTTP 200となり、`created` が `false` になります。

```json
{
  "ok": true,
  "message": "書籍をNotionに登録しました。",
  "result": {
    "isbn13": "9784297135782",
    "title": "Python Testing",
    "page_id": "...",
    "page_url": "https://www.notion.so/...",
    "created": true
  }
}
```

失敗時もJSONを返し、`error.code` で分岐できます。`retryable` が `true` の場合だけ、
時間を置いた再実行の候補にしてください。

```json
{
  "ok": false,
  "message": "画像から有効なISBN-13を読み取れませんでした。",
  "error": {
    "code": "isbn_not_detected",
    "retryable": false
  }
}
```

接続上限でサーバーがrequest lineを解析する前に拒否した場合だけは、versionを特定できないため
HTTP 503と `{"error":"Server is busy."}` を返します。この場合は `ok` を読まず、HTTP statusを
優先して短時間の待機後に再実行してください。

サーバー側の読取タイムアウトは `BOOK_REGISTER_REQUEST_TIMEOUT_SECONDS`（既定15秒）、
同時処理数は `BOOK_REGISTER_MAX_CONCURRENT_REQUESTS`（既定16）で制限します。外部公開時は
リバースプロキシ側にもheader/body timeout、body size、connection limitを必ず設定してください。
同一のサービスインスタンスを通るNotion書き込みは直列化されますが、複数インスタンス、
複数process、複数ホストを跨ぐ重複登録は完全には防止できません。

iPhoneショートカットの具体的な作成・エラー分岐手順は
[docs/iphone-shortcut.md](docs/iphone-shortcut.md) を参照してください。

## Docker Composeでのセルフホスト

常設サーバーではDocker Composeで実行できます。アプリケーションはステートレスで、本棚データは
Notionに保存します。コンテナのログは標準出力に出力されるため、`docker compose logs` で確認します。

```bash
cp .env.example .env
# .env に GLM_API_KEY、NOTION_API_KEY、NOTION_BOOKSHELF_DATA_SOURCE_ID、
# BOOK_REGISTER_API_TOKEN を設定する
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/healthz
```

既定ではホストの `127.0.0.1:8000` のみへ公開します。Tailscale端末から直接接続する場合は、
`.env` の `BOOK_REGISTER_BIND_ADDRESS` をホストのTailscale IPまたは `0.0.0.0` に変更します。
その場合はホスト側のファイアウォールもTailscaleインターフェースだけを許可してください。
Cloudflare Tunnelを使う場合も、コンテナはループバックのままTunnelから接続できます。具体的な
Tailscale / Cloudflare Tunnelの設定は接続・運用タスクで追加します。

停止と更新は次のとおりです。`.env` はDockerビルドコンテキストへ含まれず、イメージにも保存されません。

```bash
docker compose down
docker compose pull  # 将来レジストリイメージを使う場合だけ必要
docker compose up --build -d
docker compose logs -f app
```

## ブラウザからの登録

サービスの `/` をブラウザで開くと、書籍画像を選択・撮影して登録できる画面を表示します。
スマートフォンではファイル選択時にカメラを使用できます。既定のGLM利用時はJPEGまたはPNGを選択してください。
画面で入力したAPIトークンは、このブラウザ
タブの`sessionStorage`だけに保持され、サーバーには保存しません。ブラウザUIとiPhoneショートカットは
どちらも同じ `POST /v2/books` を利用します。

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
