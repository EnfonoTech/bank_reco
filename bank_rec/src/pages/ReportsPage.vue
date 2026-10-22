<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BankAccountFilters from "@/components/BankAccountFilters.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import { getBankReport } from "@/services/api";
import { useBankRecStore } from "@/stores/bankRec";
import type {
  BankReportName,
  BankReportResponse,
  ReportColumn,
} from "@/types/bankRec";
import { formatDate, formatMoney } from "@/utils/format";

const store = useBankRecStore();
const route = useRoute();
const router = useRouter();

const reports: Array<{ label: string; value: BankReportName }> = [
  { label: "Bank Reconciliation Statement", value: "Bank Reconciliation Statement" },
  { label: "Bank Clearance Summary", value: "Bank Clearance Summary" },
  { label: "Incorrectly Cleared Entries", value: "Cheques and Deposits Incorrectly cleared" },
];

const activeReport = ref<BankReportName>("Bank Reconciliation Statement");
const data = ref<BankReportResponse | null>(null);
const loading = ref(false);
const error = ref("");

interface Column {
  key: string;
  label: string;
  type: string;
}

// ERPNext reports give columns either as "Label:Type:width" strings or as objects
const columns = computed<Column[]>(() =>
  (data.value?.columns || []).map((column: ReportColumn, index) => {
    if (typeof column === "string") {
      const [label, type] = column.split(":");
      return { key: String(index), label, type: (type || "Data").split("/")[0] };
    }
    return {
      key: column.fieldname || String(index),
      label: column.label || column.fieldname || "",
      type: column.fieldtype || "Data",
    };
  })
);

function cell(row: Record<string, unknown> | unknown[] | null, column: Column) {
  if (!row) {
    return "";
  }
  const value = Array.isArray(row)
    ? row[Number(column.key)]
    : (row as Record<string, unknown>)[column.key];
  if (value === null || value === undefined || value === "") {
    return "";
  }
  if (column.type === "Currency" || column.type === "Float") {
    return formatMoney(Number(value), column.type === "Currency" ? store.activeCurrency : undefined);
  }
  if (column.type === "Date" && typeof value === "string") {
    return formatDate(value);
  }
  return String(value);
}

function isNumeric(column: Column) {
  return ["Currency", "Float", "Int"].includes(column.type);
}

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
    data.value = null;
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    data.value = await getBankReport({
      report_name: activeReport.value,
      bank_account: store.selectedBankAccount,
      from_date: store.fromDate,
      to_date: store.toDate,
    });
  } catch (err) {
    data.value = null;
    error.value = err instanceof Error ? err.message : "Unable to run the report.";
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

watch(activeReport, load);

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
      <div class="border-b border-bank-line px-4 py-3">
        <TabButtons v-model="activeReport" :buttons="reports" />
        <div class="mt-2 text-xs text-bank-muted">
          Statement and incorrectly-cleared reports run as of the To date; the
          clearance summary uses the whole date range.
        </div>
      </div>

      <LoadingState v-if="loading" label="Running report" />
      <ErrorState v-else-if="error" :message="error" can-retry @retry="load" />
      <EmptyState
        v-else-if="!store.selectedBankAccount"
        title="Select a bank account"
        detail="Pick a company and bank account to run the banking reports."
      />
      <EmptyState
        v-else-if="!data || !data.result.length"
        title="Nothing to show"
        detail="The report returned no rows for this bank account and date."
      />
      <div v-else class="bank-rec-scrollbar min-h-0 flex-1 overflow-auto">
        <div v-if="data.report_summary.length" class="flex flex-wrap gap-6 border-b border-bank-line px-4 py-3">
          <div v-for="item in data.report_summary" :key="item.label">
            <div class="text-xs uppercase tracking-wide text-bank-muted">{{ item.label }}</div>
            <div class="text-lg font-semibold tabular-nums text-bank-ink">
              {{ formatMoney(item.value, store.activeCurrency) }}
            </div>
          </div>
        </div>
        <table class="min-w-full divide-y divide-bank-line text-sm">
          <thead class="sticky top-0 bg-bank-muted-surface text-left text-xs font-medium uppercase tracking-wide text-bank-muted">
            <tr>
              <th
                v-for="column in columns"
                :key="column.key"
                class="px-4 py-3"
                :class="isNumeric(column) ? 'text-right' : ''"
              >
                {{ column.label }}
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-bank-line bg-bank-panel">
            <tr v-for="(row, index) in data.result" :key="index">
              <td
                v-for="column in columns"
                :key="column.key"
                class="px-4 py-2 text-bank-ink"
                :class="isNumeric(column) ? 'text-right tabular-nums' : ''"
              >
                {{ cell(row, column) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
