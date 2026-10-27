// Each news item as Markdown, at its path with `.md` added: /news/2026-10-26-a-story.md.
import type { APIRoute, GetStaticPaths } from 'astro'
import { getCollection } from 'astro:content'

export const getStaticPaths = (async () =>
  (await getCollection('news')).map(item => ({
    params: { slug: item.id },
    props: { item }
  }))) satisfies GetStaticPaths

export const GET: APIRoute = ({ props }) => {
  const { item } = props as { item: Awaited<ReturnType<typeof getCollection<'news'>>>[number] }
  const { title, date, source, url, atlas } = item.data
  const text = [
    `# ${title}`,
    '',
    `${source}, ${date.toISOString().slice(0, 10)}. [Read the original](${url}).`,
    '',
    (item.body ?? '').trim(),
    ...(atlas ? ['', `For Atlas: ${atlas}`] : []),
    ''
  ].join('\n')
  return new Response(text, { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } })
}
