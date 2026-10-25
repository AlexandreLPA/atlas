// The little linear algebra the orrery needs: column-major 4x4 matrices, as WebGL reads them.

export type Vec3 = [number, number, number]
export type Mat4 = Float32Array

export function perspective(fovy: number, aspect: number, near: number, far: number): Mat4 {
  const f = 1 / Math.tan(fovy / 2)
  const nf = 1 / (near - far)
  const m = new Float32Array(16)
  m[0] = f / aspect
  m[5] = f
  m[10] = (far + near) * nf
  m[11] = -1
  m[14] = 2 * far * near * nf
  return m
}

export function lookAt(eye: Vec3, target: Vec3, up: Vec3): Mat4 {
  let zx = eye[0] - target[0]
  let zy = eye[1] - target[1]
  let zz = eye[2] - target[2]
  let len = Math.hypot(zx, zy, zz) || 1
  zx /= len
  zy /= len
  zz /= len
  let xx = up[1] * zz - up[2] * zy
  let xy = up[2] * zx - up[0] * zz
  let xz = up[0] * zy - up[1] * zx
  len = Math.hypot(xx, xy, xz) || 1
  xx /= len
  xy /= len
  xz /= len
  const yx = zy * xz - zz * xy
  const yy = zz * xx - zx * xz
  const yz = zx * xy - zy * xx
  const m = new Float32Array(16)
  m[0] = xx
  m[1] = yx
  m[2] = zx
  m[4] = xy
  m[5] = yy
  m[6] = zy
  m[8] = xz
  m[9] = yz
  m[10] = zz
  m[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2])
  m[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2])
  m[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2])
  m[15] = 1
  return m
}

/** a × b. */
export function multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Float32Array(16)
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let sum = 0
      for (let k = 0; k < 4; k++) {
        sum += (a[k * 4 + row] ?? 0) * (b[col * 4 + k] ?? 0)
      }
      out[col * 4 + row] = sum
    }
  }
  return out
}

/** A uniform scale, then a translation. */
export function placement(offset: Vec3, scale: number): Mat4 {
  const m = new Float32Array(16)
  m[0] = scale
  m[5] = scale
  m[10] = scale
  m[12] = offset[0]
  m[13] = offset[1]
  m[14] = offset[2]
  m[15] = 1
  return m
}

/** A point through a matrix, to clip space: [x, y, z, w]. */
export function transform(m: Mat4, p: Vec3): [number, number, number, number] {
  const [x, y, z] = p
  const at = (i: number) => m[i] ?? 0
  return [
    at(0) * x + at(4) * y + at(8) * z + at(12),
    at(1) * x + at(5) * y + at(9) * z + at(13),
    at(2) * x + at(6) * y + at(10) * z + at(14),
    at(3) * x + at(7) * y + at(11) * z + at(15)
  ]
}

export const clamp = (v: number, lo = 0, hi = 1) => Math.min(Math.max(v, lo), hi)
export const mix = (a: number, b: number, t: number) => a + (b - a) * t
/** Ken Perlin's smootherstep, from 0 at `edge0` to 1 at `edge1`. */
export function smoother(edge0: number, edge1: number, v: number): number {
  const t = clamp((v - edge0) / (edge1 - edge0))
  return t * t * t * (t * (t * 6 - 15) + 10)
}
export function smooth(edge0: number, edge1: number, v: number): number {
  const t = clamp((v - edge0) / (edge1 - edge0))
  return t * t * (3 - 2 * t)
}
