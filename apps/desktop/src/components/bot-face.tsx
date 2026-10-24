/**
 * A bot's face: a colored shape with its initial, drawn from its name alone, so the same name has
 * the same face on every screen with nothing stored: same name, same face, forever. There is no
 * look to pick: the face is deterministic.
 */

/** Colors that read on a light and on a dark background alike, with white text on them. */
export const FACE_COLORS = [
  '#7c3aed',
  '#2563eb',
  '#0891b2',
  '#059669',
  '#65a30d',
  '#ca8a04',
  '#ea580c',
  '#dc2626',
  '#db2777',
  '#4b5563'
] as const

/** Corner radii, as a share of the size: a circle, a squircle, a soft square. */
export const FACE_SHAPES = ['50%', '32%', '18%'] as const

/** A small stable hash of a name (FNV-1a), so a face never depends on where it is drawn. */
export function nameHash(name: string): number {
  let hash = 0x811c9dc5

  for (const char of name) {
    hash ^= char.codePointAt(0) ?? 0
    hash = Math.imul(hash, 0x01000193)
  }

  return hash >>> 0
}

/** The look a name has: its color, its shape and its letter. */
export function faceOf(name: string, title?: string): { color: string; letter: string; radius: string } {
  const hash = nameHash(name)
  const shown = (title ?? '').trim() || name

  return {
    color: FACE_COLORS[hash % FACE_COLORS.length] ?? FACE_COLORS[0],
    letter: (Array.from(shown)[0] ?? '?').toUpperCase(),
    radius: FACE_SHAPES[Math.floor(hash / FACE_COLORS.length) % FACE_SHAPES.length] ?? FACE_SHAPES[0]
  }
}

export function BotFace({ name, size = 32, title }: { name: string; size?: number; title?: string }) {
  const face = faceOf(name || '?', title)

  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center font-semibold text-white select-none"
      data-testid="bot-face"
      style={{
        backgroundColor: face.color,
        borderRadius: face.radius,
        fontSize: Math.round(size * 0.45),
        height: size,
        width: size
      }}
    >
      {face.letter}
    </span>
  )
}
