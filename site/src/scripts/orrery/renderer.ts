// The orrery's WebGL2 renderer: the strands, the curves (arms, routes, links, ticks) and the
// sprites (bodies, stars, particles), all blended additively on a transparent canvas.
import { instanceBuffer, program, uniforms, type Uniforms, vertexBuffer } from './gl'
import type { Mat4 } from './math'
import { CURVE_SEGMENTS, SEGMENTS, STRANDS } from './model'
import {
  curveFragment,
  curveVertex,
  lineFragment,
  spriteFragment,
  spriteVertex,
  STRAND_INSTANCES,
  strandVertex
} from './shaders'

export const CURVE_FLOATS = 21
export const SPRITE_FLOATS = 9
export const MAX_CURVES = 160
export const MAX_SPRITES = 640

export type RGB = [number, number, number]

export function hex(value: string): RGB {
  const n = Number.parseInt(value.slice(1), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

export interface StrandSystem {
  model: Mat4
  scale: number
  open: number
  alpha: number
  spin: number
  frameAlpha: number
  base: RGB
  accent: RGB
  trail: Float32Array
  trailGain: number
}

export interface FrameData {
  time: number
  view: Mat4
  proj: Mat4
  shift: [number, number]
  pointer: [number, number, number]
  globe: [number, number]
  reveal: number
  systems: StrandSystem[]
  curves: Float32Array
  curveCount: number
  sprites: Float32Array
  spriteCount: number
  stars: number
}

const SCREEN_UNIFORMS = ['u_view', 'u_proj', 'u_shift', 'u_resolution', 'u_dpr', 'u_pointer'] as const

type ScreenName = (typeof SCREEN_UNIFORMS)[number]

/** A strip of `segments + 1` points, two vertices each: (u, side). */
function strip(segments: number): Float32Array {
  const data = new Float32Array((segments + 1) * 4)
  for (let s = 0; s <= segments; s++) {
    const u = s / segments
    data.set([u, -1, u, 1], s * 4)
  }
  return data
}

export class Renderer {
  readonly gl: WebGL2RenderingContext
  private readonly strandProgram: WebGLProgram
  private readonly strandUniforms: Uniforms<
    | ScreenName
    | 'u_model'
    | 'u_scale'
    | 'u_time'
    | 'u_open'
    | 'u_globe'
    | 'u_reveal'
    | 'u_alpha'
    | 'u_spin'
    | 'u_frameAlpha'
    | 'u_base'
    | 'u_accent'
    | 'u_trail'
    | 'u_trailGain'
  >
  private readonly strandVao: WebGLVertexArrayObject
  private readonly curveProgram: WebGLProgram
  private readonly curveUniforms: Uniforms<ScreenName | 'u_time'>
  private readonly curveVao: WebGLVertexArrayObject
  private readonly curveBuffer: WebGLBuffer
  private readonly spriteProgram: WebGLProgram
  private readonly spriteUniforms: Uniforms<ScreenName | 'u_time' | 'u_alpha'>
  private readonly spriteVao: WebGLVertexArrayObject
  private readonly spriteBuffer: WebGLBuffer
  private readonly starVao: WebGLVertexArrayObject
  private readonly starCount: number
  width = 1
  height = 1
  dpr = 1

  constructor(
    readonly canvas: HTMLCanvasElement,
    stars: Float32Array
  ) {
    const gl = canvas.getContext('webgl2', { alpha: true, antialias: false, premultipliedAlpha: true })
    if (!gl) {
      throw new Error('WebGL2 is not available.')
    }
    this.gl = gl

    this.strandProgram = program(gl, strandVertex, lineFragment)
    this.strandUniforms = uniforms(gl, this.strandProgram, [
      ...SCREEN_UNIFORMS,
      'u_model',
      'u_scale',
      'u_time',
      'u_open',
      'u_globe',
      'u_reveal',
      'u_alpha',
      'u_spin',
      'u_frameAlpha',
      'u_base',
      'u_accent',
      'u_trail',
      'u_trailGain'
    ])
    this.strandVao = gl.createVertexArray()
    gl.bindVertexArray(this.strandVao)
    vertexBuffer(gl, strip(SEGMENTS), [
      { location: 0, size: 1 },
      { location: 1, size: 1 }
    ])
    const instances = new Float32Array(STRAND_INSTANCES * 2)
    for (let k = 0; k < STRAND_INSTANCES; k++) {
      instances.set([Math.floor(k / 2), k % 2], k * 2)
    }
    instanceBuffer(gl, instances, [{ location: 2, size: 2 }], gl.STATIC_DRAW)

    this.curveProgram = program(gl, curveVertex, curveFragment)
    this.curveUniforms = uniforms(gl, this.curveProgram, [...SCREEN_UNIFORMS, 'u_time'])
    this.curveVao = gl.createVertexArray()
    gl.bindVertexArray(this.curveVao)
    vertexBuffer(gl, strip(CURVE_SEGMENTS), [
      { location: 0, size: 1 },
      { location: 1, size: 1 }
    ])
    this.curveBuffer = instanceBuffer(
      gl,
      new Float32Array(MAX_CURVES * CURVE_FLOATS),
      [
        { location: 2, size: 3 },
        { location: 3, size: 3 },
        { location: 4, size: 3 },
        { location: 5, size: 4 },
        { location: 6, size: 4 },
        { location: 7, size: 4 }
      ],
      gl.DYNAMIC_DRAW
    )

    this.spriteProgram = program(gl, spriteVertex, spriteFragment)
    this.spriteUniforms = uniforms(gl, this.spriteProgram, [...SCREEN_UNIFORMS, 'u_time', 'u_alpha'])
    const corners = new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1])
    const spriteLayout = [
      { location: 1, size: 3 },
      { location: 2, size: 1 },
      { location: 3, size: 4 },
      { location: 4, size: 1 }
    ]
    this.spriteVao = gl.createVertexArray()
    gl.bindVertexArray(this.spriteVao)
    vertexBuffer(gl, corners, [{ location: 0, size: 2 }])
    this.spriteBuffer = instanceBuffer(gl, new Float32Array(MAX_SPRITES * SPRITE_FLOATS), spriteLayout, gl.DYNAMIC_DRAW)

    this.starVao = gl.createVertexArray()
    gl.bindVertexArray(this.starVao)
    vertexBuffer(gl, corners, [{ location: 0, size: 2 }])
    instanceBuffer(gl, stars, spriteLayout, gl.STATIC_DRAW)
    this.starCount = stars.length / SPRITE_FLOATS

    gl.bindVertexArray(null)
    gl.disable(gl.DEPTH_TEST)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE)
  }

  resize(width: number, height: number, dpr: number) {
    this.dpr = dpr
    this.width = Math.max(1, Math.round(width * dpr))
    this.height = Math.max(1, Math.round(height * dpr))
    if (this.canvas.width !== this.width || this.canvas.height !== this.height) {
      this.canvas.width = this.width
      this.canvas.height = this.height
    }
  }

  private screen(u: Uniforms<ScreenName>, frame: FrameData) {
    const gl = this.gl
    gl.uniformMatrix4fv(u.u_view, false, frame.view)
    gl.uniformMatrix4fv(u.u_proj, false, frame.proj)
    gl.uniform2f(u.u_shift, frame.shift[0], frame.shift[1])
    gl.uniform2f(u.u_resolution, this.width, this.height)
    gl.uniform1f(u.u_dpr, this.dpr)
    gl.uniform3f(u.u_pointer, frame.pointer[0], frame.pointer[1], frame.pointer[2])
  }

  draw(frame: FrameData) {
    const gl = this.gl
    gl.viewport(0, 0, this.width, this.height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)

    // Stars first, the faintest layer.
    if (frame.stars > 0.001) {
      gl.useProgram(this.spriteProgram)
      this.screen(this.spriteUniforms, { ...frame, pointer: [0, 0, 0] })
      gl.uniform1f(this.spriteUniforms.u_time, frame.time)
      gl.uniform1f(this.spriteUniforms.u_alpha, frame.stars)
      gl.bindVertexArray(this.starVao)
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, this.starCount)
    }

    gl.useProgram(this.strandProgram)
    const u = this.strandUniforms
    this.screen(u, frame)
    gl.uniform1f(u.u_time, frame.time)
    gl.uniform2f(u.u_globe, frame.globe[0], frame.globe[1])
    gl.uniform1f(u.u_reveal, frame.reveal)
    gl.bindVertexArray(this.strandVao)
    for (const system of frame.systems) {
      if (system.alpha <= 0.001) {
        continue
      }
      gl.uniformMatrix4fv(u.u_model, false, system.model)
      gl.uniform1f(u.u_scale, system.scale)
      gl.uniform1f(u.u_open, system.open)
      gl.uniform1f(u.u_alpha, system.alpha)
      gl.uniform1f(u.u_spin, system.spin)
      gl.uniform1f(u.u_frameAlpha, system.frameAlpha)
      gl.uniform3f(u.u_base, ...system.base)
      gl.uniform3f(u.u_accent, ...system.accent)
      gl.uniform1fv(u.u_trail, system.trail)
      gl.uniform1f(u.u_trailGain, system.trailGain)
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, (SEGMENTS + 1) * 2, STRAND_INSTANCES)
    }

    if (frame.curveCount > 0) {
      gl.useProgram(this.curveProgram)
      this.screen(this.curveUniforms, frame)
      gl.uniform1f(this.curveUniforms.u_time, frame.time)
      gl.bindVertexArray(this.curveVao)
      gl.bindBuffer(gl.ARRAY_BUFFER, this.curveBuffer)
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, frame.curves, 0, frame.curveCount * CURVE_FLOATS)
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, (CURVE_SEGMENTS + 1) * 2, frame.curveCount)
    }

    if (frame.spriteCount > 0) {
      gl.useProgram(this.spriteProgram)
      this.screen(this.spriteUniforms, { ...frame, pointer: [0, 0, 0] })
      gl.uniform1f(this.spriteUniforms.u_time, frame.time)
      gl.uniform1f(this.spriteUniforms.u_alpha, 1)
      gl.bindVertexArray(this.spriteVao)
      gl.bindBuffer(gl.ARRAY_BUFFER, this.spriteBuffer)
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, frame.sprites, 0, frame.spriteCount * SPRITE_FLOATS)
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, frame.spriteCount)
    }
    gl.bindVertexArray(null)
  }
}

/** Writes curves and sprites into the frame's arrays, counting as it goes. */
export class Batch {
  readonly curves = new Float32Array(MAX_CURVES * CURVE_FLOATS)
  readonly sprites = new Float32Array(MAX_SPRITES * SPRITE_FLOATS)
  curveCount = 0
  spriteCount = 0

  reset() {
    this.curveCount = 0
    this.spriteCount = 0
  }

  /**
   * A quadratic Bezier from p0 to p2 through the pull of p1. `head` places a comet along it
   * (-1 for none), `dash` makes it a dashed line moving at `speed`, and `from`/`to` draw a part.
   */
  curve(
    p0: readonly number[],
    p1: readonly number[],
    p2: readonly number[],
    color: RGB,
    alpha: number,
    options: {
      core?: number
      halo?: number
      head?: number
      tail?: number
      dash?: number
      speed?: number
      from?: number
      to?: number
    } = {}
  ) {
    if (this.curveCount >= MAX_CURVES || alpha <= 0.001) {
      return
    }
    const { core = 1, halo = 6, head = -1, tail = 0.12, dash = 0, speed = 0, from = 0, to = 1 } = options
    this.curves.set(
      [
        p0[0] ?? 0,
        p0[1] ?? 0,
        p0[2] ?? 0,
        p1[0] ?? 0,
        p1[1] ?? 0,
        p1[2] ?? 0,
        p2[0] ?? 0,
        p2[1] ?? 0,
        p2[2] ?? 0,
        color[0],
        color[1],
        color[2],
        alpha,
        core,
        halo,
        head,
        tail,
        dash,
        speed,
        from,
        to
      ],
      this.curveCount * CURVE_FLOATS
    )
    this.curveCount++
  }

  /** kind: 0 body, 1 machine ring, 2 star, 3 Atlas's core. `size` is half the sprite, in CSS px. */
  sprite(p: readonly number[], size: number, color: RGB, alpha: number, kind: number) {
    if (this.spriteCount >= MAX_SPRITES || alpha <= 0.001) {
      return
    }
    this.sprites.set(
      [p[0] ?? 0, p[1] ?? 0, p[2] ?? 0, size, color[0], color[1], color[2], alpha, kind],
      this.spriteCount * SPRITE_FLOATS
    )
    this.spriteCount++
  }
}

export const STRAND_TRAIL_SIZE = STRANDS
