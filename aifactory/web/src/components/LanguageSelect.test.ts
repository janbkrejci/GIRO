import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { locale, setLocale } from '@/lib/i18n'
import { chooseOption, openSelect } from '@/test/select'
import LanguageSelect from './LanguageSelect.vue'

enableAutoUnmount(afterEach)
beforeEach(() => setLocale('en'))
afterEach(() => {
  setLocale('en')
  localStorage.clear()
  document.body.innerHTML = ''
})

const SELECTOR = '[data-test="language-select"]'

describe('LanguageSelect', () => {
  it.each([['en', 'Language'], ['cs', 'Jazyk']] as const)(
    'shows only the Languages SVG with a localized name and tooltip in %s',
    async (language, label) => {
      setLocale(language)
      const wrapper = mount(LanguageSelect, { attachTo: document.body })
      const trigger = wrapper.get(SELECTOR)
      expect(trigger.attributes('aria-label')).toBe(label)
      expect(trigger.attributes('aria-expanded')).toBe('false')
      expect(trigger.text()).toBe('')
      expect(trigger.findAll('svg')).toHaveLength(1)
      expect(trigger.get('svg').classes()).toContain('lucide-languages')
      expect(trigger.get('svg').attributes('aria-hidden')).toBe('true')
      expect(trigger.find('.select-value').exists()).toBe(false)
      expect(trigger.find('.chevron').exists()).toBe(false)
      await wrapper.get('[data-test="tip-anchor"]').trigger('mouseenter')
      await flushPromises()
      expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe(label)
    },
  )

  it('keeps both options, marks the selection and synchronizes persisted mouse and keyboard choices', async () => {
    const wrapper = mount({ components: { LanguageSelect }, template: '<div><LanguageSelect /><LanguageSelect /></div>' }, { attachTo: document.body })
    const [first, second] = wrapper.findAll(SELECTOR)
    const options = await openSelect(wrapper, SELECTOR)
    expect(options.map((o) => o.textContent?.trim())).toEqual(['English', 'Čeština'])
    expect(options.map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false'])
    await chooseOption(wrapper, SELECTOR, 'cs')
    expect(locale.value).toBe('cs')
    expect(localStorage.getItem('giro.dashboard.locale')).toBe('cs')
    for (const trigger of [first!, second!]) {
      expect(trigger.attributes('data-value')).toBe('cs')
      expect(trigger.attributes('aria-label')).toBe('Jazyk')
      expect(trigger.text()).toBe('')
    }
    const selected = await openSelect(wrapper, SELECTOR)
    expect(selected.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'true'])
    await first!.trigger('keydown', { key: 'Escape' })
    ;(second!.element as HTMLButtonElement).focus()
    await second!.trigger('keydown', { key: 'ArrowDown' })
    await second!.trigger('keydown', { key: 'Home' })
    await second!.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(locale.value).toBe('en')
    expect(localStorage.getItem('giro.dashboard.locale')).toBe('en')
    expect(first!.attributes('data-value')).toBe('en')
    expect(second!.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(second!.element)
  })
})
