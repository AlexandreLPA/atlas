import { afterEach, describe, expect, it } from 'vitest'

import { $executorOrder, inOrder, moveBlock, setExecutorOrder } from './order'

afterEach(() => {
  $executorOrder.set({})
  localStorage.clear()
})

describe("the executors' order the user dragged", () => {
  it('puts the rows it names in its order, and the others after them as they came', () => {
    expect(inOrder(['a', 'b', 'c', 'd'], name => name, ['c', 'a'])).toEqual(['c', 'a', 'b', 'd'])
    expect(inOrder(['a', 'b'], name => name, [])).toEqual(['a', 'b'])
  })

  it('moves a block whole, before or after its anchor, and leaves it be on itself', () => {
    expect(moveBlock(['a', 'b', 'c', 'd'], ['c', 'd'], 'a', false)).toEqual(['c', 'd', 'a', 'b'])
    expect(moveBlock(['a', 'b', 'c', 'd'], ['a'], 'c', true)).toEqual(['b', 'c', 'a', 'd'])
    expect(moveBlock(['a', 'b', 'c'], ['a', 'b'], 'b', true)).toEqual(['a', 'b', 'c'])
  })

  it('keeps it per home, those not shown now after the ones shown', () => {
    setExecutorOrder('work', ['a', 'b', 'c'])
    setExecutorOrder('work', ['c', 'a'])
    setExecutorOrder('play', ['x'])

    expect($executorOrder.get()).toEqual({ play: ['x'], work: ['c', 'a', 'b'] })
    expect(JSON.parse(localStorage.getItem('atlas.executorOrder') ?? '{}')).toEqual($executorOrder.get())
  })
})
