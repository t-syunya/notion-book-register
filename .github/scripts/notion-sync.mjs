import { readFileSync } from "node:fs"
import { Client } from "@notionhq/client"

const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"))
const pr = event.pull_request

const key = `${pr.title} ${pr.head.ref}`
  .match(/(^|[^A-Z0-9_])(NBR-[0-9]+)(?=$|[^A-Z0-9_])/i)?.[2]
  ?.toUpperCase()

if (!key) {
  console.error("Issue キーが見つかりません")
  process.exit(1)
}

if (!process.env.NOTION_TOKEN) {
  console.error("NOTION_TOKEN が設定されていません")
  process.exit(1)
}

if (!process.env.NOTION_ISSUES_DB_ID) {
  console.error("NOTION_ISSUES_DB_ID が設定されていません")
  process.exit(1)
}

const notion = new Client({ auth: process.env.NOTION_TOKEN })

const { results } = await notion.databases.query({
  database_id: process.env.NOTION_ISSUES_DB_ID,
  filter: { property: "Issue Key", formula: { string: { equals: key } } },
  page_size: 1,
})

const page = results[0]
if (!page) {
  console.error(`Notion に ${key} の Issue がありません`)
  process.exit(1)
}

const properties = { "GitHub PR": { url: pr.html_url } }

if (pr.merged) {
  if (!pr.merged_at) {
    console.error("merged_at が取得できません")
    process.exit(1)
  }

  properties.Status = { status: { name: "Done" } }
  properties["Completed At"] = {
    date: { start: pr.merged_at.slice(0, 10) },
  }
} else if (pr.state === "closed") {
  console.log(`${key} は未マージでクローズされたため Status は更新しません`)
} else if (!pr.draft) {
  properties.Status = { status: { name: "In Progress" } }
}

await notion.pages.update({ page_id: page.id, properties })

console.log(`${key} を更新しました: ${pr.html_url}`)
