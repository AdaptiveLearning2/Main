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

  it('opens on a click and closes on another', async () => {
    const user = userEvent.setup()
    const { button, panel } = setup()
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(panel).toBeVisible()
    await user.click(button)
    expect(panel).not.toBeVisible()
  })

  it('opens from the keyboard, and Escape closes it with focus back on the button', async () => {
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
