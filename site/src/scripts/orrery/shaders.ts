// The orrery's GLSL. Lines are drawn as strips whose points the vertex shader computes itself:
// the sphere, the orbits and every moment between the two cost the CPU nothing per frame.
import {
  CURVE_SEGMENTS,
  FRAME_RADII,
  FRAME_RINGS,
  ORBIT_INNER,
  ORBIT_OUTER,
  SEGMENTS,
  SPHERE_RINGS,
  SPIN,
  STRANDS
} from './model'

const f = (v: number) => v.toFixed(5)

/** Screen-space helpers every program shares: pixels, the pointer's pull and the scene's shift. */
const SCREEN = /* glsl */ `
uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec2 u_shift;
uniform vec2 u_resolution;
uniform float u_dpr;
uniform vec3 u_pointer;

vec4 toClip(vec3 world) {
  vec4 clip = u_proj * u_view * vec4(world, 1.0);
  clip.xy += u_shift * clip.w;
  return clip;
}

vec2 toScreen(vec4 clip) {
  return (clip.xy / clip.w * 0.5 + 0.5) * u_resolution;
}

vec4 fromScreen(vec2 px, vec4 clip) {
  return vec4((px / u_resolution * 2.0 - 1.0) * clip.w, clip.z, clip.w);
}

// The pointer bends what passes near it, as a mass bends light.
vec2 pull(vec2 px) {
  vec2 d = u_pointer.xy - px;
  float r = 160.0 * u_dpr;
  float k = exp(-dot(d, d) / (2.0 * r * r)) * u_pointer.z;
  return px + d * k * 0.32;
}
`

export const strandVertex = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in float a_u;
layout(location = 1) in float a_side;
layout(location = 2) in vec2 a_strand;

${SCREEN}
uniform mat4 u_model;
uniform float u_scale;
uniform float u_time;
uniform float u_open;
uniform vec2 u_globe;
uniform float u_reveal;
uniform float u_alpha;
uniform float u_spin;
uniform float u_frameAlpha;
uniform vec3 u_base;
uniform vec3 u_accent;
uniform float u_trail[${STRANDS}];
uniform float u_trailGain;

out float v_offset;
out float v_core;
out float v_halo;
out vec4 v_color;

const float PI = 3.14159265359;
const float N = ${f(STRANDS)};

float rankOf(float i) {
  float d = i - (N - 1.0) * 0.5;
  if (abs(d) < 0.5) return 0.0;
  return d < 0.0 ? -2.0 * d - 1.0 : 2.0 * d;
}

mat3 globeRotation() {
  float cy = cos(u_globe.x), sy = sin(u_globe.x);
  float cx = cos(u_globe.y), sx = sin(u_globe.y);
  mat3 yaw = mat3(cy, 0.0, -sy, 0.0, 1.0, 0.0, sy, 0.0, cy);
  mat3 pitch = mat3(1.0, 0.0, 0.0, 0.0, cx, sx, 0.0, -sx, cx);
  return yaw * pitch;
}

// A strand's point: on the sphere, on its orbit, or between the two by e. The near half of a
// strand runs over the front of the sphere and becomes the near half of its orbit.
vec3 strandPoint(float i, float h, float u, float e, mat3 rot, out float angle) {
  float th = h < 0.5 ? PI * (1.0 - u) : PI * (1.0 + u);
  vec3 sphere;
  vec3 orbit;
  float lift = 0.0;
  if (i > N - 0.5) {
    float k = i - N;
    float r = mix(${f(SPHERE_RINGS[0])}, ${f(SPHERE_RINGS[1])}, k);
    sphere = vec3(r * cos(th), r * sin(th), 0.0);
    float a = mix(${f(FRAME_RADII[0])}, ${f(FRAME_RADII[1])}, k);
    orbit = vec3(a * cos(th), 0.0, a * sin(th));
  } else {
    float fr = (i + 0.5) / N;
    float y0 = (2.0 * fr - 1.0) * 0.9;
    // The tips stop short of the sphere's edge: there the path runs straight into the screen, and
    // a turned globe would curl it into a bright hook.
    float hw = sqrt(max(1.0 - y0 * y0, 0.0)) * 0.95;
    float x = mix(-hw, hw, u);
    float y = y0 + sin(u * 2.0 * PI + u_time * 0.5 + i * 0.62) * 0.13 * sin(u * PI);
    float r2 = x * x + y * y;
    if (r2 > 0.998) {
      float s = sqrt(0.998 / r2);
      x *= s;
      y *= s;
    }
    float z = sqrt(max(1.0 - x * x - y * y, 0.0)) * (h < 0.5 ? 1.0 : -1.0);
    sphere = vec3(x, y, z);
    float a = ${f(ORBIT_OUTER)} - (${f(ORBIT_OUTER - ORBIT_INNER)}) * rankOf(i) / (N - 1.0);
    float turn = th + u_time * ${f(SPIN)} * pow(a, -1.25) * u_spin;
    orbit = vec3(a * cos(turn), 0.0, a * sin(turn));
    lift = (2.0 * fr - 1.0) * 0.32;
  }
  angle = atan(orbit.z, orbit.x);
  vec3 p = mix(rot * sphere, orbit, e);
  p.y += lift * sin(e * PI);
  return p;
}

void main() {
  float i = a_strand.x;
  float h = a_strand.y;
  bool frame = i > N - 0.5;
  float rank = frame ? 0.0 : rankOf(i);
  float e = smoothstep(0.0, 1.0, clamp(u_open * 1.7 - rank / (N - 1.0) * 0.7, 0.0, 1.0));
  mat3 rot = globeRotation();
  float du = 1.0 / ${f(SEGMENTS)};
  float angle;
  float ignored;
  vec3 p = strandPoint(i, h, a_u, e, rot, angle);
  vec3 pPrev = strandPoint(i, h, max(a_u - du, 0.0), e, rot, ignored);
  vec3 pNext = strandPoint(i, h, min(a_u + du, 1.0), e, rot, ignored);

  vec4 world = u_model * vec4(p, 1.0);
  vec4 clip = toClip(world.xyz);
  vec2 sp = pull(toScreen(clip));
  vec2 spPrev = pull(toScreen(toClip((u_model * vec4(pPrev, 1.0)).xyz)));
  vec2 spNext = pull(toScreen(toClip((u_model * vec4(pNext, 1.0)).xyz)));
  vec2 dir = spNext - spPrev;
  dir = length(dir) > 1e-4 ? normalize(dir) : vec2(1.0, 0.0);
  vec2 normal = vec2(-dir.y, dir.x);

  bool accent = !frame && rank < 0.5;
  float core = (accent ? 1.75 : 1.15) * u_dpr;
  float halo = 7.0 * u_dpr;
  gl_Position = fromScreen(sp + normal * a_side * halo, clip);

  float fr = (i + 0.5) / N;
  float sphereAlpha = frame ? 0.62 : 0.24 + 0.5 * sin(fr * PI);
  float orbitAlpha = frame ? 0.34 * u_frameAlpha : 0.17 + 0.24 * (1.0 - rank / (N - 1.0));
  float alpha = mix(sphereAlpha, orbitAlpha, e);

  // Nearer is brighter: the depth of the point against the system's own centre.
  vec4 viewP = u_view * world;
  vec4 viewC = u_view * u_model * vec4(0.0, 0.0, 0.0, 1.0);
  float extent = mix(1.1, 2.9, e) * u_scale;
  float nearness = clamp(0.5 + 0.5 * (viewP.z - viewC.z) / extent, 0.0, 1.0);
  alpha *= mix(0.26, 1.0, nearness);

  // On the sphere, the two halves of a strand meet at its ends: fade them there so the meeting
  // does not add up to a bright point. Open, the halves join into one ring and need no fade.
  float ends = smoothstep(0.0, 0.07, a_u) * (1.0 - smoothstep(0.93, 1.0, a_u));
  alpha *= mix(ends, 1.0, e);

  // On load, each strand grows from its middle outward.
  float reach = clamp(u_reveal * 1.6 - (frame ? 0.6 : rank / (N - 1.0) * 0.6), 0.0, 1.0);
  alpha *= 1.0 - smoothstep(reach - 0.06, reach, abs(a_u - 0.5) * 2.0);

  // A body riding this orbit leaves a glow behind it.
  vec3 color = accent ? u_accent : u_base;
  if (!frame) {
    float body = u_trail[int(i + 0.5)];
    if (body > -50.0) {
      float behind = mod(body - angle, 2.0 * PI);
      float glow = exp(-behind * 1.4) * e * u_trailGain;
      alpha += glow * 0.85;
    }
  }

  v_color = vec4(color, alpha * u_alpha);
  v_offset = a_side * halo;
  v_core = core;
  v_halo = halo;
}
`

export const lineFragment = /* glsl */ `#version 300 es
precision highp float;
in float v_offset;
in float v_core;
in float v_halo;
in vec4 v_color;
out vec4 outColor;

void main() {
  float d = abs(v_offset);
  float core = 1.0 - smoothstep(v_core * 0.5 - 0.6, v_core * 0.5 + 0.6, d);
  float halo = exp(-(d * d) / (v_halo * v_halo * 0.1)) * 0.3;
  float a = clamp((core + halo) * v_color.a, 0.0, 1.0);
  outColor = vec4(v_color.rgb * a, a);
}
`

export const curveVertex = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in float a_u;
layout(location = 1) in float a_side;
layout(location = 2) in vec3 a_p0;
layout(location = 3) in vec3 a_p1;
layout(location = 4) in vec3 a_p2;
layout(location = 5) in vec4 a_color;
layout(location = 6) in vec4 a_line;
layout(location = 7) in vec4 a_extra;

${SCREEN}

out float v_offset;
out float v_core;
out float v_halo;
out vec4 v_color;
out float v_t;
out vec4 v_pulse;

vec3 bezier(float t) {
  float s = 1.0 - t;
  return s * s * a_p0 + 2.0 * s * t * a_p1 + t * t * a_p2;
}

void main() {
  float t0 = a_extra.z;
  float t1 = a_extra.w;
  float t = mix(t0, t1, a_u);
  float dt = max((t1 - t0) / ${f(CURVE_SEGMENTS)}, 1e-4);
  vec4 clip = toClip(bezier(t));
  vec2 sp = pull(toScreen(clip));
  vec2 spPrev = pull(toScreen(toClip(bezier(max(t - dt, 0.0)))));
  vec2 spNext = pull(toScreen(toClip(bezier(min(t + dt, 1.0)))));
  vec2 dir = spNext - spPrev;
  dir = length(dir) > 1e-4 ? normalize(dir) : vec2(1.0, 0.0);
  vec2 normal = vec2(-dir.y, dir.x);
  float halo = a_line.y * u_dpr;
  gl_Position = fromScreen(sp + normal * a_side * halo, clip);
  v_offset = a_side * halo;
  v_core = a_line.x * u_dpr;
  v_halo = halo;
  v_color = a_color;
  v_t = t;
  v_pulse = vec4(a_line.z, a_line.w, a_extra.x, a_extra.y);
}
`

export const curveFragment = /* glsl */ `#version 300 es
precision highp float;
uniform float u_time;
in float v_offset;
in float v_core;
in float v_halo;
in vec4 v_color;
in float v_t;
in vec4 v_pulse;
out vec4 outColor;

void main() {
  float d = abs(v_offset);
  float core = 1.0 - smoothstep(v_core * 0.5 - 0.6, v_core * 0.5 + 0.6, d);
  float halo = exp(-(d * d) / (v_halo * v_halo * 0.1)) * 0.3;
  float body = v_color.a;
  if (v_pulse.z > 0.0) {
    body *= 0.35 + 0.65 * step(0.5, fract(v_t * v_pulse.z - u_time * v_pulse.w));
  }
  float comet = 0.0;
  if (v_pulse.x >= 0.0) {
    float behind = v_pulse.x - v_t;
    comet = behind >= 0.0 ? exp(-behind / max(v_pulse.y, 1e-3)) : exp(behind * 80.0);
  }
  float a = clamp((core + halo) * (body + comet * 1.5), 0.0, 1.0);
  outColor = vec4(v_color.rgb * a, a);
}
`

export const spriteVertex = /* glsl */ `#version 300 es
precision highp float;
layout(location = 0) in vec2 a_corner;
layout(location = 1) in vec3 a_pos;
layout(location = 2) in float a_size;
layout(location = 3) in vec4 a_color;
layout(location = 4) in float a_kind;

${SCREEN}
uniform float u_time;
uniform float u_alpha;

out vec2 v_uv;
out vec4 v_color;
out float v_kind;

void main() {
  vec4 clip = toClip(a_pos);
  vec2 sp = toScreen(clip);
  vec4 color = a_color;
  if (a_kind > 1.5 && a_kind < 2.5) {
    float rate = 0.5 + fract(a_pos.x * 7.13 + a_pos.z * 3.7) * 1.6;
    color.a *= 0.62 + 0.38 * sin(u_time * rate + a_pos.y * 3.1);
  }
  gl_Position = fromScreen(sp + a_corner * a_size * u_dpr, clip);
  v_uv = a_corner;
  v_color = vec4(color.rgb, color.a * u_alpha);
  v_kind = a_kind;
}
`

export const spriteFragment = /* glsl */ `#version 300 es
precision highp float;
in vec2 v_uv;
in vec4 v_color;
in float v_kind;
out vec4 outColor;

void main() {
  float r = length(v_uv);
  float a;
  if (v_kind < 0.5) {
    // A body: a solid disc in a soft glow.
    a = (1.0 - smoothstep(0.17, 0.21, r)) + exp(-r * r * 8.0) * 0.42;
  } else if (v_kind < 1.5) {
    // A machine: a ring around a point.
    float ring = 1.0 - smoothstep(0.03, 0.065, abs(r - 0.6));
    a = ring * 0.95 + (1.0 - smoothstep(0.1, 0.14, r)) + exp(-r * r * 5.0) * 0.22;
  } else if (v_kind < 2.5) {
    a = exp(-r * r * 14.0);
  } else {
    // Atlas: a bright core whose light reaches far.
    a = exp(-r * r * 60.0) * 1.3 + exp(-r * r * 7.0) * 0.38 + 0.07 / (1.0 + r * r * 40.0);
  }
  a *= v_color.a * (1.0 - smoothstep(0.88, 1.0, r));
  outColor = vec4(v_color.rgb * a, a);
}
`

/** How many logo rings ride along with the strands. */
export const STRAND_INSTANCES = (STRANDS + FRAME_RINGS) * 2
