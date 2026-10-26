// The first draft of a news item, made from the page of the story it summarizes: what the page says
// about itself (title, picture, day), never its text. The summary is left for a person to write in
// Atlas's own words, and tests/news.test.ts refuses an item that still carries this placeholder.
export const PLACEHOLDER = 'Write the summary here, in your own words.'

export interface Story {
  title: string
  url: string
  image: string | undefined
  date: string
}

const ENTITIES: Record<string, string> = { amp: '&', quot: '"', apos: "'", lt: '<', gt: '>' }

function decode(text: string): string {
  return text.replace(/&(?:#x([0-9a-f]+)|#(\d+)|(\w+));/gi, (whole, hex: string, dec: string, name: string) => {
    if (hex) {
      return String.fromCodePoint(parseInt(hex, 16))
    }
    if (dec) {
      return String.fromCodePoint(parseInt(dec, 10))
    }
    return ENTITIES[name.toLowerCase()] ?? whole
  })
}

/** The content of a `<meta>` tag by its property or name, with the attributes in either order. */
function meta(html: string, key: string): string | undefined {
  const tags = html.match(/<meta\b[^>]*>/gi) ?? []
  for (const tag of tags) {
    const named = new RegExp(`(?:property|name)=["']${key}["']`, 'i').test(tag)
    const content = /content=(["'])(.*?)\1/i.exec(tag)
    if (named && content?.[2]) {
      return decode(content[2])
    }
  }
  return undefined
}

export function readStory(html: string, url: string, today: Date): Story {
  const raw = meta(html, 'og:title') ?? /<title>(.*?)<\/title>/is.exec(html)?.[1] ?? ''
  const title = decode(raw)
    .replace(/\s*[|–-]\s*AlphaSignal\s*$/i, '')
    .trim()
  if (!title) {
    throw new Error(`No title found at ${url}.`)
  }
  const published = meta(html, 'article:published_time')
  const date = (published ?? today.toISOString()).slice(0, 10)
  return { title, url, image: meta(html, 'og:image'), date }
}

/** The file name of an item: its day, then the end of the story's address. */
export function fileName(story: Story): string {
  const last = new URL(story.url).pathname.split('/').filter(Boolean).pop() ?? 'story'
  return `${story.date}-${last.split('-').slice(0, 6).join('-')}.md`
}

export function draftMarkdown(story: Story, source: string): string {
  const quote = (text: string) => (/[:#'"]/.test(text) ? JSON.stringify(text) : text)
  return [
    '---',
    `title: ${quote(story.title)}`,
    `date: ${story.date}`,
    `source: ${source}`,
    `url: ${story.url}`,
    ...(story.image ? [`image: ${story.image}`] : []),
    'kind: news',
    'topics: []',
    '---',
    '',
    PLACEHOLDER,
    ''
  ].join('\n')
}
