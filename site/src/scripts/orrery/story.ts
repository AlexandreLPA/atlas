// The story the orrery tells as the page scrolls: one shot per chapter, eased between them.
import { clamp, lookAt, type Mat4, mix, perspective, smoother, type Vec3 } from './math'

export const CHAPTERS = ['hero', 'accounts', 'route', 'machines', 'memory', 'close'] as const
export type ChapterId = (typeof CHAPTERS)[number]

export interface Shot {
  /** 0 is the sphere, 1 the open orrery. */
  open: number
  yaw: number
  pitch: number
  roll: number
  /** The radius, in world units, the shot must frame. */
  fit: number
  /** How much of half the window's smaller side that radius fills. */
  fill: number
  target: Vec3
  /** 1 beside the text column, 0 centred in the window. */
  side: number
  /** Where the scene's centre sits vertically, in the window's -1..1 coordinates. */
  shiftY: number
  executors: number
  route: number
  machines: number
  memory: number
  /** Atlas's light at the centre. */
  core: number
}

const base: Shot = {
  open: 0,
  yaw: 0,
  pitch: 0,
  roll: 0,
  fit: 1.16,
  fill: 0.62,
  target: [0, 0, 0],
  side: 1,
  shiftY: 0,
  executors: 0,
  route: 0,
  machines: 0,
  memory: 0,
  core: 0
}

const wide: Record<ChapterId, Shot> = {
  hero: { ...base },
  accounts: {
    ...base,
    open: 1,
    yaw: 0.18,
    pitch: 0.42,
    roll: -0.1,
    fit: 3,
    fill: 0.84,
    executors: 1,
    core: 0.85
  },
  route: {
    ...base,
    open: 1,
    yaw: -0.32,
    pitch: 0.55,
    roll: -0.06,
    fit: 2.5,
    fill: 0.9,
    target: [0, 0, 0.15],
    executors: 1,
    route: 1,
    core: 0.85
  },
  machines: {
    ...base,
    open: 1,
    yaw: 0.08,
    pitch: 0.6,
    roll: -0.04,
    fit: 10.5,
    fill: 0.95,
    target: [-0.2, 0.4, -6.6],
    shiftY: 0.04,
    executors: 0.55,
    machines: 1,
    core: 0.9
  },
  memory: {
    ...base,
    open: 1,
    yaw: 0.62,
    pitch: 0.34,
    roll: -0.12,
    fit: 2.5,
    fill: 0.92,
    executors: 0.35,
    memory: 1,
    core: 1.35
  },
  close: { ...base, fit: 1.16, fill: 0.42, side: 0, shiftY: 0.36, core: 0.55 }
}

/** On a narrow window the scene sits above the text instead of beside it. */
const narrow: Record<ChapterId, Shot> = {
  hero: { ...wide.hero, fill: 0.78, side: 0, shiftY: 0 },
  accounts: { ...wide.accounts, fill: 1.04, side: 0, shiftY: 0.02 },
  route: { ...wide.route, fill: 1.08, side: 0, shiftY: 0.02 },
  machines: { ...wide.machines, fill: 1.08, side: 0, shiftY: 0 },
  memory: { ...wide.memory, fill: 1.06, side: 0, shiftY: 0.02 },
  close: { ...wide.close, fill: 0.78, side: 0, shiftY: 0 }
}

const KEYS = [
  'open',
  'yaw',
  'pitch',
  'roll',
  'fit',
  'fill',
  'side',
  'shiftY',
  'executors',
  'route',
  'machines',
  'memory',
  'core'
] as const

function blend(a: Shot, b: Shot, t: number): Shot {
  const out = {
    ...a,
    target: [
      mix(a.target[0], b.target[0], t),
      mix(a.target[1], b.target[1], t),
      mix(a.target[2], b.target[2], t)
    ] as Vec3
  }
  for (const key of KEYS) {
    out[key] = mix(a[key], b[key], t)
  }
  return out
}

/**
 * Where the story is, as a chapter index with a fraction: 2 while the third chapter's middle is
 * at the middle of the window. Each chapter holds its shot through its middle half, and the shots
 * blend across the edges between chapters.
 */
export function storyPosition(center: number, chapters: { top: number; height: number }[]): number {
  if (chapters.length === 0) {
    return 0
  }
  let raw = 0
  for (let k = 0; k < chapters.length; k++) {
    const chapter = chapters[k]
    if (!chapter) {
      continue
    }
    if (center >= chapter.top || k === 0) {
      raw = k + (center - chapter.top) / chapter.height - 0.5
    }
  }
  raw = clamp(raw, 0, chapters.length - 1)
  const whole = Math.floor(raw)
  return whole + smoother(0.22, 0.78, raw - whole)
}

export function shotAt(position: number, isNarrow: boolean): Shot {
  const shots = isNarrow ? narrow : wide
  const k = clamp(Math.floor(position), 0, CHAPTERS.length - 1)
  const next = clamp(k + 1, 0, CHAPTERS.length - 1)
  const a = shots[CHAPTERS[k] ?? 'hero']
  const b = shots[CHAPTERS[next] ?? 'hero']
  return blend(a, b, position - k)
}

export const FOV = (30 * Math.PI) / 180

export interface Camera {
  view: Mat4
  proj: Mat4
  eye: Vec3
}

/** The camera for a shot: it frames `fit` so it fills `fill` of the window's smaller half. */
export function cameraFor(shot: Shot, aspect: number, drift: [number, number]): Camera {
  const half = Math.tan(FOV / 2) * Math.min(1, aspect)
  const distance = shot.fit / (half * shot.fill)
  const yaw = shot.yaw + drift[0]
  const pitch = shot.pitch + drift[1]
  const [tx, ty, tz] = shot.target
  const eye: Vec3 = [
    tx + distance * Math.sin(yaw) * Math.cos(pitch),
    ty + distance * Math.sin(pitch),
    tz + distance * Math.cos(yaw) * Math.cos(pitch)
  ]
  const up: Vec3 = [Math.sin(shot.roll), Math.cos(shot.roll), 0]
  return {
    view: lookAt(eye, shot.target, up),
    proj: perspective(FOV, aspect, 0.1, 400),
    eye
  }
}
