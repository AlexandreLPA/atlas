import { useStore } from '@nanostores/react'
import { Dialog, DropdownMenu } from 'radix-ui'
import { type ReactElement, useRef, useState } from 'react'

import { t } from '@/i18n'
import {
  $home,
  $homeNotice,
  $homes,
  $machine,
  $picture,
  $pictureError,
  $remote,
  backUpHome,
  chooseBackup,
  dismissHomeNotice,
  type HomeNotice,
  newHome,
  openHome,
  openMachine,
  setPicture
} from '@/store/app'
import { $machines } from '@/store/machines'
import { openSettings } from '@/store/settings'

import {
  Check,
  ChevronDown,
  Close,
  Computer,
  Download,
  Machine,
  Photo,
  Plus,
  Settings,
  Trash,
  Upload,
  UserCircle
} from './ui/icons'
import { ICON_BUTTON, IconButton } from './ui/tip'

/**
 * The home on screen, always (step 10c, docs/ARCHITECTURE.md, Homes in the app), at the foot of the
 * sidebar even with only the default home: its picture and
 * its name (asked for by the user on 18/10/2026). A
 * click opens its menu: the homes to switch to, a new one, the home's own dialog (its picture, a
 * backup, an import). The light or dark look moved to the settings screen (step 15b). Choosing another home opens it, and its server
 * starts if it is not running. Against a remote server, which serves one home on another machine,
 * making, backing up and importing are off and say the terminal's way.
 *
 * **Above it, the machine** (step 15, docs/ARCHITECTURE.md, Several machines), only once another
 * machine is in the list: this computer
 * and each machine, its home beside its name; machine → home → conversations. Beside the home, the
 * gear opens the settings screen, and the machine's menu
 * opens it on Machines.
 */
export function HomeBar() {
  const home = useStore($home)
  const notice = useStore($homeNotice)
  const picture = useStore($picture)
  const [editing, setEditing] = useState(false)
  const name = home ?? ''

  return (
    <div className="space-y-2 border-t border-border p-2" data-testid="home-bar">
      <MachinePicker />
      <div className="flex items-center gap-1">
        <HomeMenu
          onEdit={() => {
            setEditing(true)
          }}
        >
          <button
            aria-label={t.homes.menu(name)}
            className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-selected"
            data-home={name}
            data-testid="home-button"
            type="button"
          >
            <HomePicture name={name} picture={picture} size="sm" />
            <span className="min-w-0 flex-1 truncate font-medium">{name}</span>
            <ChevronDown aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
          </button>
        </HomeMenu>
        <IconButton
          icon={Settings}
          label={t.settings.title}
          onClick={() => {
            openSettings()
          }}
          testId="settings-open"
        />
      </div>
      {notice && !editing && <Notice notice={notice} />}
      <HomeProfile name={name} onOpenChange={setEditing} open={editing} picture={picture} />
    </div>
  )
}

/** The machine on screen, and the others to switch to. */
function MachinePicker() {
  const machines = useStore($machines)
  const machine = useStore($machine)

  if (machines.length === 0) {
    return null
  }

  const named = machines.find(each => each.id === machine)
  const label = named ? named.name : t.machines.thisComputer

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          aria-label={t.machines.selector(label)}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs text-soft hover:bg-selected"
          data-machine={named?.name ?? ''}
          data-testid="machine-button"
          type="button"
        >
          {named ? (
            <Machine aria-hidden className="shrink-0" size={14} stroke={1.75} />
          ) : (
            <Computer aria-hidden className="shrink-0" size={14} stroke={1.75} />
          )}
          <span className="min-w-0 flex-1 truncate">{label}</span>
          <ChevronDown aria-hidden className="shrink-0 text-muted-foreground" size={12} stroke={1.75} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          className="z-50 w-60 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          data-testid="machine-menu"
          side="top"
          sideOffset={6}
        >
          <DropdownMenu.Item
            className={ITEM}
            data-machine=""
            data-testid="machine-option"
            onSelect={() => void openMachine(null)}
          >
            <Computer aria-hidden size={15} stroke={1.75} />
            <span className="min-w-0 flex-1 truncate">{t.machines.thisComputer}</span>
            {machine === null && <Check aria-hidden size={14} stroke={2} />}
          </DropdownMenu.Item>
          {machines.map(each => (
            <DropdownMenu.Item
              className={ITEM}
              data-machine={each.name}
              data-testid="machine-option"
              key={each.id}
              onSelect={() => void openMachine(each.id)}
            >
              <Machine aria-hidden size={15} stroke={1.75} />
              <span className="min-w-0 flex-1 truncate">
                {each.name} <span className="text-muted-foreground">· {each.home}</span>
              </span>
              {machine === each.id && <Check aria-hidden size={14} stroke={2} />}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Separator className="my-1 h-px bg-selected" />
          <DropdownMenu.Item
            className={ITEM}
            data-testid="machine-settings"
            onSelect={() => {
              openSettings('machines')
            }}
          >
            <Settings aria-hidden size={15} stroke={1.75} />
            {t.machines.settings}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-40 data-highlighted:bg-muted'

function HomeMenu({ children, onEdit }: { children: ReactElement; onEdit: () => void }) {
  const homes = useStore($homes)
  const home = useStore($home)
  const remote = useStore($remote)
  // Until the server listed the homes, the one on screen is all there is to show.
  const names = homes.length > 0 ? homes.map(row => row.name) : home ? [home] : []

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{children}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          className="z-50 w-60 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          data-testid="home-menu"
          side="top"
          sideOffset={6}
        >
          <DropdownMenu.Label className="px-2 pt-1 pb-0.5 text-xs text-muted-foreground">
            {t.homes.homes}
          </DropdownMenu.Label>
          {names.map(name => (
            <DropdownMenu.Item
              className={ITEM}
              data-current={name === home}
              data-home={name}
              data-testid="home-option"
              key={name}
              onSelect={() => {
                if (name !== home) {
                  void openHome(name)
                }
              }}
            >
              <span className="min-w-0 flex-1 truncate">{name}</span>
              {name === home && <Check aria-hidden size={14} stroke={2} />}
            </DropdownMenu.Item>
          ))}
          <DropdownMenu.Item
            className={ITEM}
            data-testid="home-new"
            disabled={remote}
            onSelect={newHome}
            title={remote ? t.homes.remote : undefined}
          >
            <Plus aria-hidden size={15} stroke={1.75} />
            {t.homes.newHome}
          </DropdownMenu.Item>
          <DropdownMenu.Separator className="my-1 h-px bg-selected" />
          <DropdownMenu.Item className={ITEM} data-testid="home-profile" onSelect={onEdit}>
            <UserCircle aria-hidden size={15} stroke={1.75} />
            {t.homes.editProfile}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** The home's dialog: its picture, its name, a backup and an import. */
function HomeProfile({
  name,
  onOpenChange,
  open,
  picture
}: {
  name: string
  onOpenChange: (open: boolean) => void
  open: boolean
  picture: null | string
}) {
  const notice = useStore($homeNotice)
  const refused = useStore($pictureError)
  const [unreadable, setUnreadable] = useState(false)
  const chooser = useRef<HTMLInputElement>(null)
  const remote = useStore($remote)
  const off = remote ? t.homes.remote : undefined

  return (
    <Dialog.Root onOpenChange={onOpenChange} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-50 w-[22rem] -translate-x-1/2 -translate-y-1/2 space-y-4 rounded-xl border border-border bg-raised p-5 text-foreground shadow-xl"
          data-testid="home-profile-dialog"
        >
          <div className="flex items-start justify-between">
            <Dialog.Title className="font-semibold">{t.homes.profileTitle}</Dialog.Title>
            <Dialog.Close aria-label={t.homes.dismiss} className={ICON_BUTTON}>
              <Close aria-hidden size={16} stroke={1.75} />
            </Dialog.Close>
          </div>
          <div className="flex items-center gap-4">
            <button
              aria-label={t.homes.picture}
              className="rounded-full"
              onClick={() => chooser.current?.click()}
              type="button"
            >
              <HomePicture name={name} picture={picture} size="lg" />
            </button>
            <div className="min-w-0 space-y-2">
              <p className="truncate text-lg font-medium" data-testid="home-profile-name">
                {name}
              </p>
              <div className="flex gap-1">
                <input
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  className="hidden"
                  data-testid="home-picture-input"
                  onChange={event => {
                    const file = event.target.files?.[0]
                    event.target.value = ''

                    if (file) {
                      setUnreadable(false)
                      void squarePng(file).then(
                        png => setPicture(png),
                        () => {
                          setUnreadable(true)
                        }
                      )
                    }
                  }}
                  ref={chooser}
                  type="file"
                />
                <button
                  className="flex items-center gap-1.5 rounded-md border border-border-strong px-2 py-1 text-xs hover:bg-muted"
                  data-testid="home-picture"
                  onClick={() => chooser.current?.click()}
                  type="button"
                >
                  <Photo aria-hidden size={14} stroke={1.75} />
                  {t.homes.picture}
                </button>
                {picture && (
                  <IconButton
                    icon={Trash}
                    label={t.homes.pictureRemove}
                    onClick={() => void setPicture(null)}
                    testId="home-picture-remove"
                  />
                )}
              </div>
            </div>
          </div>
          {(unreadable || refused) && (
            <p className="text-xs text-failed-text" role="alert">
              {unreadable ? t.homes.pictureUnreadable : refused}
            </p>
          )}
          <p className="text-xs text-muted-foreground">{t.homes.profileHint}</p>
          <div className="flex gap-2">
            <button
              className={BUTTON}
              data-testid="home-backup"
              disabled={notice?.kind === 'backing-up'}
              onClick={() => void backUpHome()}
              title={t.homes.backupTitle}
              type="button"
            >
              <Download aria-hidden size={15} stroke={1.75} />
              {t.homes.backup}
            </button>
            <button
              className={BUTTON}
              data-testid="home-import"
              disabled={remote || notice?.kind === 'checking'}
              onClick={() => {
                onOpenChange(false)
                void chooseBackup()
              }}
              title={off ?? t.homes.importTooltip}
              type="button"
            >
              <Upload aria-hidden size={15} stroke={1.75} />
              {t.homes.import}
            </button>
          </div>
          {notice && <Notice notice={notice} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** The home's picture, or its initial on a plain disc when it has none. */
function HomePicture({ name, picture, size }: { name: string; picture: null | string; size: 'lg' | 'sm' }) {
  const box = size === 'lg' ? 'h-16 w-16 text-2xl' : 'h-7 w-7 text-sm'

  return picture ? (
    <img
      alt=""
      className={`${box} shrink-0 rounded-full object-cover`}
      data-testid="home-picture-image"
      src={picture}
    />
  ) : (
    <span
      aria-hidden
      className={`${box} grid shrink-0 place-items-center rounded-full bg-border-strong font-semibold text-soft uppercase`}
    >
      {name.slice(0, 1)}
    </span>
  )
}

/** The side of the square kept: enough for the sidebar and the dialog, a few KB as a PNG. */
const PICTURE_SIDE = 256

/** An image file as the home keeps it: the square at its centre, 256 pixels a side, a PNG in
 * base64. Rejects for a file the window cannot read as an image. */
async function squarePng(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file)
  const side = Math.min(bitmap.width, bitmap.height)
  const canvas = document.createElement('canvas')
  canvas.width = PICTURE_SIDE
  canvas.height = PICTURE_SIDE
  const context = canvas.getContext('2d')

  if (!context) {
    throw new Error('No canvas to draw the picture on.')
  }

  context.drawImage(
    bitmap,
    (bitmap.width - side) / 2,
    (bitmap.height - side) / 2,
    side,
    side,
    0,
    0,
    PICTURE_SIDE,
    PICTURE_SIDE
  )
  bitmap.close()

  return canvas.toDataURL('image/png').replace(/^data:image\/png;base64,/, '')
}

function Notice({ notice }: { notice: HomeNotice }) {
  const busy = notice.kind === 'backing-up' || notice.kind === 'checking'
  const text =
    notice.kind === 'backing-up'
      ? t.homes.backingUp
      : notice.kind === 'checking'
        ? t.homes.checking
        : notice.kind === 'backed-up'
          ? t.homes.backedUp(notice.written.path, size(notice.written.size))
          : notice.message

  return (
    <div
      className={`flex items-start gap-2 rounded-md px-2 py-1.5 text-xs [overflow-wrap:anywhere] ${
        notice.kind === 'failed' ? 'bg-failed/10 text-failed-text' : 'bg-muted text-soft'
      }`}
      data-kind={notice.kind}
      data-testid="home-notice"
      role={notice.kind === 'failed' ? 'alert' : 'status'}
    >
      <span className="flex-1">{text}</span>
      {!busy && (
        <button aria-label={t.homes.dismiss} className="shrink-0" onClick={dismissHomeNotice} type="button">
          <Close aria-hidden size={12} stroke={2} />
        </button>
      )}
    </div>
  )
}

/** A backup's size as `atlas backup` says it: megabytes, or kilobytes under one. */
export function size(bytes: number): string {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${String(Math.max(1, Math.round(bytes / 1e3)))} KB`
}

const BUTTON =
  'flex flex-1 items-center justify-center gap-1.5 truncate rounded-md border border-border-strong px-2 py-1.5 text-sm hover:bg-muted disabled:opacity-40'
