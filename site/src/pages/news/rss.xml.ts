import rss from '@astrojs/rss'
import type { APIRoute } from 'astro'
import { getCollection } from 'astro:content'

import { SITE_URL } from '../../../site.config.mjs'

export const GET: APIRoute = async ({ site }) => {
  const items = (await getCollection('news')).sort((a, b) => b.data.date.valueOf() - a.data.date.valueOf())
  return rss({
    title: 'Atlas news',
    description: 'Short summaries of what changes in the models and tools Atlas works with.',
    site: site ?? SITE_URL,
    items: items.map(item => ({
      title: item.data.title,
      link: item.data.url,
      pubDate: item.data.date,
      description: [item.body?.trim() ?? '', item.data.atlas ? `For Atlas: ${item.data.atlas}` : '']
        .filter(Boolean)
        .join('\n\n')
    }))
  })
}
