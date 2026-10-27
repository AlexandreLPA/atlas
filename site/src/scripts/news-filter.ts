// The news page's tabs and search: every item is already in the page, so filtering only hides rows.
import { hasTopic } from '../lib/news-topics'

const root = document.querySelector<HTMLElement>('[data-news]')

if (root) {
  const rows = Array.from(root.querySelectorAll<HTMLElement>('[data-news-row]'))
  const tabs = Array.from(root.querySelectorAll<HTMLButtonElement>('[data-news-tab]'))
  const search = root.querySelector<HTMLInputElement>('[data-news-search]')
  const empty = root.querySelector<HTMLElement>('[data-news-empty]')
  let topic = ''

  const apply = () => {
    const query = (search?.value ?? '').trim().toLowerCase()
    let shown = 0
    for (const row of rows) {
      const matches =
        (!topic || hasTopic(row.dataset.topics ?? '', topic)) && (!query || (row.dataset.text ?? '').includes(query))
      row.hidden = !matches
      if (matches) {
        shown += 1
        row.style.setProperty('--n', String(shown).padStart(2, '0'))
      }
    }
    if (empty) {
      empty.hidden = shown > 0
    }
  }

  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      topic = tab.dataset.newsTab ?? ''
      for (const other of tabs) {
        other.setAttribute('aria-pressed', String(other === tab))
      }
      apply()
    })
  }
  search?.addEventListener('input', apply)
}
