import type { APIRoute } from 'astro'

import { changelogMarkdown } from '../lib/agents'
import { changes } from '../lib/changelog'

export const GET: APIRoute = () =>
  new Response(changelogMarkdown(changes()), { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } })
