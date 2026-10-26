// The documentation's title tags: a page keeps its own title on screen and in `atlas docs`, and a
// search result shows the words people search with.
import { defineRouteMiddleware } from '@astrojs/starlight/route-data'

import { searchTitle } from './lib/seo-titles'

export const onRequest = defineRouteMiddleware(context => {
  const route = context.locals.starlightRoute
  const title = searchTitle(route.id, route.entry.data.title)
  for (const item of route.head) {
    if (item.tag === 'title') {
      item.content = title
    } else if (item.tag === 'meta' && item.attrs?.['property'] === 'og:title') {
      item.attrs['content'] = title
    }
  }
})
