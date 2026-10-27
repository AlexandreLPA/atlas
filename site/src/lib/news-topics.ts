// How a news row carries its topics in the page. A topic can hold a space ("open source"), so
// the list is joined with a character no topic name uses, and a tab matches a whole topic.
const SEPARATOR = '|'

export function joinTopics(topics: readonly string[]): string {
  return topics.join(SEPARATOR)
}

export function hasTopic(joined: string, topic: string): boolean {
  return joined.split(SEPARATOR).includes(topic)
}
