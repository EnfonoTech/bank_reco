<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  getAutoReconcileSettings,
  getJobStatus,
  startAutoReconcile,
  undoBatch,
} from "@/services/api";
import type { AutoReconcileSettings, JobStatus } from "@/types/bankRec";
import { formatDate, formatMoney } from "@/utils/format";
import X from "~icons/lucide/x";

const props = defineProps<{
  open: boolean;
  bankAccount: string;
  fromDate: string;
  toDate: string;
  currency?: string;
}>();

const emit = defineEmits<{
  close: [];
  done: [];
}>();

const settings = ref<AutoReconcileSettings | null>(null);
const job = ref<JobStatus | null>(null);
const busy = ref(false);
const error = ref("");
const undone = ref(false);
type Phase = "intro" | "running" | "preview" | "finished";
const phase = ref<Phase>("intro");
const lastDryRun = ref(true);

const percent = computed(() => {
  const j = job.value;
  return j && j.total ? Math.min(100, Math.round((j.processed / j.total) * 100)) : 0;
});

const criteria = computed(() => {
  const s = settings.value;
  if (!s) {
    return [];
  }
  const rows = ["Amount equals the bank amount (always)"];
  if (s.reference) rows.push("Reference number matches");
  if (s.party) rows.push("Party matches");
  if (s.date) rows.push("Voucher date within " + s.date_days + " day(s)");
  if (s.one_only) rows.push("Exactly one voucher qualifies");
  if (s.unpaid_invoices) rows.push("Unpaid invoices are included (a Payment Entry is created)");
  return rows;
});

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      return;
    }
    phase.value = "intro";
    job.value = null;
    error.value = "";
    undone.value = false;
    try {
      settings.value = await getAutoReconcileSettings();
    } catch (err) {
      error.value = err instanceof Error ? err.message : "Unable to read the settings.";
    }
  },
  { immediate: true }
);

async function run(dryRun: boolean) {
  busy.value = true;
  error.value = "";
  lastDryRun.value = dryRun;
  phase.value = "running";
  try {
    const { job_id } = await startAutoReconcile({
      bank_account: props.bankAccount,
      from_date: props.fromDate,
      to_date: props.toDate,
      dry_run: dryRun,
    });
    for (;;) {
      const status = await getJobStatus(job_id);
      job.value = status;
      if (status.status === "done") {
        phase.value = dryRun ? "preview" : "finished";
        if (!dryRun) {
          emit("done");
        }
        return;
      }
      if (status.status === "failed") {
        throw new Error(status.message || "Auto reconcile failed.");
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Auto reconcile failed.";
    phase.value = "intro";
  } finally {
    busy.value = false;
  }
}

async function undo() {
  if (!job.value?.batch_id) {
    return;
  }
  busy.value = true;
  try {
    await undoBatch(job.value.batch_id);
    undone.value = true;
    emit("done");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to undo.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
    @mousedown.self="emit('close')"
  >
    <div class="w-full max-w-3xl rounded-lg border border-bank-line bg-bank-panel shadow-xl">
      <div class="flex items-center justify-between border-b border-bank-line px-5 py-3">
        <div class="text-base font-semibold text-bank-ink">Auto reconcile</div>
        <button class="rounded p-1 text-bank-muted hover:bg-bank-muted-surface" @click="emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <div class="flex flex-col gap-4 px-5 py-4">
        <div v-if="error" class="text-sm text-ink-red-4">{{ error }}</div>

        <template v-if="phase === 'intro'">
          <p class="text-sm text-bank-ink">
            Matches unreconciled transactions of {{ formatDate(fromDate) }} to {{ formatDate(toDate) }}
            to existing vouchers, without a click. A voucher is used only when it meets all of:
          </p>
          <ul class="list-disc pl-5 text-sm text-bank-ink">
            <li v-for="row in criteria" :key="row">{{ row }}</li>
          </ul>
          <p class="text-sm text-bank-muted">
            Change these in
            <a class="text-bank-accent underline" href="/app/advance-bank-reconciliation-settings" target="_blank" rel="noopener">
              Advance Bank Reconciliation Settings
            </a>
            (Auto Reconcile section). Everything it does is logged and can be undone.
          </p>
          <div class="flex justify-end gap-2">
            <Button variant="subtle" @click="emit('close')">Cancel</Button>
            <Button variant="subtle" :loading="busy" @click="run(true)">Preview matches</Button>
            <Button variant="solid" :loading="busy" @click="run(false)">Reconcile now</Button>
          </div>
        </template>

        <template v-else-if="phase === 'running'">
          <div class="text-sm text-bank-ink">
            {{ lastDryRun ? "Looking for matches" : "Reconciling" }}...
            {{ job?.processed || 0 }} / {{ job?.total || 0 }}
          </div>
          <div class="h-2 w-full overflow-hidden rounded-full bg-bank-muted-surface">
            <div class="h-full rounded-full bg-bank-accent transition-all" :style="{ width: percent + '%' }" />
          </div>
          <div class="text-xs text-bank-muted">
            It keeps running on the server if you close this window.
          </div>
        </template>

        <template v-else-if="phase === 'preview' && job">
          <div class="text-sm text-bank-ink">
            <b>{{ job.matched || 0 }}</b> transaction(s) would be matched;
            {{ job.ambiguous || 0 }} have more than one qualifying voucher and
            {{ job.no_match || 0 }} have none.
          </div>
          <div v-if="job.proposals?.length" class="max-h-[320px] overflow-auto rounded-md border border-bank-line">
            <table class="min-w-full text-sm">
              <thead class="sticky top-0 bg-bank-muted-surface text-left text-xs uppercase tracking-wide text-bank-muted">
                <tr>
                  <th class="px-3 py-2">Date</th>
                  <th class="px-3 py-2">Description</th>
                  <th class="px-3 py-2 text-right">Amount</th>
                  <th class="px-3 py-2">Voucher</th>
                  <th class="px-3 py-2">Because</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-bank-line">
                <tr v-for="row in job.proposals" :key="row.bank_transaction">
                  <td class="whitespace-nowrap px-3 py-1.5 tabular-nums">{{ formatDate(row.date) }}</td>
                  <td class="max-w-[220px] truncate px-3 py-1.5">{{ row.description }}</td>
                  <td class="px-3 py-1.5 text-right tabular-nums">{{ formatMoney(row.amount, currency) }}</td>
                  <td class="px-3 py-1.5">{{ row.voucher_type }} {{ row.voucher_name }}</td>
                  <td class="px-3 py-1.5 text-bank-muted">{{ row.reasons.join(", ") }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="flex justify-end gap-2">
            <Button variant="subtle" @click="phase = 'intro'">Back</Button>
            <Button variant="solid" :disabled="!job.matched" :loading="busy" @click="run(false)">
              Reconcile {{ job.matched || 0 }} now
            </Button>
          </div>
        </template>

        <template v-else-if="phase === 'finished' && job">
          <div class="text-base font-semibold text-bank-ink">
            {{ undone ? "Undone" : (job.matched || 0) + " transaction(s) reconciled" }}
          </div>
          <div class="text-sm text-bank-muted">
            {{ job.ambiguous || 0 }} left to choose (more than one voucher), {{ job.no_match || 0 }} without a match,
            {{ job.failed || 0 }} failed.
          </div>
          <div class="flex justify-end gap-2">
            <Button v-if="job.batch_id && job.matched && !undone" variant="subtle" :loading="busy" @click="undo">
              Undo all
            </Button>
            <Button variant="solid" @click="emit('close')">Close</Button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
