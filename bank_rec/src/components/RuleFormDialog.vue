<script setup lang="ts">
import { computed, ref, watch } from "vue";
import PartyAutocomplete from "@/components/PartyAutocomplete.vue";
import SearchSelect from "@/components/SearchSelect.vue";
import { getRule, getRuleOptions, saveRule, testRule } from "@/services/api";
import type {
  RuleCondition,
  RuleDetail,
  RuleOptions,
  RuleTestResult,
} from "@/types/bankRec";
import { formatDate, formatMoney } from "@/utils/format";
import X from "~icons/lucide/x";

const props = defineProps<{
  open: boolean;
  bankAccount: string;
  currency?: string;
  /** name of the rule to edit; empty = new rule */
  ruleName?: string;
  /** values to start a new rule from (e.g. the selected bank transaction) */
  prefill?: { title?: string; text?: string; direction?: "deposit" | "withdrawal" | "any" };
}>();

const emit = defineEmits<{
  close: [];
  saved: [rule: RuleDetail];
}>();

type Direction = "any" | "deposit" | "withdrawal";

const options = ref<RuleOptions | null>(null);
const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const error = ref("");
const testResult = ref<RuleTestResult | null>(null);

const title = ref("");
const enabled = ref(true);
const actionMode = ref<"Auto Execute" | "Suggest Only">("Suggest Only");
const direction = ref<Direction>("any");
const descriptionText = ref("");
const extraConditions = ref<RuleCondition[]>([]);
const matchAny = ref(false);
const entryType = ref<"Journal Entry" | "Payment Entry">("Journal Entry");
const account = ref("");
const partyType = ref("");
const party = ref("");
const costCenter = ref("");
const project = ref("");
const dimensions = ref<Record<string, string>>({});

const isEdit = computed(() => Boolean(props.ruleName));
const accountOptions = computed(() =>
  (options.value?.accounts || []).map((row) => ({
    value: row.name,
    label: row.name,
  }))
);
const costCenterOptions = computed(() =>
  (options.value?.cost_centers || []).map((row) => ({ value: row.name }))
);
const projectOptions = computed(() =>
  (options.value?.projects || []).map((row) => ({ value: row.name }))
);
const dimensionList = computed(() => options.value?.accounting_dimensions || []);

function dimensionChoices(fieldname: string) {
  return (options.value?.dimension_options?.[fieldname] || []).map((row) => ({
    value: String(row.name),
  }));
}

function resetForm() {
  title.value = props.prefill?.title || "";
  enabled.value = true;
  actionMode.value = "Suggest Only";
  direction.value = props.prefill?.direction || "any";
  descriptionText.value = props.prefill?.text || "";
  extraConditions.value = [];
  matchAny.value = false;
  entryType.value = "Journal Entry";
  account.value = "";
  partyType.value = "";
  party.value = "";
  costCenter.value = "";
  project.value = "";
  dimensions.value = {};
  testResult.value = null;
  error.value = "";
}

function loadRule(rule: RuleDetail) {
  title.value = rule.title;
  enabled.value = Boolean(rule.enabled);
  actionMode.value = rule.action_mode;
  matchAny.value = Boolean(rule.match_any_condition);
  entryType.value = (rule.entry_type || "Journal Entry") as "Journal Entry" | "Payment Entry";
  account.value = rule.account || "";
  partyType.value = rule.party_type || "";
  party.value = rule.party || "";
  costCenter.value = rule.cost_center || "";
  project.value = rule.project || "";
  dimensions.value = { ...rule.dimensions };

  // pull the two "simple" conditions out; everything else stays in the list
  let text = "";
  let dir: Direction = "any";
  const rest: RuleCondition[] = [];
  for (const condition of rule.conditions) {
    if (!text && condition.field_name === "Description" && condition.condition === "Contains") {
      text = condition.value;
    } else if (
      dir === "any" &&
      (condition.field_name === "Deposit" || condition.field_name === "Withdrawal") &&
      condition.condition === "Greater than" &&
      Number(condition.value) === 0
    ) {
      dir = condition.field_name === "Deposit" ? "deposit" : "withdrawal";
    } else {
      rest.push({ ...condition });
    }
  }
  descriptionText.value = text;
  direction.value = dir;
  extraConditions.value = rest;
}

async function initialise() {
  if (!props.open || !props.bankAccount) {
    return;
  }
  resetForm();
  loading.value = true;
  try {
    options.value = await getRuleOptions(props.bankAccount);
    if (props.ruleName) {
      loadRule(await getRule(props.ruleName));
    } else if (options.value.accounting_dimensions) {
      dimensions.value = Object.fromEntries(
        options.value.accounting_dimensions.map((dimension) => [
          dimension.fieldname,
          dimension.default_value || "",
        ])
      );
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to load the rule form.";
  } finally {
    loading.value = false;
  }
}

watch(() => [props.open, props.ruleName, props.bankAccount], initialise, {
  immediate: true,
});

function buildConditions(): RuleCondition[] {
  const rows: RuleCondition[] = [];
  if (direction.value === "deposit") {
    rows.push({ field_name: "Deposit", condition: "Greater than", value: "0" });
  } else if (direction.value === "withdrawal") {
    rows.push({ field_name: "Withdrawal", condition: "Greater than", value: "0" });
  }
  if (descriptionText.value.trim()) {
    rows.push({
      field_name: "Description",
      condition: "Contains",
      value: descriptionText.value.trim(),
    });
  }
  extraConditions.value.forEach((row) => {
    if (row.field_name && row.condition && row.value !== "") {
      rows.push({ ...row });
    }
  });
  return rows;
}

function buildPayload(): RuleDetail {
  return {
    title: title.value.trim(),
    enabled: enabled.value ? 1 : 0,
    bank_account: props.bankAccount,
    action_mode: actionMode.value,
    match_any_condition: matchAny.value ? 1 : 0,
    entry_type: entryType.value,
    account: entryType.value === "Journal Entry" ? account.value : "",
    party_type: partyType.value,
    party: party.value,
    cost_center: costCenter.value,
    project: project.value,
    dimensions: dimensions.value,
    conditions: buildConditions(),
  };
}

function addCondition() {
  extraConditions.value.push({
    field_name: options.value?.condition_fields[0] || "Description",
    condition: "Contains",
    value: "",
  });
}

async function runTest() {
  testing.value = true;
  testResult.value = null;
  error.value = "";
  try {
    testResult.value = await testRule(buildPayload());
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to test the rule.";
  } finally {
    testing.value = false;
  }
}

async function submit() {
  saving.value = true;
  error.value = "";
  try {
    const rule = await saveRule(buildPayload(), props.ruleName || undefined);
    emit("saved", rule);
    emit("close");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Unable to save the rule.";
  } finally {
    saving.value = false;
  }
}

const fieldLabel = "mb-1 block text-xs font-medium text-bank-muted";
const selectClass =
  "h-8 w-full rounded border border-bank-line bg-bank-panel px-2 text-sm text-bank-ink focus:border-bank-accent focus:outline-none";
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
    @mousedown.self="emit('close')"
  >
    <div class="w-full max-w-3xl rounded-lg border border-bank-line bg-bank-panel shadow-xl">
      <div class="flex items-center justify-between border-b border-bank-line px-5 py-3">
        <div class="text-base font-semibold text-bank-ink">
          {{ isEdit ? "Edit bank rule" : "New bank rule" }}
        </div>
        <button class="rounded p-1 text-bank-muted hover:bg-bank-muted-surface" @click="emit('close')">
          <X class="h-4 w-4" />
        </button>
      </div>

      <div v-if="loading" class="px-5 py-10 text-center text-sm text-bank-muted">Loading...</div>
      <div v-else class="flex flex-col gap-5 px-5 py-4">
        <div class="grid gap-3 md:grid-cols-[1fr_auto]">
          <div>
            <label :class="fieldLabel">Rule name</label>
            <input
              v-model="title"
              :class="selectClass"
              type="text"
              placeholder="e.g. Bank charges"
            />
          </div>
          <label class="flex items-end gap-2 pb-1 text-sm text-bank-ink">
            <input v-model="enabled" type="checkbox" />
            Enabled
          </label>
        </div>

        <div class="rounded-md border border-bank-line p-3">
          <div class="mb-2 text-sm font-semibold text-bank-ink">When a transaction matches</div>
          <div class="grid gap-3 md:grid-cols-[180px_1fr]">
            <div>
              <label :class="fieldLabel">Direction</label>
              <select v-model="direction" :class="selectClass">
                <option value="any">Any</option>
                <option value="deposit">Money in (deposit)</option>
                <option value="withdrawal">Money out (withdrawal)</option>
              </select>
            </div>
            <div>
              <label :class="fieldLabel">Description contains</label>
              <input
                v-model="descriptionText"
                :class="selectClass"
                type="text"
                placeholder="e.g. BANK CHARGE"
              />
            </div>
          </div>

          <div v-if="extraConditions.length" class="mt-3 flex flex-col gap-2">
            <div
              v-for="(row, index) in extraConditions"
              :key="index"
              class="grid items-center gap-2 md:grid-cols-[1fr_1fr_1fr_32px]"
            >
              <select v-model="row.field_name" :class="selectClass">
                <option v-for="name in options?.condition_fields" :key="name" :value="name">
                  {{ name }}
                </option>
              </select>
              <select v-model="row.condition" :class="selectClass">
                <option v-for="name in options?.condition_operators" :key="name" :value="name">
                  {{ name }}
                </option>
              </select>
              <input v-model="row.value" :class="selectClass" type="text" placeholder="Value" />
              <button
                class="rounded p-1 text-bank-muted hover:bg-bank-muted-surface"
                title="Remove condition"
                @click="extraConditions.splice(index, 1)"
              >
                <X class="h-4 w-4" />
              </button>
            </div>
          </div>

          <div class="mt-3 flex flex-wrap items-center gap-3">
            <Button size="sm" variant="subtle" @click="addCondition">Add condition</Button>
            <label v-if="extraConditions.length" class="flex items-center gap-2 text-sm text-bank-ink">
              <input v-model="matchAny" type="checkbox" />
              Match any condition (instead of all)
            </label>
          </div>
        </div>

        <div class="rounded-md border border-bank-line p-3">
          <div class="mb-2 text-sm font-semibold text-bank-ink">Then</div>
          <div class="grid gap-3 md:grid-cols-2">
            <div>
              <label :class="fieldLabel">Mode</label>
              <select v-model="actionMode" :class="selectClass">
                <option value="Suggest Only">Suggest only (a user applies it)</option>
                <option value="Auto Execute">Auto execute (posts the voucher)</option>
              </select>
            </div>
            <div>
              <label :class="fieldLabel">Create a</label>
              <select v-model="entryType" :class="selectClass">
                <option value="Journal Entry">Journal Entry (bank coding to an account)</option>
                <option value="Payment Entry">Payment Entry (against a party)</option>
              </select>
            </div>
            <div v-if="entryType === 'Journal Entry'" class="md:col-span-2">
              <label :class="fieldLabel">Account</label>
              <SearchSelect v-model="account" :options="accountOptions" placeholder="Search accounts" />
            </div>
            <div>
              <label :class="fieldLabel">
                Party type {{ entryType === "Payment Entry" ? "(required)" : "(optional)" }}
              </label>
              <select v-model="partyType" :class="selectClass" @change="party = ''">
                <option value="">None</option>
                <option value="Customer">Customer</option>
                <option value="Supplier">Supplier</option>
                <option value="Employee">Employee</option>
              </select>
            </div>
            <div v-if="partyType && options">
              <label :class="fieldLabel">Party</label>
              <PartyAutocomplete
                v-model="party"
                :company="options.company"
                :party-type="partyType"
              />
            </div>
            <div>
              <label :class="fieldLabel">Cost center</label>
              <SearchSelect v-model="costCenter" :options="costCenterOptions" placeholder="Optional" />
            </div>
            <div>
              <label :class="fieldLabel">Project</label>
              <SearchSelect v-model="project" :options="projectOptions" placeholder="Optional" />
            </div>
            <div v-for="dimension in dimensionList" :key="dimension.fieldname">
              <label :class="fieldLabel">{{ dimension.label || dimension.fieldname }}</label>
              <SearchSelect
                :model-value="dimensions[dimension.fieldname] || ''"
                :options="dimensionChoices(dimension.fieldname)"
                placeholder="Optional"
                @update:model-value="dimensions[dimension.fieldname] = $event"
              />
            </div>
          </div>
        </div>

        <div v-if="testResult" class="rounded-md border border-bank-line bg-bank-muted-surface p-3 text-sm">
          <div class="font-medium text-bank-ink">
            {{ testResult.matched }} of {{ testResult.checked }} unreconciled transactions match.
          </div>
          <ul v-if="testResult.samples.length" class="mt-2 text-bank-muted">
            <li v-for="sample in testResult.samples" :key="sample.name" class="flex justify-between gap-3">
              <span class="truncate">{{ formatDate(sample.date) }} - {{ sample.description }}</span>
              <span class="tabular-nums">{{ formatMoney(sample.amount, currency) }}</span>
            </li>
          </ul>
        </div>

        <div v-if="error" class="text-sm text-ink-red-4">{{ error }}</div>

        <div class="flex flex-wrap items-center justify-between gap-2 border-t border-bank-line pt-3">
          <Button variant="subtle" :loading="testing" @click="runTest">Test on unreconciled</Button>
          <div class="flex gap-2">
            <Button variant="subtle" @click="emit('close')">Cancel</Button>
            <Button variant="solid" :loading="saving" @click="submit">
              {{ isEdit ? "Save rule" : "Create rule" }}
            </Button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
