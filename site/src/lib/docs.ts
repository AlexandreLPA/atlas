// The documentation's pages as the files for agents need them: id, header and Markdown body.
import { getCollection } from 'astro:content'

import type { DocPage } from './agents'

export async function docPages(): Promise<DocPage[]> {
  return (await getCollection('docs')).map(page => ({
    id: page.id,
    title: page.data.title,
    description: page.data.description ?? '',
    body: page.body ?? ''
  }))
}
