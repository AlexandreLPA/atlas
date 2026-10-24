import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { BackupDownloads } from './backup-down'

const made: string[] = []

afterEach(() => {
  for (const folder of made.splice(0)) {
    rmSync(folder, { force: true, recursive: true })
  }
})

function folder(): string {
  const created = mkdtempSync(path.join(tmpdir(), 'atlas-backup-down-'))
  made.push(created)

  return created
}

const part = (text: string) => Buffer.from(text).toString('base64')

describe("a remote machine's backup, written here", () => {
  it('goes where the save dialog said, whole, the parts in order', async () => {
    const target = path.join(folder(), 'backup.zip')
    writeFileSync(target, 'an older backup the dialog asked to replace')
    const downloads = new BackupDownloads()
    downloads.allow(target)

    await downloads.write(target, 0, part('first '))
    await downloads.write(target, 6, part('second'))
    expect(readFileSync(target, 'utf8')).toBe('an older backup the dialog asked to replace')
    await downloads.finish(target, true)

    expect(readFileSync(target, 'utf8')).toBe('first second')
    expect(existsSync(`${target}.part`)).toBe(false)
    await expect(downloads.write(target, 0, part('again'))).rejects.toThrow('save dialog')
  })

  it('leaves nothing when it fails halfway', async () => {
    const target = path.join(folder(), 'backup.zip')
    const downloads = new BackupDownloads()
    downloads.allow(target)

    await downloads.write(target, 0, part('half'))
    await downloads.finish(target, false)

    expect(existsSync(target)).toBe(false)
    expect(existsSync(`${target}.part`)).toBe(false)
  })

  it('writes nowhere the save dialog did not give', async () => {
    const downloads = new BackupDownloads()
    const elsewhere = path.join(folder(), 'startup.bat')

    await expect(downloads.write(elsewhere, 0, part('x'))).rejects.toThrow('save dialog')
    await expect(downloads.finish(elsewhere, true)).rejects.toThrow('save dialog')
    expect(existsSync(`${elsewhere}.part`)).toBe(false)
  })
})
