// The skills this site publishes, for agents that look for them (/.well-known/agent-skills/).
import type { APIRoute } from 'astro'

import { SITE_URL } from '../../../../site.config.mjs'
import { skillsIndex } from '../../../lib/agents'

export const GET: APIRoute = ({ site }) =>
  new Response(skillsIndex(site ?? new URL(SITE_URL)), { headers: { 'Content-Type': 'application/json' } })
