import type { Icon } from '@tabler/icons-react'

import type { ExecutorRow } from '@/protocol/protocol.generated'

import { ClaudeCode, Codex, DeepSeek, Sparkles, Terminal } from './ui/icons'

/** Each harness's mark, suggested by its maker's colors and never its logo (asked for by the user on
 * 21/10/2026, so a Codex account is not taken for a Claude Code one): Claude's terracotta and spark,
 * OpenAI's black and Codex's prompt, Google's blue and the sparkle its Gemini is known by (step 16p), DeepSeek's blue and a
 * telescope for its deep seeking (step 16q);
 * any other harness, a plain terminal. */
export function ExecutorAvatar({ row }: { row: ExecutorRow }) {
  const [look, Glyph] = AVATARS[row.plugin] ?? AVATAR

  return (
    <span
      aria-hidden
      className={`grid h-7 w-7 place-items-center rounded-full ${look}`}
      data-plugin={row.plugin}
      data-testid="executor-avatar"
    >
      <Glyph size={15} stroke={2} />
    </span>
  )
}

const AVATAR: [string, Icon] = ['bg-soft text-background', Terminal]
const AVATARS: Record<string, [string, Icon]> = {
  'claude-code': ['bg-[#d97757] text-white', ClaudeCode],
  codex: ['bg-primary text-primary-foreground', Codex],
  deepseek: ['bg-[#4d6bfe] text-white', DeepSeek],
  'gemini-cli': ['bg-[#1a73e8] text-white', Sparkles]
}
