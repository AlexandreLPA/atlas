import type { APIRoute } from 'astro'

import { SITE_URL } from '../../../../../site.config.mjs'
import { skillMarkdown } from '../../../../lib/agents'

export const GET: APIRoute = ({ site }) =>
  new Response(skillMarkdown(site ?? new URL(SITE_URL)), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' }
  })
