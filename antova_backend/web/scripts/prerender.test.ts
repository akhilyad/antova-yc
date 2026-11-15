// @vitest-environment node
// The node environment has no window or document, so this also proves the server render
// touches neither at import or render time.
import { describe, expect, it } from 'vitest'
import { copy } from '../src/content/copy'
import { render } from '../src/entry-server.tsx'

describe('prerender', () => {
  it('prerendered index contains the hero and the sample table', () => {
    const html = render('/')
    expect(html).toContain(copy.landing.h1)
    expect(html).toContain('$1,385,000')
  })
})
