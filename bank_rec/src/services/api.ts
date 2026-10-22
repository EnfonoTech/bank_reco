import type {
  ImportMapping,
  ImportResult,
  ParsedStatement,
  RuleDetail,
  RuleOptions,
  RuleTestResult,
  StatementPreview,
  ActionBatch,
  BankAccount,
  BankReportName,
  BankReportResponse,
  CreateTransferResponse,
  RuleSuggestionResult,
  StatementBalance,
  TransferCounterpart,
  UndoResponse,
  BankRule,
  BootData,
  CashCodingRowsResponse,
  CashCodingSubmitResponse,
  CashCodingRow,
  CreateDefaultsResponse,
  CreateVoucherPayload,
  CreateVoucherResponse,
  DraftVoucherResponse,
  MatchCandidatesResponse,
  MatchVoucherSelection,
  MatchedTransactionsResponse,
  PartySearchResult,
  StatementSummary,
  SubmitMatchResponse,
  TransactionContext,
  BankTransaction,
  TransactionStatusFilter,
  UnreconcileResponse,
  UpdateTransactionResponse,
} from "@/types/bankRec";

type ApiValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Record<string, unknown>
  | object
  | unknown[];

interface FrappeResponse<T> {
  message?: T;
  exc?: string;
  exc_type?: string;
  _server_messages?: string;
}

const bankRecApiPath = "/api/method/bank_reco.api.bank_rec.";
const matchingApiPath = "/api/method/bank_reco.api.matching.";
const createVoucherApiPath =
  "/api/method/bank_reco.api.create_voucher.";
const cashCodingApiPath =
  "/api/method/bank_reco.api.cash_coding.";
const matchedApiPath = "/api/method/bank_reco.api.matched.";
const partyCompanyApiPath =
  "/api/method/bank_reco.api.party_company.";

const historyApiPath = "/api/method/bank_reco.api.history.";
const statementImportApiPath = "/api/method/bank_reco.api.statement_import.";
const rulesApiPath = "/api/method/bank_reco.api.rules.";
const statementBalanceApiPath = "/api/method/bank_reco.api.statement_balance.";
const reportsApiPath = "/api/method/bank_reco.api.reports.";
const transferApiPath = "/api/method/bank_reco.api.transfer.";
const bankRuleApiPath =
  "/api/method/bank_reco.bank_reco.doctype.abr_bank_rule.abr_bank_rule.";

function getCsrfToken() {
  return window.csrf_token || "";
}

function encodeParams(params: Record<string, ApiValue>) {
  const body = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) {
      return;
    }
    body.append(
      key,
      typeof value === "object" ? JSON.stringify(value) : String(value)
    );
  });

  return body;
}

function getServerMessage(payload: FrappeResponse<unknown>) {
  if (payload._server_messages) {
    try {
      const messages = JSON.parse(payload._server_messages) as string[];
      const firstMessage = messages
        .map((message) => JSON.parse(message))
        .find((message) => message?.message);
      if (firstMessage?.message) {
        return firstMessage.message as string;
      }
    } catch {
      return "The server returned an error.";
    }
  }

  if (payload.exc_type) {
    const exceptionName = payload.exc_type.split(".").pop() || "";
    const label = exceptionName
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/Exception$/, "Error")
      .trim();
    return label || "The server returned an error.";
  }

  return "The server returned an error.";
}

async function call<T>(
  apiPath: string,
  method: string,
  params: Record<string, ApiValue> = {}
): Promise<T> {
  const response = await fetch(`${apiPath}${method}`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "X-Frappe-CSRF-Token": getCsrfToken(),
    },
    body: encodeParams(params),
  });

  const payload = (await response.json().catch(() => ({}))) as FrappeResponse<T>;

  if (!response.ok || payload.exc) {
    throw new Error(getServerMessage(payload));
  }

  return payload.message as T;
}

export async function getDevBoot() {
  const response = await fetch(
    "/api/method/bank_reco.www.bank_rec.index.get_context_for_dev",
    {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Frappe-CSRF-Token": getCsrfToken(),
      },
    }
  );
  const payload = (await response.json().catch(() => ({}))) as FrappeResponse<
    Record<string, unknown>
  >;
  if (!response.ok || payload.exc) {
    throw new Error(getServerMessage(payload));
  }
  return payload.message || {};
}

export function getBoot() {
  return call<BootData>(bankRecApiPath, "get_boot");
}

export function getBankAccounts(company?: string) {
  return call<BankAccount[]>(bankRecApiPath, "get_bank_accounts", { company });
}

export function getStatementSummary(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
}) {
  return call<StatementSummary>(bankRecApiPath, "get_statement_summary", params);
}

export function getTransactions(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
  status?: TransactionStatusFilter;
}) {
  return call<BankTransaction[]>(bankRecApiPath, "get_transactions", params);
}

export function getTransactionContext(bank_transaction_name: string) {
  return call<TransactionContext>(bankRecApiPath, "get_transaction_context", {
    bank_transaction_name,
  });
}

export function getBankRules(bank_account?: string) {
  return call<BankRule[]>(bankRecApiPath, "get_bank_rules", { bank_account });
}

export async function searchParties(params: {
  party_type: string;
  company: string;
  txt?: string;
  page_len?: number;
  exact_party?: string;
}): Promise<PartySearchResult[]> {
  const rows = await call<string[][]>(partyCompanyApiPath, "search_parties", {
    doctype: params.party_type,
    txt: params.txt || "",
    searchfield: "name",
    start: 0,
    page_len: params.page_len || 20,
    exact_party: params.exact_party,
    filters: {
      party_type: params.party_type,
      company: params.company,
    },
  });
  return rows.map((row) => ({
    value: row[0],
    label: row[1] && row[1] !== row[0] ? `${row[1]} (${row[0]})` : row[0],
  }));
}

export function getMatchCandidates(params: {
  bank_transaction_name: string;
  from_date?: string;
  to_date?: string;
  document_types?: string[];
  exact_match?: boolean;
}) {
  return call<MatchCandidatesResponse>(matchingApiPath, "get_match_candidates", params);
}

export function submitMatch(params: {
  bank_transaction_name: string;
  vouchers: MatchVoucherSelection[];
}) {
  return call<SubmitMatchResponse>(matchingApiPath, "submit_match", params);
}

export function updateTransactionMetadata(params: {
  bank_transaction_name: string;
  reference_number?: string;
  party_type?: string;
  party?: string;
}) {
  return call<UpdateTransactionResponse>(
    matchingApiPath,
    "update_transaction_metadata",
    params
  );
}

export function getCreateDefaults(bank_transaction_name: string) {
  return call<CreateDefaultsResponse>(
    createVoucherApiPath,
    "get_create_defaults",
    { bank_transaction_name }
  );
}

export function createVoucherFromTransaction(params: {
  bank_transaction_name: string;
  payload: CreateVoucherPayload;
}) {
  return call<CreateVoucherResponse>(
    createVoucherApiPath,
    "create_voucher_from_transaction",
    params
  );
}

export function createVoucherDraftFromTransaction(params: {
  bank_transaction_name: string;
  payload: CreateVoucherPayload;
}) {
  return call<DraftVoucherResponse>(
    createVoucherApiPath,
    "create_voucher_draft_from_transaction",
    params
  );
}

export function getCashCodingRows(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
}) {
  return call<CashCodingRowsResponse>(
    cashCodingApiPath,
    "get_cash_coding_rows",
    params
  );
}

export function submitCashCoding(rows: Array<CashCodingRow & { bank_transaction_name: string }>) {
  return call<CashCodingSubmitResponse>(cashCodingApiPath, "submit_cash_coding", {
    rows,
  });
}

export function getMatchedTransactions(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
}) {
  return call<MatchedTransactionsResponse>(
    matchedApiPath,
    "get_matched_transactions",
    params
  );
}

export function unreconcileTransaction(params: {
  bank_transaction_name: string;
  cancel_linked_documents?: boolean;
}) {
  return call<UnreconcileResponse>(
    matchedApiPath,
    "unreconcile_transaction",
    params
  );
}

export function getActionHistory(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
  mine_only?: boolean;
}) {
  return call<{ batches: ActionBatch[] }>(historyApiPath, "get_action_history", {
    ...params,
    mine_only: params.mine_only === false ? 0 : 1,
  });
}

export function undoAction(log_name: string) {
  return call<UndoResponse>(historyApiPath, "undo_action", { log_name });
}

export function undoBatch(batch_id: string) {
  return call<UndoResponse>(historyApiPath, "undo_batch", { batch_id });
}

export function getStatementBalance(bank_account: string, date: string) {
  return call<StatementBalance | null>(
    statementBalanceApiPath,
    "get_statement_balance",
    { bank_account, date }
  );
}

export function setStatementBalance(params: {
  bank_account: string;
  date: string;
  balance: string;
}) {
  return call<StatementBalance | null>(
    statementBalanceApiPath,
    "set_statement_balance",
    params
  );
}

export function getBankReport(params: {
  report_name: BankReportName;
  bank_account: string;
  from_date?: string;
  to_date?: string;
}) {
  return call<BankReportResponse>(reportsApiPath, "get_bank_report", params);
}

export function findTransferCounterpart(bank_transaction_name: string) {
  return call<{ counterparts: TransferCounterpart[] }>(
    transferApiPath,
    "find_transfer_counterpart",
    { bank_transaction_name }
  );
}

export function createInternalTransfer(params: {
  bank_transaction_name: string;
  counterpart_name?: string;
  counter_account?: string;
  reference_no?: string;
  remarks?: string;
}) {
  return call<CreateTransferResponse>(
    transferApiPath,
    "create_internal_transfer",
    params
  );
}

export function runRuleSuggestions(params: {
  bank_account: string;
  from_date?: string;
  to_date?: string;
  force?: boolean;
}) {
  return call<RuleSuggestionResult>(bankRuleApiPath, "run_rule_suggestions", {
    ...params,
    force: params.force ? 1 : 0,
  });
}

export function runAutoRules(params: {
  bank_account: string;
  from_date: string;
  to_date: string;
}) {
  return call<{ matched: number; unmatched: number; skipped: number; errors: number }>(
    bankRuleApiPath,
    "run_bank_rules",
    params
  );
}

export function applySuggestedRule(bank_transaction_name: string) {
  return call<{ bank_transaction: string; rule: string; action_batch?: string }>(
    bankRuleApiPath,
    "apply_suggested_rule",
    { bank_transaction_name }
  );
}

export function dismissSuggestedRule(bank_transaction_name: string) {
  return call<{ bank_transaction: string }>(
    bankRuleApiPath,
    "dismiss_suggested_rule",
    { bank_transaction_name }
  );
}

export async function uploadStatementFile(file: File) {
  const body = new FormData();
  body.append("file", file, file.name);
  body.append("is_private", "1");
  body.append("folder", "Home/Attachments");

  const response = await fetch("/api/method/upload_file", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Frappe-CSRF-Token": getCsrfToken() },
    body,
  });
  const payload = (await response.json().catch(() => ({}))) as FrappeResponse<{
    file_url: string;
    file_name: string;
  }>;
  if (!response.ok || payload.exc || !payload.message) {
    throw new Error(getServerMessage(payload));
  }
  return payload.message;
}

export function parseStatement(file_url: string, bank_account: string) {
  return call<ParsedStatement>(statementImportApiPath, "parse_statement", {
    file_url,
    bank_account,
  });
}

export function previewStatement(params: {
  file_url: string;
  bank_account: string;
  mapping: ImportMapping;
}) {
  return call<StatementPreview>(statementImportApiPath, "preview_statement", params);
}

export function importStatement(params: {
  file_url: string;
  bank_account: string;
  mapping: ImportMapping;
  skip_duplicates: boolean;
  save_mapping: boolean;
}) {
  return call<ImportResult>(statementImportApiPath, "import_statement", {
    ...params,
    skip_duplicates: params.skip_duplicates ? 1 : 0,
    save_mapping: params.save_mapping ? 1 : 0,
  });
}

export function getRuleOptions(bank_account: string) {
  return call<RuleOptions>(rulesApiPath, "get_rule_options", { bank_account });
}

export function getRule(name: string) {
  return call<RuleDetail>(rulesApiPath, "get_rule", { name });
}

export function saveRule(payload: RuleDetail, name?: string) {
  return call<RuleDetail>(rulesApiPath, "save_rule", { payload, name });
}

export function setRuleEnabled(name: string, enabled: boolean) {
  return call<RuleDetail>(rulesApiPath, "set_rule_enabled", {
    name,
    enabled: enabled ? 1 : 0,
  });
}

export function deleteRule(name: string) {
  return call<{ name: string }>(rulesApiPath, "delete_rule", { name });
}

export function reorderRules(bank_account: string, names: string[]) {
  return call<{ count: number }>(rulesApiPath, "reorder_rules", {
    bank_account,
    names,
  });
}

export function testRule(payload: RuleDetail) {
  return call<RuleTestResult>(rulesApiPath, "test_rule", { payload });
}
