import { act, fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Lightbox } from '../components/markdown/Lightbox'
import { FOCUSABLE } from '../hooks/useDialogFocusTrap'

// The viewer can open from inside another focus trap (the full-screen artifact
// panel), so it takes focus itself through the shared dialog trap: focus moves
// in, Tab stays within its controls, and focus goes back on close.

const open = () => window.dispatchEvent(new CustomEvent('lightbox', {
  detail: { images: [{ src: '/x.png', alt: 'x' }], index: 0 },
}))

/** jsdom lays nothing out, so every element reports a null offsetParent and the
 *  trap would see no visible focusable. Give the viewer's controls one. */
function markVisible(root: HTMLElement) {
  for (const el of root.querySelectorAll<HTMLElement>(FOCUSABLE)) {
    Object.defineProperty(el, 'offsetParent', { configurable: true, get: () => root })
  }
}

function openViewer() {
  const utils = render(<><button type="button">Outside</button><Lightbox /></>)
  const outside = utils.container.querySelector('button') as HTMLButtonElement
  outside.focus()
  act(() => { open() })
  const toolbar = utils.container.querySelector('[data-testid="lightbox-toolbar"]') as HTMLElement
  const overlay = toolbar.parentElement as HTMLElement
  markVisible(overlay)
  const controls = Array.from(overlay.querySelectorAll<HTMLElement>(FOCUSABLE))
  return { ...utils, outside, overlay, first: controls[0], last: controls[controls.length - 1] }
}

describe('Lightbox focus', () => {
  it('moves focus into the viewer when it opens', () => {
    const { overlay, outside } = openViewer()
    expect(overlay.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).not.toBe(outside)
  })

  it('wraps Tab and Shift+Tab within the viewer', () => {
    const { first, last } = openViewer()
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('keeps Shift+Tab inside when the overlay itself holds focus', () => {
    // A click on the image at fit zoom focuses the overlay, not a control.
    const { overlay, last } = openViewer()
    overlay.focus()
    fireEvent.keyDown(overlay, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('gives focus back to the opener on close', () => {
    const { container, last, outside } = openViewer()
    fireEvent.keyDown(last, { key: 'Escape' })
    expect(container.querySelector('[data-testid="lightbox-toolbar"]')).toBeNull()
    expect(document.activeElement).toBe(outside)
  })
})
