// Small WebGL2 helpers: programs, uniform lookups and instanced buffers.

export function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)
  if (!shader) {
    throw new Error('The browser could not create a shader.')
  }
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? ''
    gl.deleteShader(shader)
    throw new Error(`A shader did not compile: ${log}`)
  }
  return shader
}

export function program(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
  const result = gl.createProgram()
  gl.attachShader(result, compile(gl, gl.VERTEX_SHADER, vertex))
  gl.attachShader(result, compile(gl, gl.FRAGMENT_SHADER, fragment))
  gl.linkProgram(result)
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) {
    throw new Error(`A program did not link: ${gl.getProgramInfoLog(result) ?? ''}`)
  }
  return result
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
