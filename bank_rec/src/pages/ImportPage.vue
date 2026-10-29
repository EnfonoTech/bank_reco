<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import {
  getJobStatus,
  importStatement,
  parseStatement,
  previewStatement,
  uploadStatementFile,
} from "@/services/api";
import { useBankRecStore } from "@/stores/bankRec";
import type {
  ImportMapping,
  JobStatus,
  ParsedStatement,
  StatementPreview,
} from "@/types/bankRec";
import { formatDate, formatMoney } from "@/utils/format";
import FileUp from "~icons/lucide/file-up";

const store = useBankRecStore();
const route = useRoute();
const router = useRouter();

type Step = "upload" | "map" | "preview" | "running" | "done";
const step = ref<Step>("upload");
const busy = ref(false);
const error = ref("");
const dragging = ref(false);

const fileUrl = ref("");
const fileName = ref("");
const parsed = ref<ParsedStatement | null>(null);
const mapping = ref<ImportMapping | null>(null);
const preview = ref<StatementPreview | null>(null);
const result = ref<JobStatus | null>(null);
const progress = ref<JobStatus | null>(null);
const autoReconcile = ref(false);
const runRules = ref(false);
const skipDuplicates = ref(true);
const saveMapping = ref(true);
const showOptional = ref(false);

const headerOptions = computed(() => parsed.value?.headers || []);
const canPreview = computed(() => {
  const m = mapping.value;
  if (!m || !m.date_select || !m.description_select) {
    return false;
  }
  return m.same_amount_field
    ? Boolean(m.amount_select)
    : Boolean(m.deposit_select && m.withdrawal_select);
});

function reset() {
  step.value = "upload";
  fileUrl.value = "";
  fileName.value = "";
  parsed.value = null;
  mapping.value = null;
  preview.value = null;
  result.value = null;
  progress.value = null;
  error.value = "";
}

async function handleFile(file: File | undefined | null) {
  if (!file || !store.selectedBankAccount) {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    const uploaded = await uploadStatementFile(file);
    fileUrl.value = uploaded.file_url;
    fileName.value = file.name;
    parsed.value = await parseStatement(uploaded.file_url, store.selectedBankAccount);
    mapping.value = { ...parsed.value.mapping };
    showOptional.value = Boolean(
      mapping.value.particulars_select ||
        mapping.value.code_select ||
        mapping.value.other_party_select
    );
    step.value = "map";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to read the file.";
  } finally {
    busy.value = false;
  }
}

function onPick(event: Event) {
  handleFile((event.target as HTMLInputElement).files?.[0]);
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  handleFile(event.dataTransfer?.files?.[0]);
}

async function runPreview() {
  if (!mapping.value) {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    preview.value = await previewStatement({
      file_url: fileUrl.value,
      bank_account: store.selectedBankAccount,
      mapping: mapping.value,
    });
    step.value = "preview";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to preview the import.";
  } finally {
    busy.value = false;
  }
}

async function runImport() {
  if (!mapping.value) {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    const { job_id } = await importStatement({
      file_url: fileUrl.value,
      bank_account: store.selectedBankAccount,
      mapping: mapping.value,
      skip_duplicates: skipDuplicates.value,
      save_mapping: saveMapping.value,
      auto_reconcile: autoReconcile.value,
      run_rules: runRules.value,
    });
    step.value = "running";
    await followJob(job_id);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to import the statement.";
  } finally {
    busy.value = false;
  }
}

// the import runs on the server in the background: poll its progress
async function followJob(jobId: string) {
  for (;;) {
    const job = await getJobStatus(jobId);
    progress.value = job;
    if (job.status === "done") {
      result.value = job;
      step.value = "done";
      return;
    }
    if (job.status === "failed") {
      step.value = "preview";
      throw new Error(job.message || "The import failed.");
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

const progressPercent = computed(() => {
  const job = progress.value;
  if (!job || !job.total) {
    return 0;
  }
  return Math.min(100, Math.round((job.processed / job.total) * 100));
});

async function goReconcile() {
  if (preview.value?.from_date) {
    store.fromDate = preview.value.from_date;
  }
  if (preview.value?.to_date) {
    store.toDate = preview.value.to_date;
  }
  store.transactionStatus = "unreconciled";
  await router.push({ path: "/reconcile", query: store.queryState() });
}

async function changeCompany(company: string) {
  await store.changeCompany(company);
  reset();
}

function changeAccount(account: string) {
  store.selectedBankAccount = account;
  reset();
}

const companyOptions = computed(() =>
  store.allowedCompanies.map((company) => ({ label: company, value: company }))
);
const accountOptions = computed(() =>
  store.bankAccounts.map((account) => ({
    label: account.account_name || account.name,
    value: account.name,
  }))
);
const dateFormatOptions = computed(() =>
  (parsed.value?.date_formats || ["Auto"]).map((format) => ({
    label: format === "Auto" ? "Auto detect" : format,
    value: format,
  }))
);

const selectClass =
  "h-9 w-full rounded border border-bank-line bg-bank-panel px-2 text-sm text-bank-ink focus:border-bank-accent focus:outline-none";
const labelClass = "mb-1 block text-xs font-medium text-bank-muted";

onMounted(async () => {
  await store.initialize(route.query);
});
</script>

<template>
  <div class="flex min-h-0 w-full flex-col gap-4">
    <section class="rounded-lg border border-bank-line bg-bank-panel p-4 shadow-sm">
      <div class="mb-3 text-base font-semibold text-bank-ink">Import bank statement</div>
      <div class="grid gap-3 md:grid-cols-2">
        <div>
          <label :class="labelClass">Company</label>
          <select
            :class="selectClass"
            :value="store.selectedCompany"
            @change="changeCompany(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="option in companyOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </div>
        <div>
          <label :class="labelClass">Bank account</label>
          <select
            :class="selectClass"
            :value="store.selectedBankAccount"
            @change="changeAccount(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="option in accountOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </div>
      </div>
    </section>

    <EmptyState
      v-if="!store.loading.bankAccounts && !store.bankAccounts.length"
      title="No company bank accounts found"
      detail="Open the Bank Account in the desk, tick Is Company Account and set its ledger Account."
    />

    <template v-else>
      <ErrorState v-if="error" :message="error" />

      <!-- 1. upload -->
      <section
        v-if="step === 'upload'"
        class="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-14 text-center transition"
        :class="dragging ? 'border-bank-accent bg-bank-accent-soft' : 'border-bank-line bg-bank-panel'"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <FileUp class="h-8 w-8 text-bank-accent" />
        <div class="text-base font-semibold text-bank-ink">Drop the statement here</div>
        <div class="text-sm text-bank-muted">
          CSV, XLSX or XLS. Columns are detected for you and the layout is remembered for this bank.
        </div>
        <label class="cursor-pointer">
          <input class="hidden" type="file" accept=".csv,.xlsx,.xls" @change="onPick" />
          <span class="inline-flex h-9 items-center rounded bg-bank-accent px-4 text-sm font-medium text-white hover:bg-bank-accent-hover">
            {{ busy ? "Reading..." : "Choose file" }}
          </span>
        </label>
      </section>

      <!-- 2. map -->
      <section
        v-else-if="step === 'map' && parsed && mapping"
        class="rounded-lg border border-bank-line bg-bank-panel p-4 shadow-sm"
      >
        <div class="mb-1 flex items-center justify-between">
          <div class="text-base font-semibold text-bank-ink">Match the columns</div>
          <div class="text-sm text-bank-muted">{{ fileName }} - {{ parsed.row_count }} rows</div>
        </div>
        <div v-if="parsed.used_saved_mapping" class="mb-3 text-sm text-ink-green-3">
          Using the column layout saved for this bank.
        </div>

        <div class="grid gap-3 md:grid-cols-3">
          <div>
            <label :class="labelClass">Date *</label>
            <select v-model="mapping.date_select" :class="selectClass">
              <option value="">Select column</option>
              <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
            </select>
          </div>
          <div>
            <label :class="labelClass">Date format</label>
            <select v-model="mapping.date_format" :class="selectClass">
              <option v-for="o in dateFormatOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <div>
            <label :class="labelClass">Description *</label>
            <select v-model="mapping.description_select" :class="selectClass">
              <option value="">Select column</option>
              <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
            </select>
          </div>
          <div>
            <label :class="labelClass">Reference (optional)</label>
            <select v-model="mapping.reference_number_select" :class="selectClass">
              <option value="">None</option>
              <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
            </select>
          </div>
        </div>

        <div class="mt-4 rounded-md border border-bank-line p-3">
          <div class="mb-2 text-sm font-semibold text-bank-ink">Amounts</div>
          <div class="mb-3 flex flex-wrap gap-5 text-sm text-bank-ink">
            <label class="flex items-center gap-2">
              <input v-model="mapping.same_amount_field" type="radio" :value="1" />
              One amount column
            </label>
            <label class="flex items-center gap-2">
              <input v-model="mapping.same_amount_field" type="radio" :value="0" />
              Separate deposit and withdrawal columns
            </label>
          </div>
          <div v-if="mapping.same_amount_field" class="grid gap-3 md:grid-cols-3">
            <div>
              <label :class="labelClass">Amount *</label>
              <select v-model="mapping.amount_select" :class="selectClass">
                <option value="">Select column</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Positive amounts are</label>
              <select v-model="mapping.positive_field" :class="selectClass">
                <option value="Deposit">Deposits (money in)</option>
                <option value="Withdrawal">Withdrawals (money out)</option>
              </select>
            </div>
            <div class="text-xs text-bank-muted md:pt-6">
              Amounts written with CR / DR (e.g. 1,250.00 CR) are read as deposit / withdrawal automatically.
            </div>
          </div>
          <div v-else class="grid gap-3 md:grid-cols-3">
            <div>
              <label :class="labelClass">Deposit *</label>
              <select v-model="mapping.deposit_select" :class="selectClass">
                <option value="">Select column</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Withdrawal *</label>
              <select v-model="mapping.withdrawal_select" :class="selectClass">
                <option value="">Select column</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
          </div>
        </div>

        <div class="mt-3">
          <button class="text-sm text-bank-accent hover:underline" @click="showOptional = !showOptional">
            {{ showOptional ? "Hide" : "Show" }} extra columns (particulars, code, other party)
          </button>
          <div v-if="showOptional" class="mt-2 grid gap-3 md:grid-cols-3">
            <div>
              <label :class="labelClass">Particulars</label>
              <select v-model="mapping.particulars_select" :class="selectClass">
                <option value="">None</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Code</label>
              <select v-model="mapping.code_select" :class="selectClass">
                <option value="">None</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
            <div>
              <label :class="labelClass">Other party</label>
              <select v-model="mapping.other_party_select" :class="selectClass">
                <option value="">None</option>
                <option v-for="h in headerOptions" :key="h" :value="h">{{ h }}</option>
              </select>
            </div>
          </div>
        </div>

        <div class="mt-4 overflow-x-auto rounded-md border border-bank-line">
          <table class="min-w-full text-xs">
            <thead class="bg-bank-muted-surface text-left text-bank-muted">
              <tr>
                <th v-for="h in parsed.headers" :key="h" class="whitespace-nowrap px-3 py-2">{{ h }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-bank-line">
              <tr v-for="(row, index) in parsed.sample" :key="index">
                <td v-for="(cell, c) in row" :key="c" class="whitespace-nowrap px-3 py-1.5 text-bank-ink">
                  {{ cell }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-4 flex justify-between">
          <Button variant="subtle" @click="reset">Choose another file</Button>
          <Button variant="solid" :disabled="!canPreview" :loading="busy" @click="runPreview">
            Preview import
          </Button>
        </div>
      </section>

      <!-- 3. preview -->
      <section
        v-else-if="step === 'preview' && preview"
        class="rounded-lg border border-bank-line bg-bank-panel p-4 shadow-sm"
      >
        <div class="mb-3 text-base font-semibold text-bank-ink">Check before importing</div>
        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div class="rounded-md border border-bank-line p-3">
            <div class="text-xs uppercase tracking-wide text-bank-muted">New transactions</div>
            <div class="text-2xl font-semibold tabular-nums text-bank-ink">{{ preview.new_count }}</div>
            <div class="text-xs text-bank-muted">
              {{ formatDate(preview.from_date || undefined) }} to {{ formatDate(preview.to_date || undefined) }}
            </div>
          </div>
          <div class="rounded-md border border-bank-line p-3">
            <div class="text-xs uppercase tracking-wide text-bank-muted">Already in the system</div>
            <div class="text-2xl font-semibold tabular-nums text-bank-ink">{{ preview.duplicates }}</div>
            <div class="text-xs text-bank-muted">same date, amount, reference</div>
          </div>
          <div class="rounded-md border border-bank-line p-3">
            <div class="text-xs uppercase tracking-wide text-bank-muted">Deposits</div>
            <div class="text-2xl font-semibold tabular-nums text-bank-success">
              {{ formatMoney(preview.deposit_total, store.activeCurrency) }}
            </div>
          </div>
          <div class="rounded-md border border-bank-line p-3">
            <div class="text-xs uppercase tracking-wide text-bank-muted">Withdrawals</div>
            <div class="text-2xl font-semibold tabular-nums text-bank-ink">
              {{ formatMoney(preview.withdrawal_total, store.activeCurrency) }}
            </div>
          </div>
        </div>
        <div v-if="preview.is_credit_card" class="mt-3 rounded-md bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3">
          This is a credit card account: deposits and withdrawals are swapped on import (charges become withdrawals).
        </div>
        <div
          v-for="warning in preview.warnings"
          :key="warning"
          class="mt-3 rounded-md bg-surface-amber-1 px-3 py-2 text-sm text-ink-amber-3"
        >
          {{ warning }}
        </div>
        <div v-if="preview.skipped_zero" class="mt-3 text-sm text-bank-muted">
          {{ preview.skipped_zero }} row(s) with no amount (for example balance brought forward) are left out.
        </div>
        <div v-if="preview.error_count" class="mt-3 text-sm text-ink-red-4">
          {{ preview.error_count }} row(s) cannot be imported and will be left out:
          <ul class="mt-1 list-disc pl-5">
            <li v-for="item in preview.errors" :key="item.row + item.message">Row {{ item.row }}: {{ item.message }}</li>
            <li v-if="preview.error_count > preview.errors.length">and {{ preview.error_count - preview.errors.length }} more</li>
          </ul>
        </div>

        <div class="mt-4 max-h-[360px] overflow-auto rounded-md border border-bank-line">
          <table class="min-w-full text-sm">
            <thead class="sticky top-0 bg-bank-muted-surface text-left text-xs uppercase tracking-wide text-bank-muted">
              <tr>
                <th class="px-3 py-2">Date</th>
                <th class="px-3 py-2">Description</th>
                <th class="px-3 py-2">Reference</th>
                <th class="px-3 py-2 text-right">Deposit</th>
                <th class="px-3 py-2 text-right">Withdrawal</th>
                <th class="px-3 py-2">State</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-bank-line">
              <tr v-for="(row, index) in preview.rows" :key="index" :class="row.duplicate ? 'opacity-50' : ''">
                <td class="whitespace-nowrap px-3 py-1.5 tabular-nums">{{ formatDate(row.date || undefined) }}</td>
                <td class="max-w-[320px] truncate px-3 py-1.5">{{ row.description }}</td>
                <td class="px-3 py-1.5 text-bank-muted">{{ row.reference_number }}</td>
                <td class="px-3 py-1.5 text-right tabular-nums">{{ row.deposit ? formatMoney(row.deposit) : "" }}</td>
                <td class="px-3 py-1.5 text-right tabular-nums">{{ row.withdrawal ? formatMoney(row.withdrawal) : "" }}</td>
                <td class="px-3 py-1.5">
                  <Badge :theme="row.duplicate ? 'gray' : row.date ? 'green' : 'red'">
                    {{ row.duplicate ? "Duplicate" : row.date ? "New" : "No date" }}
                  </Badge>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="preview.truncated" class="mt-1 text-xs text-bank-muted">
          Showing the first {{ preview.rows.length }} of {{ preview.total }} rows; all are imported.
        </div>

        <div class="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap gap-5 text-sm text-bank-ink">
            <label class="flex items-center gap-2"><input v-model="skipDuplicates" type="checkbox" /> Skip duplicates</label>
            <label class="flex items-center gap-2"><input v-model="saveMapping" type="checkbox" /> Remember this column layout for the bank</label>
            <label class="flex items-center gap-2"><input v-model="runRules" type="checkbox" /> Run auto rules afterwards</label>
            <label class="flex items-center gap-2"><input v-model="autoReconcile" type="checkbox" /> Auto reconcile afterwards</label>
          </div>
          <div class="flex gap-2">
            <Button variant="subtle" @click="step = 'map'">Back</Button>
            <Button variant="solid" :disabled="!preview.new_count && skipDuplicates" :loading="busy" @click="runImport">
              Import {{ skipDuplicates ? preview.new_count : preview.total }} transactions
            </Button>
          </div>
        </div>
      </section>

      <!-- importing: server side, in the background -->
      <section
        v-else-if="step === 'running'"
        class="rounded-lg border border-bank-line bg-bank-panel p-8 text-center shadow-sm"
      >
        <div class="text-base font-semibold text-bank-ink">
          {{ progress?.stage || "Starting the import" }}...
        </div>
        <div class="mx-auto mt-4 h-2 w-full max-w-xl overflow-hidden rounded-full bg-bank-muted-surface">
          <div class="h-full rounded-full bg-bank-accent transition-all" :style="{ width: progressPercent + '%' }" />
        </div>
        <div class="mt-2 text-sm tabular-nums text-bank-muted">
          {{ progress?.processed || 0 }} / {{ progress?.total || 0 }} rows
          <template v-if="progress?.imported !== undefined"> - {{ progress.imported }} imported</template>
        </div>
        <div class="mt-3 text-xs text-bank-muted">
          It keeps running on the server if you close this page; imported rows are saved as it goes.
        </div>
      </section>

      <!-- 4. done -->
      <section
        v-else-if="step === 'done' && result"
        class="rounded-lg border border-bank-line bg-bank-panel p-6 text-center shadow-sm"
      >
        <div class="text-xl font-semibold text-bank-ink">
          {{ result.imported || 0 }} transaction{{ result.imported === 1 ? "" : "s" }} imported
        </div>
        <div class="mt-1 text-sm text-bank-muted">
          {{ result.skipped_duplicates || 0 }} duplicate(s) skipped, {{ result.error_count || 0 }} failed.
        </div>
        <div v-if="result.skipped_zero" class="mt-1 text-sm text-bank-muted">
          {{ result.skipped_zero }} row(s) with no amount were left out.
        </div>
        <div
          v-for="warning in result.warnings"
          :key="warning"
          class="mx-auto mt-3 max-w-xl rounded-md bg-surface-amber-1 px-3 py-2 text-left text-sm text-ink-amber-3"
        >
          {{ warning }}
        </div>
        <div v-if="result.rules" class="mt-2 text-sm text-bank-ink">
          Auto rules: {{ result.rules.matched }} reconciled, {{ result.rules.unmatched }} unmatched.
        </div>
        <div v-if="result.auto_reconcile" class="mt-1 text-sm text-bank-ink">
          Auto reconcile: {{ result.auto_reconcile.matched }} matched, {{ result.auto_reconcile.ambiguous }} left to choose, {{ result.auto_reconcile.no_match }} without a match.
        </div>
        <ul v-if="result.errors?.length" class="mx-auto mt-3 max-w-xl text-left text-sm text-ink-red-4">
          <li v-for="item in result.errors" :key="item.row">Row {{ item.row }}: {{ item.message }}</li>
        </ul>
        <div class="mt-5 flex justify-center gap-2">
          <Button variant="subtle" @click="reset">Import another file</Button>
          <Button variant="solid" @click="goReconcile">Reconcile these transactions</Button>
        </div>
      </section>
    </template>
  </div>
</template>
