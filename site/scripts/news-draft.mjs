// Starts a news item from the address of a story: `npm run news:draft -- <url>`. It writes the file
// into src/content/news/ with the story's title, picture and day, and leaves the summary and the
// topics for a person to fill in. The page of the story is read, and none of its text is copied.
import { existsSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { draftMarkdown, fileName, readStory } from '../src/lib/news-draft.ts'

const url = process.argv[2]
if (!url || !URL.canParse(url)) {
  console.error('Usage: npm run news:draft -- <address of the story>')
  process.exit(1)
}

const response = await fetch(url, { headers: { 'User-Agent': 'atlas-site-news-draft' } })
if (!response.ok) {
  console.error(`${url} answered ${response.status}.`)
  process.exit(1)
}

const story = readStory(await response.text(), url, new Date())
const path = fileURLToPath(new URL(`../src/content/news/${fileName(story)}`, import.meta.url))
if (existsSync(path)) {
  console.error(`${path} already exists.`)
  process.exit(1)
}

writeFileSync(path, draftMarkdown(story, new URL(url).hostname.includes('alphasignal') ? 'AlphaSignal' : 'Source'))
console.log(`Wrote ${path}`)
console.log('Now write the summary in your own words, add the topics and the kind, then run npm run check.')
