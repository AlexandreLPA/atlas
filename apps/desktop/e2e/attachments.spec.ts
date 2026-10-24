import { expect, test } from '@playwright/test'

import { type Desktop, launch } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** A 1×1 PNG, real enough for the window to decode. */
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='

test('files dropped and picked become chips, go with the message, and come back reopened', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'I have the contract, the photo and the budget.' })
  const composer = page.getByTestId('composer')

  // Dropped, as from the system's file manager: a PDF and an image.
  const dropped = await page.evaluateHandle(png => {
    const transfer = new DataTransfer()
    const image = Uint8Array.from(atob(png), char => char.charCodeAt(0))
    transfer.items.add(new File(['%PDF-1.7\n% a contract\n'], 'contract.pdf', { type: 'application/pdf' }))
    transfer.items.add(new File([image], 'photo.png', { type: 'image/png' }))

    return transfer
  }, PNG)
  await composer.dispatchEvent('dragover', { dataTransfer: dropped })
  await expect(page.getByTestId('drop-zone')).toBeVisible()
  await composer.dispatchEvent('drop', { dataTransfer: dropped })
  await expect(page.getByTestId('drop-zone')).toBeHidden()

  // Picked with 📎: a spreadsheet, and a CSV taken off again.
  await page.getByTestId('attachment-picker').setInputFiles([
    { buffer: Buffer.from('PK\u0003\u0004 budget'), mimeType: 'application/octet-stream', name: 'budget.xlsx' },
    { buffer: Buffer.from('a,b\n1,2\n'), mimeType: 'text/csv', name: 'scratch.csv' }
  ])

  const chips = page.getByTestId('composer-attachments').getByTestId('attachment-chip')
  await expect(chips).toHaveCount(4)
  await chips.nth(3).hover()
  await page.getByRole('button', { name: 'Remove scratch.csv' }).click()
  await expect(chips).toHaveCount(3)
  // In the order they came, each marked by its own format and family.
  await expect(chips.nth(0)).toHaveAttribute('data-tone', 'pdf')
  await expect(chips.nth(1)).toHaveAttribute('data-tone', 'image')
  await expect(chips.nth(1).locator('img')).toBeVisible()
  await expect(chips.nth(2)).toHaveAttribute('data-tone', 'data')
  await expect(chips.nth(2)).toHaveAttribute('data-format', 'XLSX')

  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await box.fill('Compare these')
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled()
  await box.press('Enter')

  await expect(page.getByTestId('assistant-message')).toContainText('the contract, the photo and the budget')
  await expect(page.getByTestId('composer-attachments')).toBeHidden()
  const sent = page.getByTestId('message-attachments').getByTestId('attachment-chip')
  await expect(sent).toHaveCount(3)
  await expect(page.getByTestId('user-message')).toContainText('Compare these')

  // The model saw the photo as an image, and was told where the documents are and how to read them.
  const requests = await model.requests()
  const user = requests.at(-1)?.body?.messages?.at(-1)
  const parts = user?.content as { image_url?: { url: string }; text?: string; type: string }[]
  expect(parts.map(part => part.type)).toEqual(['text', 'image_url'])
  expect(parts[1]?.image_url?.url.startsWith('data:image/png;base64,')).toBe(true)
  expect(parts[0]?.text).toContain("The user sent a document: 'contract.pdf'")
  expect(parts[0]?.text).toContain("The user sent a document: 'budget.xlsx'")
  expect(parts[0]?.text).toContain('read_file')
  expect(parts[0]?.text).toContain('Compare these')

  // Reopened, the message has its files again.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await page.getByTestId('conversation-row').click()
  const reopened = page.getByTestId('message-attachments').getByTestId('attachment-chip')
  await expect(reopened).toHaveCount(3)
  await expect(reopened.nth(0)).toHaveAttribute('data-format', 'PDF')
  // The server is on this machine: a chip opens its file beside.
  await reopened.nth(0).getByRole('button', { name: 'Open contract.pdf' }).click()
  await expect(page.getByTestId('side-panel')).toBeVisible()
})

test('a file too heavy for its kind is refused at the drop, with the reason', async () => {
  const { page } = desktop
  const heavy = await page.evaluateHandle(() => {
    const transfer = new DataTransfer()
    transfer.items.add(new File([new Uint8Array(21 * 1024 * 1024)], 'long-call.mp3', { type: 'audio/mpeg' }))

    return transfer
  })
  await page.getByTestId('composer').dispatchEvent('drop', { dataTransfer: heavy })

  const chip = page.getByTestId('attachment-chip')
  await expect(chip).toHaveAttribute('data-tone', 'audio')
  await expect(chip).toContainText('long-call.mp3 is too heavy')
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()
})
