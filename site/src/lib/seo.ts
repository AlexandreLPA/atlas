// The structured data search engines read (schema.org, as JSON-LD): who makes Atlas, the site, the
// app itself, the questions the home answers, and where a documentation page sits. Built from the
// same words the pages show, so a rich result never says what the page does not.
import { REPOSITORY } from '../../site.config.mjs'

import { faq, hero, seo } from './home'

type Thing = Record<string, unknown>

const id = (site: URL, name: string) => new URL(`/#${name}`, site).toString()

export function organization(site: URL): Thing {
  return {
    '@type': 'Organization',
    '@id': id(site, 'organization'),
    name: 'Atlas',
    url: new URL('/', site).toString(),
    logo: new URL('/favicon.svg', site).toString(),
    sameAs: [REPOSITORY]
  }
}

export function website(site: URL): Thing {
  return {
    '@type': 'WebSite',
    '@id': id(site, 'website'),
    name: 'Atlas',
    url: new URL('/', site).toString(),
    description: seo.description,
    inLanguage: 'en',
    publisher: { '@id': id(site, 'organization') }
  }
}

export function application(site: URL): Thing {
  return {
    '@type': 'SoftwareApplication',
    '@id': id(site, 'app'),
    name: 'Atlas',
    description: hero.lede,
    url: new URL('/', site).toString(),
    applicationCategory: 'DeveloperApplication',
    operatingSystem: 'Windows, macOS, Linux',
    license: 'https://opensource.org/license/mit',
    isAccessibleForFree: true,
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    downloadUrl: new URL('/#install', site).toString(),
    installUrl: new URL('/docs/install/', site).toString(),
    softwareHelp: { '@type': 'CreativeWork', url: new URL('/docs/', site).toString() },
    codeRepository: REPOSITORY,
    image: new URL('/og.jpg', site).toString(),
    publisher: { '@id': id(site, 'organization') }
  }
}

export function faqPage(site: URL): Thing {
  return {
    '@type': 'FAQPage',
    '@id': id(site, 'faq'),
    mainEntity: faq.items.map(item => ({
      '@type': 'Question',
      name: item.question,
      acceptedAnswer: { '@type': 'Answer', text: item.answer }
    }))
  }
}

/** A documentation page: an article in the documentation, and the trail that leads to it. */
export function docPage(
  site: URL,
  page: { path: string; title: string; description: string; modified?: Date | undefined }
): Thing[] {
  const url = new URL(page.path, site).toString()
  // Every step a page of its own: a step without one is refused by the rich result.
  const trail = [
    { name: 'Atlas', url: new URL('/', site).toString() },
    { name: 'Docs', url: new URL('/docs/', site).toString() },
    ...(page.path === '/docs/' ? [] : [{ name: page.title, url }])
  ]
  return [
    {
      '@type': 'TechArticle',
      '@id': `${url}#article`,
      headline: page.title,
      description: page.description,
      url,
      inLanguage: 'en',
      ...(page.modified ? { dateModified: page.modified.toISOString() } : {}),
      isPartOf: { '@id': id(site, 'website') },
      about: { '@id': id(site, 'app') },
      publisher: { '@id': id(site, 'organization') }
    },
    {
      '@type': 'BreadcrumbList',
      itemListElement: trail.map((step, k) => ({
        '@type': 'ListItem',
        position: k + 1,
        name: step.name,
        item: step.url
      }))
    }
  ]
}

/** JSON-LD for a `<script>`: a `<` inside a string never closes the element. */
export function jsonLd(graph: Thing[]): string {
  return JSON.stringify({ '@context': 'https://schema.org', '@graph': graph }).replace(/</g, '\\u003c')
}
