# notion-book-register

iPhone で撮影した書籍画像から ISBN を抽出し、国立国会図書館の書誌情報を参照して Notion の書籍管理 DB に登録するためのツールです。

## 開発

```bash
python -m unittest discover -s tests
```

## 実装順

Notion の `Project Issues` の親子関係、優先度、外部依存の少なさから、次の順で進めます。

1. ISBN-13 の正規化とチェックデジット検証を実装する
2. 書籍管理 DB の項目とマッピングを定義する
3. NDL API クライアントを実装する
4. NDL 書誌レスポンスを内部 Book モデルへ正規化する
5. Notion API で書籍ページを新規作成する
6. ISBN 重複時の登録防止処理を実装する
7. VLM プロバイダ抽象化と API クライアントを実装する
8. 画像から ISBN-13 を JSON で抽出するプロンプトを実装する
9. ISBN 検索失敗時のタイトル・著者検索フォールバックを実装する
10. 画像アップロード用 API エンドポイントを実装する
11. ショートカットへ登録成功・失敗結果を返す
12. iPhone ショートカットで撮影画像を API へ送信する

最初に純粋関数と内部モデルを固め、外部 API、Notion 書き込み、画像アップロード API の順で境界を広げます。

## Issue / PR 運用

Notion の Issue と GitHub PR は `NBR-<ID>` 形式の Issue キーで連携します。

- ブランチ名、コミットメッセージ、PR タイトルのいずれかに Issue キーを含める
- コミットメッセージと PR タイトルは `[NBR-8] ISBN検証を追加` のように先頭へキーを付ける
- PR 本文の「対応 Issue」に Notion Issue URL を貼る
- PR 作成時の GitHub Actions が Notion の `GitHub PR` と `Status` を更新する

Issue を先に作成してから PR を開きます。
