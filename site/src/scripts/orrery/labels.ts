// The orrery's labels are HTML over the canvas: crisp at any size, set in the site's type, and
// moved each frame to where their body is.
import { type Mat4, transform, type Vec3 } from './math'

export interface Label {
  key: string
  element: HTMLElement
  status: HTMLElement | null
  shown: boolean
}

export function createLabels(
  layer: HTMLElement,
  items: { key: string; text: string; kind: string; status?: boolean }[]
): Map<string, Label> {
  const labels = new Map<string, Label>()
  for (const item of items) {
    const element = document.createElement('span')
    element.className = `orrery-label orrery-label--${item.kind}`
    element.dataset['key'] = item.key
    const name = document.createElement('span')
    name.className = 'orrery-label-name'
    name.textContent = item.text
    element.append(name)
    let status: HTMLElement | null = null
    if (item.status) {
      status = document.createElement('span')
      status.className = 'orrery-label-status'
      element.append(status)
    }
    layer.append(element)
    labels.set(item.key, { key: item.key, element, status, shown: false })
  }
  return labels
}

/** Where a world point lands on the page, in CSS pixels, or null behind the camera. */
export function project(
  point: Vec3,
  viewProj: Mat4,
  shift: [number, number],
  width: number,
  height: number
): [number, number] | null {
  const [x, y, , w] = transform(viewProj, point)
  if (w <= 0.0001) {
    return null
  }
  const nx = x / w + shift[0]
  const ny = y / w + shift[1]
  return [(nx * 0.5 + 0.5) * width, (1 - (ny * 0.5 + 0.5)) * height]
}

export function place(label: Label, at: [number, number] | null, alpha: number) {
  const visible = at !== null && alpha > 0.02
  if (visible !== label.shown) {
    label.element.style.visibility = visible ? 'visible' : 'hidden'
    label.shown = visible
  }
  if (!visible) {
    return
  }
  label.element.style.transform = `translate3d(${at[0].toFixed(1)}px, ${at[1].toFixed(1)}px, 0)`
  label.element.style.opacity = Math.min(alpha, 1).toFixed(3)
}
