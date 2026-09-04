# ruff: noqa: E501
"""Built-in browser interface for image-based book registration."""

BOOK_REGISTRATION_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>本を登録</title>
<style>
:root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: Canvas; color: CanvasText; }
main { box-sizing: border-box; max-width: 42rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
h1 { margin: 0 0 .5rem; font-size: 1.75rem; }
p { line-height: 1.6; }
form { display: grid; gap: 1rem; margin-top: 1.5rem; }
label { display: grid; gap: .4rem; font-weight: 600; }
input, button { box-sizing: border-box; font: inherit; padding: .75rem; border-radius: .5rem; }
input { border: 1px solid color-mix(in srgb, CanvasText 30%, transparent); background: Canvas; color: CanvasText; }
button { border: 0; background: #1769aa; color: #fff; font-weight: 700; cursor: pointer; }
button:disabled { cursor: wait; opacity: .65; }
#status { min-height: 1.5rem; padding: .75rem; border-radius: .5rem; background: color-mix(in srgb, CanvasText 8%, transparent); white-space: pre-wrap; }
#status[data-kind="error"] { background: #b42318; color: #fff; }
#status[data-kind="success"] { background: #027a48; color: #fff; }
.note { color: color-mix(in srgb, CanvasText 72%, transparent); font-size: .9rem; }
</style>
</head>
<body>
<main>
<h1>本を登録</h1>
<p>表紙またはISBNバーコードが写った画像を選択してください。</p>
<form id="registration-form">
  <label>APIトークン
    <input id="api-token" type="password" autocomplete="off" required>
  </label>
  <label>書籍画像
    <input id="image" type="file" accept="image/jpeg,image/png" capture="environment" required>
  </label>
  <label>ジャンル（任意）
    <input id="genre" type="text" maxlength="200" autocomplete="off" placeholder="例: 技術書">
  </label>
  <button id="submit" type="submit">画像から登録する</button>
</form>
<p id="status" aria-live="polite">画像とAPIトークンを入力してください。</p>
<p class="note">トークンはこのブラウザタブ内だけに保存され、サーバーには保存されません。</p>
</main>
<script>
(() => {
  const storageKey = "notion-book-register.api-token";
  const form = document.getElementById("registration-form");
  const token = document.getElementById("api-token");
  const image = document.getElementById("image");
  const genre = document.getElementById("genre");
  const submit = document.getElementById("submit");
  const status = document.getElementById("status");
  token.value = sessionStorage.getItem(storageKey) || "";
  token.addEventListener("input", () => sessionStorage.setItem(storageKey, token.value));

  const showStatus = (message, kind = "") => {
    status.textContent = message;
    status.dataset.kind = kind;
  };
  const toBase64 = (file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("画像を読み込めませんでした。"));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = image.files[0];
    if (!file) {
      showStatus("画像を選択してください。", "error");
      return;
    }
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      showStatus("GLMではJPEGまたはPNG形式の画像を選択してください。", "error");
      return;
    }
    submit.disabled = true;
    showStatus("画像を解析して登録しています…");
    try {
      const response = await fetch("/v2/books", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token.value}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          image: await toBase64(file),
          mime_type: file.type,
          genre: genre.value.trim() || undefined,
        }),
      });
      const contentType = response.headers.get("content-type") || "";
      const result = contentType.includes("application/json") ? await response.json() : null;
      if (response.status === 503 && result && result.error === "Server is busy.") {
        throw new Error("サーバーが混雑しています。少し待ってから再実行してください。");
      }
      if (!response.ok || !result || !result.ok) {
        const message = result && result.message ? result.message : "サーバーとの通信に失敗しました。";
        const retry = result && result.error && result.error.retryable
          ? " 少し待ってから再実行してください。"
          : "";
        throw new Error(`${message}${retry}`);
      }
      const detail = result.result.created ? "登録しました。" : "すでに登録されています。";
      showStatus(`${detail}\n${result.result.title}\nISBN: ${result.result.isbn13}`, "success");
    } catch (error) {
      showStatus(error instanceof Error ? error.message : "通信に失敗しました。", "error");
    } finally {
      submit.disabled = false;
    }
  });
})();
</script>
</body>
</html>
"""
