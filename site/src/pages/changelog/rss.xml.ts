import rss from '@astrojs/rss'
import type { APIRoute } from 'astro'

import { REPOSITORY, SITE_URL } from '../../../site.config.mjs'
import { changes } from '../../lib/changelog'

export const GET: APIRoute = ({ site }) =>
  rss({
    title: 'Atlas changelog',
    description: 'Every change that reaches the Update button in Atlas.',
    site: site ?? SITE_URL,
    items: changes(60).map(change => ({
      title: change.subject,
      link: `${REPOSITORY}/commit/${change.sha}`,
      pubDate: new Date(`${change.date}T12:00:00Z`)
    }))
  })
