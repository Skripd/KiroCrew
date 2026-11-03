import { render, fireEvent, act } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Lightbox } from '../components/markdown/Lightbox'

// The viewer can open from inside an opaque full-screen shell at z-[9999] (an
// image artifact in the side panel's full screen, #28760). At equal z-index
// that shell, a later body portal, paints over the viewer, so the viewer sits
// one layer above it, and its actions menu one layer above the viewer.
// jsdom does no stacking, so the classes are the observable fact.

const open = () => window.dispatchEvent(new CustomEvent('lightbox', {
  detail: { images: [{ src: '/api/artifacts/diagram/asset', alt: 'Architecture diagram' }], index: 0 },
}))

const zOf = (el: Element | null) => Number(/(?:^|\s)z-\[(\d+)\]/.exec(el?.className ?? '')?.[1] ?? NaN)

afterEach(() => { vi.unstubAllGlobals() })

describe('Lightbox stacking', () => {
  it('opens above a z-[9999] full-screen shell', () => {
    const { container } = render(<Lightbox />)
    act(() => { open() })
    const overlay = container.querySelector('[role="button"]')
    expect(overlay).not.toBeNull()
    expect(zOf(overlay)).toBeGreaterThan(9999)
  })

  it('opens its actions menu above the viewer itself', () => {
    // The menu is only drawn where the image can be copied.
    class FakeClipboardItem { constructor(public items: Record<string, unknown>) {} }
    vi.stubGlobal('ClipboardItem', FakeClipboardItem)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { write: vi.fn(async () => {}) } })
    const { container } = render(<Lightbox />)
    act(() => { open() })
    const trigger = container.querySelector('[data-testid="lightbox-actions-menu"]') as HTMLElement
    act(() => {
      fireEvent.pointerDown(trigger, { button: 0 })
      fireEvent.pointerUp(trigger)
      fireEvent.click(trigger)
    })
    const menu = document.body.querySelector('[role="menu"]')
    expect(menu).not.toBeNull()
    expect(zOf(menu)).toBeGreaterThan(zOf(container.querySelector('[role="button"]')))
  })
})
