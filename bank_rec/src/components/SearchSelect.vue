<script setup lang="ts">
import { computed, ref, watch } from "vue";

interface Option {
  value: string;
  label?: string;
}

const props = defineProps<{
  modelValue: string;
  options: Option[];
  placeholder?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const listId = "ss-" + Math.random().toString(36).slice(2, 9);
const text = ref("");

function labelFor(value: string) {
  const option = props.options.find((item) => item.value === value);
  return option ? option.label || option.value : value;
}

watch(
  () => [props.modelValue, props.options] as const,
  () => {
    text.value = props.modelValue ? labelFor(props.modelValue) : "";
  },
  { immediate: true }
);

const labelToValue = computed(() => {
  const map = new Map<string, string>();
  props.options.forEach((item) => map.set(item.label || item.value, item.value));
  return map;
});

function onInput(event: Event) {
  const typed = (event.target as HTMLInputElement).value;
  text.value = typed;
  if (typed === "") {
    emit("update:modelValue", "");
    return;
  }
  const value = labelToValue.value.get(typed);
  if (value !== undefined) {
    emit("update:modelValue", value);
  }
}

function onBlur() {
  // an unfinished entry falls back to the last valid choice
  text.value = props.modelValue ? labelFor(props.modelValue) : "";
}
</script>

<template>
  <div>
    <input
      class="h-8 w-full rounded border border-bank-line bg-bank-panel px-2.5 text-sm text-bank-ink placeholder:text-bank-subtle focus:border-bank-accent focus:outline-none disabled:opacity-60"
      type="text"
      autocomplete="off"
      :list="listId"
      :value="text"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="onInput"
      @blur="onBlur"
    />
    <datalist :id="listId">
      <option
        v-for="option in options"
        :key="option.value"
        :value="option.label || option.value"
      />
    </datalist>
  </div>
</template>
