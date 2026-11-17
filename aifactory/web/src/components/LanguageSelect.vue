<script setup lang="ts">
import { locale, setLocale, t } from '@/lib/i18n'
import SelectMenu from './ui/SelectMenu.vue'
import Tooltip from './ui/Tooltip.vue'
</script>

<template>
  <Tooltip :text="t('shell.language')">
    <SelectMenu
      :model-value="locale"
      :options="[{ value: 'en', label: 'English' }, { value: 'cs', label: 'Čeština' }]"
      :label="t('shell.language')"
      class="language-select"
      data-test="language-select"
      @update:model-value="setLocale"
    >
      <template #trigger>
        <span class="language-code" aria-hidden="true">{{ locale.toUpperCase() }}</span>
      </template>
    </SelectMenu>
  </Tooltip>
</template>

<style>
.select-trigger.language-select {
  justify-content: center;
  width: 36px;
  min-width: 36px;
  height: 36px;
  padding: 0;
  border-radius: 8px;
  background: var(--panel-2);
  transition: background 0.15s ease, border-color 0.15s ease;
}

[data-theme="light"] .language-select {
  background: rgba(255, 255, 255, 0.7);
}

.language-code {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.select-trigger.language-select:hover:not(:disabled) {
  background: var(--panel-3);
  border-color: var(--border);
}

[data-theme="light"] .select-trigger.language-select:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.9);
}
</style>
