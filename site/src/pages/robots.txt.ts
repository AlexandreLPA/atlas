import type { APIRoute } from 'astro'

import { SITE_URL } from '../../site.config.mjs'
import { robotsTxt } from '../lib/agents'

export const GET: APIRoute = ({ site }) =>
  new Response(robotsTxt(site ?? new URL(SITE_URL)), { headers: { 'Content-Type': 'text/plain; charset=utf-8' } })
