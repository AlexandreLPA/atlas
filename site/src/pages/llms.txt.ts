import type { APIRoute } from 'astro'

import { SITE_URL } from '../../site.config.mjs'
import { llmsTxt } from '../lib/agents'
import { docPages } from '../lib/docs'

export const GET: APIRoute = async ({ site }) =>
  new Response(llmsTxt(await docPages(), site ?? new URL(SITE_URL)), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' }
  })
