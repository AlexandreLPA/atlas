/**
 * The files dropped, pasted or picked into the box, before they are sent (step 16g).
 *
 * - **A file becomes a chip above the box at once** and starts sending: it is on the server, in the
 *   home's `attachments/`, before the message leaves. It
 *   always goes as its bytes, in parts a WebSocket frame holds, so a container or another machine
 *   gets it as this one does: one way for every server, even one that shares the disk.
 * - **What the server would refuse is refused at the drop**, with the reason.
 * - **A large image is made smaller first**, to 2048 px on its longer side, under the
 *   5 MB a model takes.
 * - **Each kind is marked its own way**: its format in a colored badge, an image by its thumbnail
 *   (`formatOf`).
 */
import { map } from 'nanostores'

import { t } from '@/i18n'
import type { AttachmentLimits, AttachmentRow } from '@/protocol/protocol.generated'

import { current, currentOwner } from './app'

/** What an attachment is, as the server says (`atlas.models.AttachmentKind`). */
export type AttachmentKind = AttachmentRow['kind']

/** A chip above the box: a file on its way, sent, or refused. */
export interface Pending {
  /** The server's attachment, once the whole file arrived. */
  attachment: AttachmentRow | null
  error: string
  /** The chip's own id, before the server gives one. */
  key: string
  kind: AttachmentKind
  name: string
  /** The share of the file sent, 0 to 1. */
  progress: number
  size: number
  status: 'error' | 'ready' | 'sending'
  /** An image's thumbnail, a small `data:` URL (the window's policy takes no `blob:` image). */
  thumbnail: string
}

/** The chips of each conversation's box (`DRAFT` for a new one). */
export const $pending = map<Record<string, Pending[] | undefined>>({})

/** The longer side an attached image is made down to. */
export const IMAGE_SIDE = 2048

/** The extensions read as each kind, as the server reads them (`atlas.conversations.identify`). */
const KINDS: Record<string, AttachmentKind> = {
  ...Object.fromEntries(
    ['pdf', 'doc', 'docx', 'odt', 'rtf', 'epub', 'ppt', 'pptx', 'odp', 'xls', 'xlsx', 'ods'].map(ext => [
      ext,
      'document'
    ])
  ),
  ...Object.fromEntries(['png', 'jpg', 'jpeg', 'gif', 'webp'].map(ext => [ext, 'image'])),
  ...Object.fromEntries(['mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v'].map(ext => [ext, 'video'])),
  ...Object.fromEntries(['mp3', 'wav', 'm4a', 'ogg', 'flac', 'aac', 'opus'].map(ext => [ext, 'audio'])),
  ...Object.fromEntries(
    [
      'txt',
      'md',
      'csv',
      'log',
      'json',
      'xml',
      'yaml',
      'yml',
      'toml',
      'ini',
      'cfg',
      'py',
      'ts',
      'tsx',
      'js',
      'jsx',
      'html',
      'css',
      'sh',
      'ps1',
      'sql',
      'java',
      'go',
      'rs',
      'c',
      'cpp',
      'h'
    ].map(ext => [ext, 'text'])
  )
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')

  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** What a file looks like from here, by its type and name; the server's word replaces it. */
export function kindOf(name: string, type: string): AttachmentKind {
  const known = KINDS[extensionOf(name)]

  if (known) {
    return known
  }

  if (type.startsWith('image/')) {
    // An image no provider reads (HEIC, TIFF, SVG): the server keeps it as a file.
    return 'other'
  }

  for (const kind of ['video', 'audio', 'text'] as const) {
    if (type.startsWith(`${kind}/`)) {
      return kind
    }
  }

  return 'other'
}

/** The families a format's badge is colored by. */
export type Tone =
  'archive' | 'audio' | 'code' | 'data' | 'ebook' | 'image' | 'pdf' | 'slides' | 'text' | 'video' | 'word'

const TONES: Record<string, Tone> = {
  ...Object.fromEntries(['pdf'].map(ext => [ext, 'pdf'])),
  ...Object.fromEntries(['doc', 'docx', 'odt', 'rtf'].map(ext => [ext, 'word'])),
  ...Object.fromEntries(['xls', 'xlsx', 'ods', 'csv', 'tsv'].map(ext => [ext, 'data'])),
  ...Object.fromEntries(['ppt', 'pptx', 'odp', 'key'].map(ext => [ext, 'slides'])),
  ...Object.fromEntries(['epub', 'mobi'].map(ext => [ext, 'ebook'])),
  ...Object.fromEntries(['txt', 'md', 'log'].map(ext => [ext, 'text'])),
  ...Object.fromEntries(['zip', 'rar', '7z', 'tar', 'gz'].map(ext => [ext, 'archive']))
}

/** A file's badge: its format in a few letters, and the family that colors it. */
export function formatOf(name: string, kind: AttachmentKind): { label: string; tone: Tone } {
  const ext = extensionOf(name)
  const label = (ext || kind).slice(0, 4).toUpperCase()
  const tone = TONES[ext]

  if (tone) {
    return { label, tone }
  }

  switch (kind) {
    case 'image':
      return { label, tone: 'image' }
    case 'video':
      return { label, tone: 'video' }
    case 'audio':
      return { label, tone: 'audio' }
    case 'document':
      return { label, tone: 'word' }
    case 'text':
      return { label, tone: ['json', 'xml', 'yaml', 'yml', 'toml'].includes(ext) ? 'data' : 'code' }
    case 'other':
      return { label: ext ? label : 'FILE', tone: 'archive' }
  }
}

/** Whether `size` bytes of `kind` go past the server's limits; the most it takes, or `null`. */
export function overLimit(kind: AttachmentKind, size: number, limits: AttachmentLimits): null | number {
  return size > limits[kind] ? limits[kind] : null
}

let limits: AttachmentLimits | null = null
let counter = 0

async function readLimits(): Promise<AttachmentLimits> {
  limits ??= await current().client.call('attachment.limits', {})

  return limits
}

function update(owner: string, key: string, change: Partial<Pending>): void {
  const chips = $pending.get()[owner] ?? []
  $pending.setKey(
    owner,
    chips.map(chip => (chip.key === key ? { ...chip, ...change } : chip))
  )
}

/** Put files on the box of what is on screen, and send each to the server. */
export function attach(files: File[]): void {
  const owner = currentOwner()

  for (const file of files) {
    counter += 1
    const kind = kindOf(file.name, file.type)
    const chip: Pending = {
      attachment: null,
      error: '',
      key: `attachment-${String(counter)}`,
      kind,
      name: file.name || 'file',
      progress: 0,
      size: file.size,
      status: 'sending',
      thumbnail: ''
    }
    $pending.setKey(owner, [...($pending.get()[owner] ?? []), chip])
    void upload(owner, chip, file)

    if (kind === 'image') {
      void thumbnailOf(file).then(thumbnail => {
        update(owner, chip.key, { thumbnail })
      })
    }
  }
}

async function upload(owner: string, chip: Pending, file: File): Promise<void> {
  try {
    const most = await readLimits()
    const blob = chip.kind === 'image' ? await smaller(file, most.image) : file
    // An image made into a JPEG is named as one.
    const name = blob !== file && blob.type === 'image/jpeg' ? `${chip.name.replace(/\.[^.]+$/, '')}.jpg` : chip.name
    const over = overLimit(chip.kind, blob.size, most)

    if (over !== null) {
      throw new Error(t.attachments.tooHeavy(chip.name, t.attachments.size(over)))
    }

    const data = new Uint8Array(await blob.arrayBuffer())
    let id = ''
    let offset = 0

    // An empty file still goes: one empty part completes it.
    do {
      const part = data.subarray(offset, offset + most.part)
      const added = await current().client.call(
        'attachment.add',
        { data: base64(part), name, offset, size: data.length, upload: id },
        120_000
      )
      id = added.upload
      offset = added.received

      if (added.attachment) {
        update(owner, chip.key, {
          attachment: added.attachment,
          kind: added.attachment.kind,
          name: added.attachment.name,
          progress: 1,
          size: added.attachment.size,
          status: 'ready'
        })

        return
      }

      update(owner, chip.key, { progress: data.length ? offset / data.length : 1 })
    } while (offset < data.length)
  } catch (error) {
    update(owner, chip.key, { error: error instanceof Error ? error.message : String(error), status: 'error' })
  }
}

/** Take a chip off the box. */
export function detach(owner: string, key: string): void {
  const chips = $pending.get()[owner] ?? []
  $pending.setKey(
    owner,
    chips.filter(chip => chip.key !== key)
  )
}

/** The box's attachments once sent: what is ready goes with the message, and the chips leave. */
export function takeReady(owner: string): AttachmentRow[] {
  const chips = $pending.get()[owner] ?? []
  const ready = chips.filter(chip => chip.status === 'ready' && chip.attachment)

  for (const chip of ready) {
    detach(owner, chip.key)
  }

  return ready.flatMap(chip => (chip.attachment ? [chip.attachment] : []))
}

/** The chips go back to a box whose message did not reach the server. */
export function putBack(owner: string, attachments: AttachmentRow[]): void {
  const back = attachments.map((attachment): Pending => {
    counter += 1

    return {
      attachment,
      error: '',
      key: `attachment-${String(counter)}`,
      kind: attachment.kind,
      name: attachment.name,
      progress: 1,
      size: attachment.size,
      status: 'ready',
      thumbnail: ''
    }
  })
  $pending.setKey(owner, [...back, ...($pending.get()[owner] ?? [])])
}

function base64(bytes: Uint8Array): string {
  let text = ''

  // In slices: one call with every byte as an argument overflows the stack past ~100 KB.
  for (let at = 0; at < bytes.length; at += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(at, at + 0x8000))
  }

  return btoa(text)
}

/** An image made down to `IMAGE_SIDE` on its longer side, or under `most` bytes; a GIF (it may move),
 * one already small and one this window cannot decode go as they are, for the server to judge. */
async function smaller(file: File, most: number): Promise<Blob> {
  if (file.type === 'image/gif' || typeof createImageBitmap === 'undefined') {
    return file
  }

  let bitmap: ImageBitmap

  try {
    bitmap = await createImageBitmap(file)
  } catch {
    return file
  }

  try {
    const scale = Math.min(1, IMAGE_SIDE / Math.max(bitmap.width, bitmap.height))

    if (scale === 1 && file.size <= most) {
      return file
    }

    const canvas = new OffscreenCanvas(Math.round(bitmap.width * scale), Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')

    if (!context) {
      return file
    }

    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    // A PNG keeps its transparency when it fits; anything else, or a PNG that does not, is a JPEG.
    const kept = file.type === 'image/png' ? await canvas.convertToBlob({ type: 'image/png' }) : null

    return kept && kept.size <= most ? kept : await canvas.convertToBlob({ quality: 0.88, type: 'image/jpeg' })
  } finally {
    bitmap.close()
  }
}

/** The side of an image's thumbnail, twice the chip's tile for a sharp screen. */
const THUMBNAIL_SIDE = 72

/** An image's thumbnail as a small PNG `data:` URL; empty when this window cannot decode it. */
async function thumbnailOf(file: File): Promise<string> {
  if (typeof createImageBitmap === 'undefined') {
    return ''
  }

  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, THUMBNAIL_SIDE / Math.max(bitmap.width, bitmap.height))
    const canvas = new OffscreenCanvas(
      Math.max(1, Math.round(bitmap.width * scale)),
      Math.max(1, Math.round(bitmap.height * scale))
    )
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    const blob = await canvas.convertToBlob({ type: 'image/png' })

    return await new Promise<string>(resolve => {
      const reader = new FileReader()
      reader.onload = () => {
        resolve(typeof reader.result === 'string' ? reader.result : '')
      }
      reader.onerror = () => {
        resolve('')
      }
      reader.readAsDataURL(blob)
    })
  } catch {
    return ''
  }
}
