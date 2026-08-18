import { readFileSync } from "node:fs"
import { Client } from "@notionhq/client"

const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH, "utf8"))
const pr = event.pull_request

const issueKeyMatch = `${pr.title} ${pr.head.ref}`.match(
  /(^|[^A-Z0-9_])(NBR-([0-9]+))(?=$|[^A-Z0-9_])/i,
)
const key = issueKeyMatch?.[2]?.toUpperCase()
const issueId = issueKeyMatch?.[3]

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

const notion = new Client({
  auth: process.env.NOTION_TOKEN,
  notionVersion: "2022-06-28",
})

async function findIssuePage() {
  const queries = [
    {
      label: "Issue Key",
      filter: { property: "Issue Key", formula: { string: { equals: key } } },
      pageSize: 1,
    },
    {
      label: "ID",
      filter: { property: "ID", unique_id: { equals: Number(issueId) } },
      pageSize: 1,
    },
    {
      label: "Name",
      filter: { property: "Name", title: { contains: key } },
      pageSize: 10,
      matcher: (result) => hasIssueKey(result, key),
    },
  ]

  for (const query of queries) {
    const page = await queryIssue(query)

    if (page) {
      if (query.label !== "Issue Key") {
        console.warn(`${key} は ${query.label} で取得しました`)
      }
      return page
    }
  }

  return undefined
}

async function queryIssue(query) {
  let startCursor

  try {
    do {
      const response = await notion.databases.query({
        database_id: process.env.NOTION_ISSUES_DB_ID,
        filter: query.filter,
        page_size: query.pageSize,
        start_cursor: startCursor,
      })

      const page = query.matcher ? response.results.find(query.matcher) : response.results[0]
      if (page) {
        return page
      }

      startCursor = response.has_more ? response.next_cursor : undefined
    } while (startCursor)
  } catch (error) {
    if (isValidationError(error)) {
      console.warn(`${query.label} で検索できませんでした: ${error.message}`)
      return undefined
    }
    throw error
  }

  return undefined
}

function hasIssueKey(page, issueKey) {
  const title = page.properties?.Name?.title
    ?.map((text) => text.plain_text)
    .join("")

  return new RegExp(`(^|[^A-Z0-9_])${issueKey}($|[^A-Z0-9_])`, "i").test(title ?? "")
}

function isValidationError(error) {
  return error?.code === "validation_error" || error?.status === 400
}

const page = await findIssuePage()
if (!page) {
  console.error(
    `Notion に ${key} の Issue がありません。NOTION_ISSUES_DB_ID が Issues database を指しているか、Name に ${key} が含まれているか確認してください`,
  )
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
