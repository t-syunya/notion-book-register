# セルフホスト運用

このアプリケーションはDocker Composeで常設サーバーに配置できます。書籍データはNotionに保存するため、
アプリケーション用の永続ボリュームは必要ありません。ログはDockerの標準出力で確認します。

## 初回起動

```bash
cp .env.example .env
# .env に GLM_API_KEY、NOTION_API_KEY、NOTION_BOOKSHELF_DATA_SOURCE_ID、
# BOOK_REGISTER_API_TOKEN を設定する
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/healthz
```

`BOOK_REGISTER_API_TOKEN` は書き込みAPIの認証情報です。URL、スクリーンショット、Git、Notionのタスク本文へ
記録しません。トークンを変更した場合は `docker compose up -d` でコンテナを再作成します。

## TailscaleでiPhoneから使う

これが基本の接続方法です。自宅サーバーとiPhoneを同じTailnetへ参加させます。ポート開放や公開DNSは不要です。

1. 自宅サーバーとiPhoneでTailscaleにログインし、同じTailnetに参加する。
2. サーバーのTailscale IPまたはMagicDNS名を `tailscale status` で確認する。
3. `.env` の `BOOK_REGISTER_BIND_ADDRESS` をサーバーのTailscale IPに変更する。
4. `docker compose up -d` を実行する。
5. iPhoneのSafariで `http://<Tailscaleのホスト名>:8000/` を開く。ショートカットのURLは
   `http://<Tailscaleのホスト名>:8000/v2/books` にする。

Tailnet限定の運用では `0.0.0.0` を指定しません。Dockerの公開ポートは一般的なホスト向け
ファイアウォール規則を迂回する場合があり、LANや外部インターフェースへ意図せず公開されるためです。
サーバーのTailscale IPへ直接バインドしてください。

## Cloudflare Tunnelで公開する

外出先などでTailscaleを使わずにアクセスしたい場合だけ、Cloudflare Tunnelを追加します。外向き接続だけを
使うため、ルーターのポート開放は不要です。CloudflareのDNSに管理されたドメインが必要です。

1. Cloudflare Zero Trustの **Networks > Tunnels** でトンネルを作成し、トークンをコピーする。
2. 同じトンネルのPublic Hostnameを追加する。ホスト名は任意のサブドメイン、Serviceは
   `http://app:8000` にする。
3. `cp .cloudflare.env.example .cloudflare.env` を実行し、`.cloudflare.env` の
   `TUNNEL_TOKEN` にトークンを設定する。このファイルはcloudflaredコンテナだけに渡され、
   アプリコンテナには渡されない。
4. 次のコマンドでアプリケーションとTunnelを起動する。

```bash
docker compose -f compose.yaml -f compose.cloudflare.yaml up --build -d
docker compose -f compose.yaml -f compose.cloudflare.yaml ps
docker compose -f compose.yaml -f compose.cloudflare.yaml logs -f cloudflared
```

トンネルが接続されると、設定した `https://<ホスト名>/` でブラウザUIを利用できます。ショートカットのURLは
`https://<ホスト名>/v2/books` です。Tunnelの宛先はDocker内部の `app:8000` なので、
`BOOK_REGISTER_BIND_ADDRESS` は既定の `127.0.0.1` のままで構いません。

Cloudflare Accessは必須にしません。ただし公開URLは誰でも到達できるため、書き込みには必ず
`BOOK_REGISTER_API_TOKEN` を使用します。十分に長いランダム値を使い、漏えい時は速やかに再生成してください。
Tunnelトークンも資格情報です。`.cloudflare.env`、シェル履歴、画面共有、ログへ出さず、漏えい時は
Cloudflare側でトークンをローテーションします。ローテーション後はCloudflareで発行された新しいトークンを
`.cloudflare.env` の `TUNNEL_TOKEN` に置き換え、次のコマンドでcloudflaredコンテナを再作成します。

```bash
docker compose -f compose.yaml -f compose.cloudflare.yaml up -d --force-recreate cloudflared
docker compose -f compose.yaml -f compose.cloudflare.yaml logs -f cloudflared
```

新しいトークンはコマンドライン引数へ渡さず、`.cloudflare.env` にだけ保存します。

## 動作確認と更新

ブラウザでは `/` を開き、画像選択またはカメラ撮影で登録します。iPhoneショートカットでは
`docs/iphone-shortcut.md` の手順に従い、接続先だけを上記URLへ変更します。APIの確認には次を使えます。

Tailscaleを使う場合:

```bash
curl http://<Tailscaleのホスト名>:8000/healthz
```

Cloudflare Tunnelを使う場合:

```bash
curl https://<ホスト名>/healthz
```

アプリケーション更新時はイメージを再ビルドして再作成します。Cloudflare Tunnelを使う場合は
cloudflaredイメージも明示的に更新します。

```bash
docker compose -f compose.yaml -f compose.cloudflare.yaml pull cloudflared
docker compose -f compose.yaml -f compose.cloudflare.yaml up --build -d
```

Cloudflare Tunnelを使わない場合は、通常の `docker compose up --build -d` を使用します。

停止時も、起動した構成と同じComposeファイルを指定します。

```bash
# Tailscaleまたはローカルだけを使う場合
docker compose down

# Cloudflare Tunnelを使う場合
docker compose -f compose.yaml -f compose.cloudflare.yaml down
```
