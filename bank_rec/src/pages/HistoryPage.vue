<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import BankAccountFilters from "@/components/BankAccountFilters.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import { getActionHistory, undoAction, undoBatch } from "@/services/api";
import { useBankRecStore } from "@/stores/bankRec";
import type { ActionBatch, ActionLogRow, UndoResponse } from "@/types/bankRec";
import { deskRoute } from "@/utils/desk";
import { formatDate, formatMoney } from "@/utils/format";
import ExternalLink from "~icons/lucide/external-link";
import Undo2 from "~icons/lucide/undo-2";

const store = useBankRecStore();
const route = useRoute();
const router = useRouter();

const batches = ref<ActionBatch[]>([]);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const busyKey = ref("");
const mineOnly = ref(true);

const rowCount = computed(() =>
  batches.value.reduce((total, batch) => total + batch.rows.length, 0)
);

async function replaceQuery() {
  await router.replace({
    path: route.path,
    query: {
      company: store.selectedCompany || undefined,
      bank_account: store.selectedBankAccount || undefined,
      from_date: store.fromDate || undefined,
      to_date: store.toDate || undefined,
    },
  });
}

async function load() {
  if (!store.selectedBankAccount) {
    batches.value = [];
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const response = await getActionHistory({
      bank_account: store.selectedBankAccount,
      from_date: store.fromDate,
      to_date: store.toDate,
      mine_only: mineOnly.value,
    });
    batches.value = response.batches;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to load history.";
  } finally {
    loading.value = false;
  }
}

async function updateFilter(
  field: "selectedCompany" | "selectedBankAccount" | "fromDate" | "toDate",
  value: string
) {
  if (field === "selectedCompany") {
    await store.changeCompany(value);
  } else {
    store[field] = value;
  }
  await replaceQuery();
  await load();
}

function summarise(response: UndoResponse) {
  const failed = response.results.filter((result) => result.status === "error");
  if (failed.length) {
    error.value = failed.map((result) => result.message).join(" ");
  }
  notice.value = response.summary.undone
    ? `${response.summary.undone} transaction(s) un-reconciled.`
    : "";
}

async function undoRow(row: ActionLogRow) {
  busyKey.value = row.name;
  error.value = "";
  notice.value = "";
  try {
    summarise(await undoAction(row.name));
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to undo.";
  } finally {
    busyKey.value = "";
    await load();
  }
}

async function undoWholeBatch(batch: ActionBatch) {
  if (
    !window.confirm(
      `Undo all ${batch.active_count} active action(s) in this batch? Vouchers created by it are cancelled.`
    )
  ) {
    return;
  }
  busyKey.value = batch.batch_id;
  error.value = "";
  notice.value = "";
  try {
    summarise(await undoBatch(batch.batch_id));
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to undo.";
  } finally {
    busyKey.value = "";
    await load();
  }
}

function voucherUrl(row: ActionLogRow) {
  return row.voucher_type && row.voucher_name
    ? deskRoute(row.voucher_type, row.voucher_name)
    : "";
}

function formatStamp(value: string) {
  return value ? value.slice(0, 16).replace("T", " ") : "";
}

onMounted(async () => {
  await store.initialize(route.query);
  await load();
});
</script>

<template>
  <div class="flex min-h-0 w-full flex-col gap-4">
    <BankAccountFilters
      :companies="store.allowedCompanies"
      :selected-company="store.selectedCompany"
      :bank-accounts="store.bankAccounts"
      :selected-bank-account="store.selectedBankAccount"
      :from-date="store.fromDate"
      :to-date="store.toDate"
      :loading="store.loading.bankAccounts || loading"
      @update:selected-company="updateFilter('selectedCompany', $event)"
      @update:selected-bank-account="updateFilter('selectedBankAccount', $event)"
      @update:from-date="updateFilter('fromDate', $event)"
      @update:to-date="updateFilter('toDate', $event)"
      @refresh="load"
    />

    <section class="flex min-h-[560px] flex-col rounded-lg border border-bank-line bg-bank-panel shadow-sm">
      <div class="flex flex-col gap-2 border-b border-bank-line px-4 py-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div class="text-base font-semibold text-bank-ink">Reconciliation history</div>
          <div class="text-sm tabular-nums text-bank-muted">
            {{ rowCount }} action(s) in {{ batches.length }} batch(es)
          </div>
        </div>
        <label class="flex items-center gap-2 text-sm text-bank-ink">
          <input v-model="mineOnly" type="checkbox" @change="load" />
          Only my actions
        </label>
      </div>

      <div v-if="notice" class="border-b border-bank-line bg-surface-green-2 px-4 py-2 text-sm text-ink-green-3">
        {{ notice }}
      </div>

      <LoadingState v-if="loading" label="Loading history" />
      <ErrorState v-else-if="error" :message="error" can-retry @retry="load" />
      <EmptyState
        v-else-if="!batches.length"
        title="No actions recorded"
        detail="Matches, created vouchers, bank coding, rules and transfers show up here once you reconcile."
      />
      <div v-else class="bank-rec-scrollbar min-h-0 flex-1 overflow-auto">
        <div
          v-for="batch in batches"
          :key="batch.batch_id"
          class="border-b border-bank-line last:border-b-0"
        >
          <div class="flex flex-wrap items-center justify-between gap-2 bg-bank-muted-surface px-4 py-2 text-xs text-bank-muted">
            <div>
              {{ formatStamp(batch.creation) }} · {{ batch.owner }} ·
              {{ batch.rows.length }} row(s)
            </div>
            <Button
              v-if="batch.rows.length > 1 && batch.active_count"
              size="sm"
              variant="subtle"
              :loading="busyKey === batch.batch_id"
              @click="undoWholeBatch(batch)"
            >
              <template #prefix>
                <Undo2 class="h-3.5 w-3.5" />
              </template>
              Undo batch
            </Button>
          </div>
          <table class="min-w-full text-sm">
            <tbody class="divide-y divide-bank-line">
              <tr v-for="row in batch.rows" :key="row.name" :class="row.status === 'Undone' ? 'opacity-60' : ''">
                <td class="w-28 px-4 py-2 tabular-nums text-bank-muted">
                  {{ formatDate(row.transaction_date) }}
                </td>
                <td class="max-w-[320px] px-4 py-2">
                  <div class="truncate font-medium text-bank-ink">
                    {{ row.transaction_description || row.bank_transaction }}
                  </div>
                  <div v-if="row.details" class="truncate text-xs text-bank-muted">
                    {{ row.details }}
                  </div>
                </td>
                <td class="px-4 py-2">
                  <Badge :theme="row.action_type === 'Matched' ? 'blue' : 'green'">
                    {{ row.action_type }}
                  </Badge>
                </td>
                <td class="px-4 py-2">
                  <a
                    v-if="voucherUrl(row)"
                    class="inline-flex items-center gap-1 text-bank-accent hover:underline"
                    :href="voucherUrl(row)"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {{ row.voucher_name }}
                    <ExternalLink class="h-3 w-3" />
                  </a>
                </td>
                <td class="px-4 py-2 text-right tabular-nums text-bank-ink">
                  {{ formatMoney(row.amount, store.activeCurrency) }}
                </td>
                <td class="px-4 py-2 text-right">
                  <Badge v-if="row.status === 'Undone'" theme="gray">Undone</Badge>
                  <Button
                    v-else
                    size="sm"
                    variant="subtle"
                    :loading="busyKey === row.name"
                    @click="undoRow(row)"
                  >
                    <template #prefix>
                      <Undo2 class="h-3.5 w-3.5" />
                    </template>
                    Undo
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</template>
