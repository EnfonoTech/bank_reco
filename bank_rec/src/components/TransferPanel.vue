<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { BankTransaction, TransferCounterpart } from "@/types/bankRec";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import { formatDate, formatMoney } from "@/utils/format";
import ArrowLeftRight from "~icons/lucide/arrow-left-right";
import RefreshCcw from "~icons/lucide/refresh-cw";

const props = defineProps<{
  transaction?: BankTransaction;
  counterparts: TransferCounterpart[];
  loading?: boolean;
  creating?: boolean;
  error?: string;
  currency?: string;
}>();

const emit = defineEmits<{
  refresh: [];
  create: [
    payload: {
      counterpart_name?: string;
      counter_account?: string;
      reference_no?: string;
      remarks?: string;
    },
  ];
}>();

const selectedName = ref("");
const otherAccount = ref("");
const referenceNo = ref("");
const remarks = ref("");

const isOpen = computed(
  () => !!props.transaction && Number(props.transaction.unallocated_amount) > 0
);
const direction = computed(() =>
  props.transaction && Number(props.transaction.withdrawal) > 0
    ? "Money out of this account"
    : "Money into this account"
);
const canCreate = computed(
  () => isOpen.value && (!!selectedName.value || !!otherAccount.value.trim())
);

watch(
  () => [props.transaction?.name, props.counterparts] as const,
  () => {
    // a single close match is the one the user almost always wants
    selectedName.value =
      props.counterparts.length === 1 ? props.counterparts[0].name : "";
    otherAccount.value = "";
    referenceNo.value = props.transaction?.reference_number || "";
    remarks.value = "";
  },
  { immediate: true }
);

function submit() {
  if (!canCreate.value) {
    return;
  }
  emit("create", {
    counterpart_name: selectedName.value || undefined,
    counter_account: selectedName.value ? undefined : otherAccount.value.trim(),
    reference_no: referenceNo.value.trim() || undefined,
    remarks: remarks.value.trim() || undefined,
  });
}
</script>

<template>
  <div class="bank-rec-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
    <EmptyState
      v-if="!transaction"
      title="No transaction selected"
      detail="Select a bank transaction to transfer it to another account."
    />
    <EmptyState
      v-else-if="!isOpen"
      title="Already reconciled"
      detail="Only unreconciled transactions can be turned into a transfer."
    />
    <template v-else>
      <div class="flex items-start justify-between gap-3">
        <div>
          <div class="flex items-center gap-2 text-base font-semibold text-bank-ink">
            <ArrowLeftRight class="h-4 w-4 text-bank-accent" />
            Internal transfer
          </div>
          <div class="mt-1 text-sm text-bank-muted">
            {{ direction }}. Pick the matching transaction in the other bank
            account and both are reconciled to one Payment Entry.
          </div>
        </div>
        <Button variant="subtle" :loading="loading" @click="emit('refresh')">
          <template #prefix>
            <RefreshCcw class="h-4 w-4" />
          </template>
          Search again
        </Button>
      </div>

      <LoadingState v-if="loading && !counterparts.length" label="Looking for the mirror transaction" />
      <ErrorState v-else-if="error" :message="error" can-retry @retry="emit('refresh')" />

      <div v-else class="flex flex-col gap-2">
        <div class="text-xs font-medium uppercase tracking-wide text-bank-muted">
          Mirror transactions found
        </div>
        <div
          v-if="!counterparts.length"
          class="rounded-md border border-dashed border-bank-line px-3 py-4 text-sm text-bank-muted"
        >
          No unreconciled transaction of the same amount in another bank
          account within the match window. Enter the other account below, or
          import that account's statement first.
        </div>
        <label
          v-for="row in counterparts"
          :key="row.name"
          class="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 transition hover:bg-bank-accent-soft"
          :class="selectedName === row.name ? 'border-bank-accent bg-bank-accent-soft' : 'border-bank-line'"
        >
          <input
            v-model="selectedName"
            type="radio"
            class="mt-1"
            :value="row.name"
          />
          <div class="min-w-0 flex-1">
            <div class="truncate text-sm font-medium text-bank-ink">
              {{ row.description || row.reference_number || row.name }}
            </div>
            <div class="mt-1 flex flex-wrap gap-x-3 text-xs text-bank-muted">
              <span>{{ row.bank_account }}</span>
              <span>{{ formatDate(row.date) }}</span>
              <span v-if="row.days_apart">{{ row.days_apart }} day(s) apart</span>
              <span v-else>same day</span>
            </div>
          </div>
          <div class="text-sm font-semibold tabular-nums text-bank-ink">
            {{ formatMoney(row.amount, row.currency || currency) }}
          </div>
        </label>
      </div>

      <div class="grid gap-3 md:grid-cols-2">
        <FormControl
          v-model="otherAccount"
          type="text"
          label="Or other bank/cash account"
          variant="outline"
          size="md"
          placeholder="Ledger account, e.g. Petty Cash - ABC"
          :disabled="!!selectedName || creating"
        />
        <FormControl
          v-model="referenceNo"
          type="text"
          label="Reference"
          variant="outline"
          size="md"
          :disabled="creating"
        />
      </div>
      <FormControl
        v-model="remarks"
        type="textarea"
        label="Remarks"
        variant="outline"
        size="md"
        :rows="2"
        :disabled="creating"
      />

      <div class="flex justify-end">
        <Button
          variant="solid"
          :disabled="!canCreate"
          :loading="creating"
          @click="submit"
        >
          Create transfer and reconcile
        </Button>
      </div>
    </template>
  </div>
</template>
