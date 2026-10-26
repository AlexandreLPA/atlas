// Small WebGL2 helpers: programs, uniform lookups and instanced buffers.

/** Shaders compiled and a program linked, without asking how it went: asking waits for the driver. */
export function startProgram(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const result = gl.createProgram()
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment]
  ] as const) {
    const shader = gl.createShader(type)
    if (!shader) {
      throw new Error('The browser could not create a shader.')
    }
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    gl.attachShader(result, shader)
  }
  gl.linkProgram(result)
  return result
}

/** A program started by `startProgram`, once linked; says why when it did not. */
export function finishProgram(gl: WebGL2RenderingContext, result: WebGLProgram): WebGLProgram {
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) {
    const shaders = gl.getAttachedShaders(result) ?? []
    const logs = shaders.map(shader => gl.getShaderInfoLog(shader) ?? '').filter(Boolean)
    throw new Error(`A program did not link: ${[gl.getProgramInfoLog(result) ?? '', ...logs].join(' ')}`)
  }
  return result
}

/**
 * Resolves once the driver has linked every program, frame by frame, so the page never waits on
 * it: with KHR_parallel_shader_compile the browser compiles beside the page. Without it, at once:
 * the first query then waits, as it always did.
 */
export function whenLinked(gl: WebGL2RenderingContext, programs: WebGLProgram[]): Promise<void> {
  const parallel = gl.getExtension('KHR_parallel_shader_compile') as { COMPLETION_STATUS_KHR: number } | null
  if (!parallel) {
    return Promise.resolve()
  }
  return new Promise(resolve => {
    const poll = () => {
      if (programs.every(prog => gl.getProgramParameter(prog, parallel.COMPLETION_STATUS_KHR) === true)) {
        resolve()
      } else {
        requestAnimationFrame(poll)
      }
    }
    poll()
  })
}

export type Uniforms<K extends string> = Record<K, WebGLUniformLocation | null>

export function uniforms<K extends string>(gl: WebGL2RenderingContext, prog: WebGLProgram, names: K[]): Uniforms<K> {
  const out = {} as Uniforms<K>
  for (const name of names) {
    out[name] = gl.getUniformLocation(prog, name)
  }
  return out
}

/** A static buffer bound to attributes, one per vertex. */
export function vertexBuffer(
  gl: WebGL2RenderingContext,
  data: Float32Array,
  layout: { location: number; size: number }[]
): WebGLBuffer {
  const buffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW)
  bindLayout(gl, layout, 0)
  return buffer
}

/** A buffer of per-instance attributes, written again whenever the scene changes. */
export function instanceBuffer(
  gl: WebGL2RenderingContext,
  data: Float32Array,
  layout: { location: number; size: number }[],
  usage: number
): WebGLBuffer {
  const buffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
  gl.bufferData(gl.ARRAY_BUFFER, data, usage)
  bindLayout(gl, layout, 1)
  return buffer
}

function bindLayout(gl: WebGL2RenderingContext, layout: { location: number; size: number }[], divisor: number) {
  const stride = layout.reduce((sum, item) => sum + item.size, 0) * 4
  let offset = 0
  for (const { location, size } of layout) {
    gl.enableVertexAttribArray(location)
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset)
    gl.vertexAttribDivisor(location, divisor)
    offset += size * 4
  }
}
