/**
 * A remote machine's backup, written on this machine (step 15, docs/ARCHITECTURE.md, Several
 * machines): the zip comes down the connection in parts (`home.backup_part`), the window hands each
 * one here, and it is written beside the file the save dialog chose, then put in its place whole.
 *
 * - **Only where the save dialog said**: the window names the file, and a file no dialog gave is
 *   refused, so a page that reached the window cannot write anywhere it likes.
 * - **Whole or not at all**: the parts go to `<file>.part`, renamed once the last one came; a backup
 *   that failed halfway leaves nothing, and never a cut zip under the chosen name.
 */
import { appendFile, rename, rm, writeFile } from 'node:fs/promises'

export class BackupDownloads {
  private readonly chosen = new Set<string>()

  /** A file the save dialog gave. */
  allow(file: string): void {
    this.chosen.add(file)
  }

  /** One part, in base64, at `offset`: the first starts the file over. */
  async write(file: unknown, offset: unknown, data: unknown): Promise<void> {
    const target = this.target(file)
    const bytes = Buffer.from(String(data), 'base64')

    if (offset === 0) {
      await writeFile(`${target}.part`, bytes)
    } else {
      await appendFile(`${target}.part`, bytes)
    }
  }

  /** The last part came (`whole`), or the backup failed: in its place, or gone. */
  async finish(file: unknown, whole: unknown): Promise<void> {
    const target = this.target(file)
    this.chosen.delete(target)

    if (whole === true) {
      await rename(`${target}.part`, target)
    } else {
      await rm(`${target}.part`, { force: true })
    }
  }

  private target(file: unknown): string {
    if (typeof file !== 'string' || !this.chosen.has(file)) {
      throw new Error('A backup is written only where the save dialog said.')
    }

    return file
  }
}
