/**
 * Client-side internationalization (i18n) engine.
 *
 * Design decisions:
 * - Translations live in flat JSON dictionaries under /static/i18n/<locale>.json.
 * - German (de) is the default locale and always loaded as fallback, so a
 *   missing key in another locale degrades gracefully instead of breaking the UI.
 * - Static markup is translated via data attributes:
 *     data-i18n="key"             -> replaces element textContent
 *     data-i18n-placeholder="key" -> sets input placeholder
 *     data-i18n-title="key"       -> sets title attribute
 * - Dynamically rendered strings (JS template literals) call t('key').
 * - Switching the locale persists to localStorage and reloads the page; a full
 *   reload is deliberate: it avoids re-render orchestration across all pages.
 */
const I18N = {
  STORAGE_KEY: 'ebm_locale',
  DEFAULT_LOCALE: 'de',
  supported: ['de', 'en'],

  locale: 'de',
  messages: {},
  fallback: {},
  ready: null,

  init() {
    const stored = localStorage.getItem(this.STORAGE_KEY);
    this.locale = this.supported.includes(stored) ? stored : this.DEFAULT_LOCALE;
    this.ready = this._load();
    return this.ready;
  },

  async _load() {
    try {
      const deResp = await fetch('/static/i18n/de.json');
      this.fallback = await deResp.json();
      if (this.locale === this.DEFAULT_LOCALE) {
        this.messages = this.fallback;
      } else {
        const resp = await fetch(`/static/i18n/${this.locale}.json`);
        this.messages = await resp.json();
      }
    } catch (err) {
      // Without translations the German inline markup still renders; log and continue.
      console.error('i18n: failed to load message catalog', err);
      this.messages = this.fallback = {};
    }
  },

  /**
   * Translate a key with optional {placeholder} interpolation.
   * Unknown keys return the key itself to make gaps visible during development.
   */
  t(key, params = {}) {
    let text = this.messages[key] ?? this.fallback[key] ?? key;
    for (const [name, value] of Object.entries(params)) {
      text = text.replaceAll(`{${name}}`, value);
    }
    return text;
  },

  /** Apply translations to all annotated elements below `root`. */
  apply(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = this.t(el.dataset.i18n);
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = this.t(el.dataset.i18nPlaceholder);
    });
    root.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = this.t(el.dataset.i18nTitle);
    });
    document.documentElement.lang = this.locale;
  },

  setLocale(locale) {
    if (!this.supported.includes(locale) || locale === this.locale) return;
    localStorage.setItem(this.STORAGE_KEY, locale);
    location.reload();
  },
};

/** Global shorthand used by page scripts for dynamically rendered strings. */
const t = (key, params) => I18N.t(key, params);

I18N.init();

document.addEventListener('DOMContentLoaded', async () => {
  await I18N.ready;
  I18N.apply();
  const picker = document.getElementById('locale-picker');
  if (picker) {
    picker.value = I18N.locale;
    picker.addEventListener('change', () => I18N.setLocale(picker.value));
  }
});
