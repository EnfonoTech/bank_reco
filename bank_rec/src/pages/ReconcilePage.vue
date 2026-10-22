<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import BankAccountFilters from "@/components/BankAccountFilters.vue";
import CreateVoucherPanel from "@/components/CreateVoucherPanel.vue";
import EmptyState from "@/components/EmptyState.vue";
import ErrorState from "@/components/ErrorState.vue";
import LoadingState from "@/components/LoadingState.vue";
import MatchPanel from "@/components/MatchPanel.vue";
import ReconcileProgressDialog from "@/components/ReconcileProgressDialog.vue";
import AutoReconcileDialog from "@/components/AutoReconcileDialog.vue";
import RuleFormDialog from "@/components/RuleFormDialog.vue";
import StatementSummary from "@/components/StatementSummary.vue";
import TransferPanel from "@/components/TransferPanel.vue";
import TransactionDetailPanel from "@/components/TransactionDetailPanel.vue";
import TransactionList from "@/components/TransactionList.vue";
import UpdateTransactionPanel from "@/components/UpdateTransactionPanel.vue";
import { useBankRecStore } from "@/stores/bankRec";
import type { MatchVoucherSelection } from "@/types/bankRec";
import Search from "~icons/lucide/search";
import Undo2 from "~icons/lucide/undo-2";
import Zap from "~icons/lucide/zap";
import type { TransactionStatusFilter } from "@/types/bankRec";

const store = useBankRecStore();
const route = useRoute();
const router = useRouter();
const activePanel = ref<"match" | "create" | "transfer" | "update" | "details">("match");
const search = ref("");
const suggestedOnly = ref(false);
const ruleDialogOpen = ref(false);
const autoDialogOpen = ref(false);
async function autoReconciled() {
  await refresh();
}
const rulePrefill = computed(() => {
  const row = store.selectedTransaction;
  if (!row) {
    return undefined;
  }
  return {
    title: row.bank_party_name || "",
    text: row.description || row.bank_party_name || "",
    direction: row.direction === "withdrawal" ? ("withdrawal" as const) : row.direction === "deposit" ? ("deposit" as const) : ("any" as const),
  };
});
async function ruleSaved() {
  await store.evaluateRuleSuggestions(true);
}
const draftUrl = ref("");
const panelButtons = [
  { label: "Match", value: "match" },
  { label: "Create", value: "create" },
  { label: "Transfer", value: "transfer" },
  { label: "Update", value: "update" },
  { label: "Details", value: "details" },
];

// every typed word must appear somewhere in the row (description, reference, party, amount)
const visibleTransactions = computed(() => {
  const terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return store.transactions.filter((transaction) => {
    if (suggestedOnly.value && !transaction.suggested_rule) {
      return false;
    }
    if (!terms.length) {
      return true;
    }
    const haystack = [
      transaction.description,
      transaction.reference_number,
      transaction.bank_party_name,
      transaction.party_display,
      transaction.custom_particulars,
      transaction.custom_code,
      transaction.suggested_rule_title,
      transaction.date,
      String(Math.abs(transaction.amount)),
      Math.abs(transaction.amount).toFixed(2),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
});

const hasBootError = computed(() => Boolean(store.errors.boot));
const hasBankAccountError = computed(() => Boolean(store.errors.bankAccounts));
const hasTransactionsError = computed(() => Boolean(store.errors.transactions));
const pageLoading = computed(
  () => store.loading.boot || store.loading.bankAccounts
);
const progressOpen = computed(
  () =>
    store.loading.submitMatch ||
    store.loading.createVoucher ||
    store.loading.createDraft
);
const progressTitle = computed(() => {
  if (store.loading.submitMatch) {
    return "Matching transaction";
  }
  if (store.loading.createDraft) {
    return "Preparing draft";
  }
  return "Creating voucher";
});
const progressMessage = computed(() => {
  if (store.loading.submitMatch) {
    return "Reconciling the selected transaction.";
  }
  if (store.loading.createDraft) {
    return "Saving a draft voucher for full-page editing.";
  }
  return "Creating and reconciling the selected transaction.";
});

async function replaceQuery() {
  await router.replace({
    path: route.path,
    query: store.queryState(),
  });
}

async function refresh() {
  await store.refreshReconcile();
  await Promise.all([store.loadMatchCandidates(), store.loadCreateDefaults()]);
  await replaceQuery();
}

async function selectTransaction(name: string) {
  store.selectedTransactionName = name;
  await replaceQuery();
  await store.loadSelectedContext();
  await Promise.all([store.loadMatchCandidates(), store.loadCreateDefaults()]);
}

async function updateFilter(
  field:
    | "selectedCompany"
    | "selectedBankAccount"
    | "fromDate"
    | "toDate"
    | "transactionStatus",
  value: string
) {
  if (field === "selectedCompany") {
    await store.changeCompany(value);
  } else if (field === "transactionStatus") {
    store.transactionStatus = value as TransactionStatusFilter;
  } else {
    store[field] = value;
  }

  store.selectedTransactionName = "";
  store.selectedContext = null;
  await replaceQuery();
  await refresh();
}

function updateStatementBalance(value: string) {
  // persisted per bank account + to-date (debounced in the store)
  store.queueStatementBalanceSave(value);
}

async function evaluateRules(force: boolean) {
  await store.evaluateRuleSuggestions(force);
}

async function applySuggestion() {
  const response = await store.applySelectedSuggestion();
  if (response) {
    await replaceQuery();
  }
}

async function createTransfer(payload: Parameters<typeof store.createTransfer>[0]) {
  const response = await store.createTransfer(payload);
  if (response) {
    activePanel.value = "match";
    await replaceQuery();
  }
}

watch(activePanel, (panel) => {
  if (panel === "transfer") {
    store.loadTransferCounterparts();
  }
});

watch(
  () => store.selectedTransactionName,
  () => {
    if (activePanel.value === "transfer") {
      store.loadTransferCounterparts();
    }
  }
);

async function initialize() {
  await store.initialize(route.query);
  await refresh();
}

async function submitMatch(vouchers: MatchVoucherSelection[]) {
  const response = await store.submitSelectedMatch(vouchers);
  if (response) {
    await replaceQuery();
  }
}

async function updateMetadata(payload: {
  reference_number?: string;
  party_type?: string;
  party?: string;
}) {
  const response = await store.saveTransactionMetadata(payload);
  if (response) {
    await replaceQuery();
  }
}

async function createVoucher(payload: Parameters<typeof store.createVoucher>[0]) {
  draftUrl.value = "";
  const response = await store.createVoucher(payload);
  if (response) {
    await replaceQuery();
  }
}

async function createDraft(payload: Parameters<typeof store.createDraftVoucher>[0]) {
  draftUrl.value = "";
  const response = await store.createDraftVoucher(payload);
  if (!response?.desk_url) {
    return;
  }

  const opened = window.open(response.desk_url, "_blank", "noopener");
  if (!opened) {
    draftUrl.value = response.desk_url;
  }
}

onMounted(initialize);

watch(
  () => route.query.bank_transaction,
  async (value) => {
    const selected = Array.isArray(value) ? value[0] || "" : value || "";
    if (selected === store.selectedTransactionName) {
      return;
    }

    store.selectedTransactionName = selected;
    if (!selected) {
      store.selectedContext = null;
      store.matchCandidates = [];
      store.createDefaults = null;
      return;
    }

    await store.loadSelectedContext();
    await Promise.all([store.loadMatchCandidates(), store.loadCreateDefaults()]);
  }
);
</script>

<template>
  <div class="flex min-h-0 w-full flex-1 flex-col gap-4 lg:h-[calc(100vh-103px)] lg:overflow-hidden">
    <ErrorState
      v-if="hasBootError"
      title="Bank Rec is unavailable"
      :message="store.errors.boot"
      can-retry
      @retry="initialize"
    />

    <template v-else>
      <BankAccountFilters
        :companies="store.allowedCompanies"
        :selected-company="store.selectedCompany"
        :bank-accounts="store.bankAccounts"
        :selected-bank-account="store.selectedBankAccount"
        :from-date="store.fromDate"
        :to-date="store.toDate"
        :statement-balance="store.statementBalance"
        :status="store.transactionStatus"
        show-statement-balance
        show-status
        :loading="pageLoading || store.loading.transactions || store.loading.summary"
        @update:selected-company="updateFilter('selectedCompany', $event)"
        @update:selected-bank-account="updateFilter('selectedBankAccount', $event)"
        @update:from-date="updateFilter('fromDate', $event)"
        @update:to-date="updateFilter('toDate', $event)"
        @update:statement-balance="updateStatementBalance"
        @update:status="updateFilter('transactionStatus', $event)"
        @refresh="refresh"
      />

      <ErrorState
        v-if="hasBankAccountError"
        :message="store.errors.bankAccounts"
        can-retry
        @retry="initialize"
      />
      <EmptyState
        v-else-if="!pageLoading && !store.bankAccounts.length"
        title="No company bank accounts found"
        detail="Bank Reco lists Bank Accounts that have Is Company Account ticked and a ledger account set (Bank Account > Account). Tick it on your bank account, or ask an administrator to grant access to the company. Administrator also needs the account to be a company account."
      />

      <template v-else>
        <StatementSummary
          :summary="store.summary"
          :statement-balance="store.statementBalance"
          :currency="store.activeCurrency"
          :loading="store.loading.summary"
        />

        <div class="grid min-h-[620px] flex-1 gap-4 xl:min-h-0 xl:grid-cols-[minmax(380px,0.8fr)_minmax(620px,1.2fr)]">
          <section class="flex min-h-0 flex-col overflow-hidden rounded-lg border border-bank-line bg-bank-panel shadow-sm">
            <div class="flex items-center justify-between gap-3 border-b border-bank-line px-4 py-3">
              <div>
                <div class="text-base font-semibold text-bank-ink">
                  Bank transactions
                </div>
                <div class="text-sm tabular-nums text-bank-muted">
                  {{ visibleTransactions.length }} of {{ store.transactions.length }} rows
                </div>
              </div>
              <div class="flex items-center gap-2">
                <label
                  v-if="store.summary?.suggested_count"
                  class="flex items-center gap-1 text-xs text-bank-ink"
                >
                  <input v-model="suggestedOnly" type="checkbox" />
                  Suggested ({{ store.summary.suggested_count }})
                </label>
                <Button
                  size="sm"
                  variant="subtle"
                  title="Match transactions to existing vouchers automatically"
                  @click="autoDialogOpen = true"
                >
                  Auto reconcile
                </Button>
                <Button
                  size="sm"
                  variant="subtle"
                  :disabled="!store.selectedTransaction"
                  title="Create a bank rule from the selected transaction"
                  @click="ruleDialogOpen = true"
                >
                  New rule
                </Button>
                <Button
                  size="sm"
                  variant="subtle"
                  :loading="store.loading.suggestion"
                  title="Flag transactions that match an enabled bank rule"
                  @click="evaluateRules(false)"
                >
                  <template #prefix>
                    <Zap class="h-3.5 w-3.5" />
                  </template>
                  Run rules
                </Button>
              </div>
            </div>
            <div class="border-b border-bank-line px-4 py-2">
              <FormControl
                v-model="search"
                type="search"
                variant="outline"
                size="md"
                placeholder="Search description, reference, party or amount"
              >
                <template #prefix>
                  <Search class="h-4 w-4 text-bank-muted" />
                </template>
              </FormControl>
            </div>

            <LoadingState
              v-if="store.loading.transactions"
              label="Loading transactions"
            />
            <ErrorState
              v-else-if="hasTransactionsError"
              :message="store.errors.transactions"
              can-retry
              @retry="store.loadTransactions"
            />
            <EmptyState
              v-else-if="!visibleTransactions.length"
              title="No transactions found"
              detail="Change the date range, bank account, or status filter, or import a statement with the Import tab."
            />
            <div v-else class="bank-rec-scrollbar min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                :transactions="visibleTransactions"
                :selected-name="store.selectedTransactionName"
                :currency="store.activeCurrency"
                @select="selectTransaction"
              />
            </div>
          </section>

          <section class="flex min-h-0 flex-col overflow-hidden rounded-lg border border-bank-line bg-bank-panel shadow-sm">
            <div class="border-b border-bank-line px-4 py-3">
              <div class="truncate text-base font-semibold text-bank-ink">
                {{ store.selectedTransaction?.description || store.selectedTransaction?.name || "No transaction selected" }}
              </div>
              <div class="mt-1 overflow-x-auto">
                <TabButtons
                  v-model="activePanel"
                  :buttons="panelButtons"
                />
              </div>
            </div>

            <div
              v-if="store.lastAction"
              class="flex items-center justify-between gap-3 border-b border-bank-line bg-surface-green-2 px-4 py-2 text-sm text-ink-green-3"
            >
              <span>{{ store.lastAction.message }}</span>
              <Button size="sm" variant="subtle" :loading="store.undoing" @click="store.undoLastAction()">
                <template #prefix>
                  <Undo2 class="h-3.5 w-3.5" />
                </template>
                Undo
              </Button>
            </div>
            <div
              v-if="store.selectedTransaction?.suggested_rule"
              class="flex items-center justify-between gap-3 border-b border-bank-line bg-bank-accent-soft px-4 py-2 text-sm"
            >
              <span class="flex min-w-0 items-center gap-2 text-bank-ink">
                <Zap class="h-4 w-4 shrink-0 text-bank-accent" />
                <span class="truncate">
                  Rule suggested:
                  <strong>{{ store.selectedTransaction.suggested_rule_title || store.selectedTransaction.suggested_rule }}</strong>
                </span>
              </span>
              <span class="flex shrink-0 gap-2">
                <Button size="sm" variant="solid" :loading="store.loading.suggestion" @click="applySuggestion">
                  Apply
                </Button>
                <Button size="sm" variant="subtle" :disabled="store.loading.suggestion" @click="store.dismissSelectedSuggestion()">
                  Dismiss
                </Button>
              </span>
            </div>
            <div v-if="store.errors.suggestion" class="border-b border-bank-line px-4 py-2 text-sm text-ink-red-4">
              {{ store.errors.suggestion }}
            </div>

            <MatchPanel
              v-if="activePanel === 'match'"
              :transaction="store.selectedTransaction"
              :candidates="store.matchCandidates"
              :loading="store.loading.matchCandidates"
              :submitting="store.loading.submitMatch"
              :error="store.errors.matchCandidates"
              :submit-error="store.errors.submitMatch"
              :currency="store.activeCurrency"
              @refresh="store.loadMatchCandidates"
              @submit="submitMatch"
            />
            <CreateVoucherPanel
              v-else-if="activePanel === 'create'"
              :transaction="store.selectedTransaction"
              :defaults="store.createDefaults"
              :loading="store.loading.createDefaults"
              :creating="store.loading.createVoucher"
              :drafting="store.loading.createDraft"
              :error="store.errors.createDefaults"
              :create-error="store.errors.createVoucher"
              :draft-error="store.errors.createDraft"
              :draft-url="draftUrl"
              @refresh="store.loadCreateDefaults"
              @create="createVoucher"
              @draft="createDraft"
            />
            <TransferPanel
              v-else-if="activePanel === 'transfer'"
              :transaction="store.selectedTransaction"
              :counterparts="store.transferCounterparts"
              :loading="store.loading.transfer"
              :creating="store.loading.transfer"
              :error="store.errors.transfer"
              :currency="store.activeCurrency"
              @refresh="store.loadTransferCounterparts"
              @create="createTransfer"
            />
            <UpdateTransactionPanel
              v-else-if="activePanel === 'update'"
              :transaction="store.selectedTransaction"
              :submitting="store.loading.updateMetadata"
              :error="store.errors.updateMetadata"
              @submit="updateMetadata"
            />
            <TransactionDetailPanel
              v-else
              embedded
              :transaction="store.selectedTransaction"
              :context="store.selectedContext"
              :bank-account="store.selectedBankAccountDoc"
              :currency="store.activeCurrency"
              :loading="store.loading.context"
              :error="store.errors.context"
              @retry="store.loadSelectedContext"
            />
          </section>
        </div>
      </template>
    </template>

    <AutoReconcileDialog
      :open="autoDialogOpen"
      :bank-account="store.selectedBankAccount"
      :from-date="store.fromDate"
      :to-date="store.toDate"
      :currency="store.activeCurrency"
      @close="autoDialogOpen = false"
      @done="autoReconciled"
    />

    <RuleFormDialog
      :open="ruleDialogOpen"
      :bank-account="store.selectedBankAccount"
      :currency="store.activeCurrency"
      :prefill="rulePrefill"
      @close="ruleDialogOpen = false"
      @saved="ruleSaved"
    />

    <ReconcileProgressDialog
      :open="progressOpen"
      :title="progressTitle"
      :message="progressMessage"
    />
  </div>
</template>
