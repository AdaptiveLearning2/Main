import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import InfoTip from './InfoTip'

function setup() {
  render(<InfoTip label="Focus">How settled the readings were.</InfoTip>)
  return {
    button: screen.getByRole('button', { name: 'What is Focus?' }),
    panel: screen.getByText('How settled the readings were.'),
  }
}

describe('InfoTip', () => {
  it('starts closed, and the button names what it explains', () => {
    const { button, panel } = setup()
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(panel).not.toBeVisible()
    expect(button).toHaveAttribute('aria-controls', panel.id)
  })

  // jsdom applies no Tailwind: a `block` class would beat `hidden` in a browser while this passes.
  it('carries no display class that would show a closed panel', async () => {
    const user = userEvent.setup()
    const { button, panel } = setup()
    expect(panel).toHaveClass('hidden')
    expect(panel).not.toHaveClass('block')
    await user.click(button)
    expect(panel).toHaveClass('block')
    expect(panel).not.toHaveClass('hidden')
  })

  it('opens on a click and closes on another', async () => {
    const user = userEvent.setup()
    const { button, panel } = setup()
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(panel).toBeVisible()
    await user.click(button)
    expect(panel).not.toBeVisible()
  })

  it('opens from the keyboard, and Escape closes it with focus still on the button', async () => {
    const user = userEvent.setup()
    const { button, panel } = setup()
    await user.tab()
    expect(button).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(panel).toBeVisible()
    await user.keyboard('{Escape}')
    expect(panel).not.toBeVisible()
    expect(button).toHaveFocus()
  })
})
