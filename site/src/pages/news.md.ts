import type { APIRoute } from 'astro'
import { getCollection } from 'astro:content'

import { newsMarkdown } from '../lib/agents'

export const GET: APIRoute = async () => {
  const items = (await getCollection('news'))
    .sort((a, b) => b.data.date.valueOf() - a.data.date.valueOf())
    .map(item => ({ ...item.data, body: item.body ?? '' }))
  return new Response(newsMarkdown(items), { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } })
}
