export interface AccountingDimension {
  fieldname: string;
  fieldtype?: string;
  label?: string;
  options?: string;
  has_company_field?: boolean;
  default_value?: string;
  mandatory_for_bs?: boolean;
  mandatory_for_pl?: boolean;
}

export interface DimensionOption {
  name: string;
  [key: string]: unknown;
}

export interface BootData {
  default_route: string;
  site_name: string;
  csrf_token: string;
  session_user: string;
  desk_theme: "Light" | "Dark" | "Automatic";
  lang: string;
  dir: "ltr" | "rtl";
  allowed_roles: string[];
  allowed_companies: string[];
  settings: Record<string, unknown>;
  accounting_dimensions: AccountingDimension[];
}

export interface BankAccount {
  name: string;
  account_name: string;
  bank?: string;
  account: string;
  company: string;
  currency?: string;
  account_root_type?: string;
  account_report_type?: string;
}

export type TransactionDirection = "deposit" | "withdrawal" | "unknown";

export interface BankTransaction {
  name: string;
  date?: string;
  description?: string;
  reference_number?: string;
  bank_party_name?: string;
  custom_particulars?: string;
  custom_code?: string;
  deposit: number;
  withdrawal: number;
  amount: number;
  direction: TransactionDirection;
  currency?: string;
  bank_account: string;
  company?: string;
  unallocated_amount: number;
  suggested_rule?: string | null;
  suggested_rule_title?: string | null;
  party_type?: string;
  party?: string;
  party_display?: string;
  payment_document?: string;
  payment_entry?: string;
  linked_payments?: LinkedPayment[];
  allocated_amount: number;
  status?: string;
}

export interface LinkedPayment {
  payment_document?: string;
  payment_entry?: string;
  allocated_amount: number;
}

export interface TransactionContext {
  transaction: BankTransaction;
  bank_account: BankAccount;
  linked_payments: LinkedPayment[];
}

export interface StatementSummary {
  bank_account: string;
  from_date?: string;
  to_date?: string;
  unreconciled_count: number;
  cleared_balance: number;
  unreconciled_total: number;
  statement_balance?: number | null;
  statement_balance_date?: string | null;
  suggested_count?: number;
}

export interface BankRule {
  name: string;
  title?: string;
  enabled?: boolean | number;
  action_mode?: "Auto Execute" | "Suggest Only";
  priority?: number;
  company?: string;
  bank_account?: string;
  entry_type?: string;
  account?: string;
  party_type?: string;
  party?: string;
  modified?: string;
  desk_url: string;
}

export interface PartySearchResult {
  value: string;
  label: string;
}

export type TransactionStatusFilter = "unreconciled" | "reconciled" | "all";

export type MatchConfidence = "high" | "medium" | "low";

export interface MatchCandidate {
  is_internal_transfer?: boolean;
  rank: number;
  voucher_type: string;
  source_type: string;
  voucher_name: string;
  amount: number;
  reference_number?: string;
  reference_date?: string;
  party?: string;
  party_type?: string;
  posting_date?: string;
  currency?: string;
  party_name?: string;
  key: string;
  reasons: string[];
  confidence: MatchConfidence;
}

export interface MatchCandidatesResponse {
  transaction: BankTransaction;
  candidates: MatchCandidate[];
  filters: {
    document_types: string[];
    from_date?: string;
    to_date?: string;
  };
}

export interface MatchVoucherSelection {
  voucher_type: string;
  source_type: string;
  voucher_name: string;
  amount: number;
}

export interface SubmitMatchResponse {
  status: string;
  idempotent: boolean;
  action_batch?: string;
  transaction: BankTransaction;
  linked_payments: LinkedPayment[];
}

export interface UpdateTransactionResponse {
  transaction: BankTransaction;
}

export interface CreateOption {
  name: string;
  account_name?: string;
  account_type?: string;
  root_type?: string;
  cost_center_name?: string;
  project_name?: string;
}

export interface CreateDefaultsResponse {
  transaction: BankTransaction;
  bank_account: BankAccount;
  settings: Record<string, unknown>;
  defaults: {
    posting_date?: string;
    reference_date?: string;
    reference_number?: string;
    party_type?: string;
    party?: string;
  };
  options: {
    accounts: CreateOption[];
    mode_of_payments: CreateOption[];
    cost_centers: CreateOption[];
    projects: CreateOption[];
    accounting_dimensions: AccountingDimension[];
    dimension_options: Record<string, DimensionOption[]>;
  };
}

export interface CreateVoucherPayload {
  account?: string;
  party_type?: string;
  party?: string;
  mode_of_payment?: string;
  posting_date?: string;
  reference_date?: string;
  reference_number?: string;
  cost_center?: string;
  project?: string;
  dimensions?: Record<string, unknown>;
  save_as_rule?: boolean;
  rule_title?: string;
}

export interface CreateVoucherResponse {
  status: string;
  action_batch?: string;
  transaction: BankTransaction;
  voucher_type?: string;
  voucher_name?: string;
  desk_url?: string;
  linked_payments: LinkedPayment[];
  rule?: { name: string; title: string } | null;
}

export interface DraftVoucherResponse {
  voucher_type: string;
  voucher_name: string;
  desk_url: string;
  transaction: BankTransaction;
}

export interface CashCodingRow {
  transaction: BankTransaction;
  account: string;
  party_type: string;
  party: string;
  cost_center: string;
  project: string;
  dimensions: Record<string, string>;
  reference_number: string;
  notes: string;
  suggested_rule?: { name: string; title: string } | null;
}

export interface CashCodingRowsResponse {
  rows: CashCodingRow[];
  options: {
    accounts: CreateOption[];
    cost_centers: CreateOption[];
    projects: CreateOption[];
    accounting_dimensions: AccountingDimension[];
    dimension_options: Record<string, DimensionOption[]>;
  };
}

export interface CashCodingSubmitRowResult {
  bank_transaction: string;
  status: "success" | "error";
  message: string;
  voucher?: {
    voucher_type: string;
    voucher_name: string;
  } | null;
}

export interface CashCodingSubmitResponse {
  results: CashCodingSubmitRowResult[];
  summary: {
    total: number;
    success: number;
    error: number;
  };
}

export interface MatchedTransactionsResponse {
  rows: BankTransaction[];
}

export interface UnreconcileResponse {
  transaction: BankTransaction;
  linked_payments: LinkedPayment[];
  cancel_linked_documents: boolean;
}

export interface StatementBalance {
  name: string;
  balance_date: string;
  closing_balance: number;
}

export type ActionType = "Matched" | "Created Voucher" | "Internal Transfer";

export interface ActionLogRow {
  name: string;
  bank_transaction: string;
  action_type: ActionType;
  status: "Active" | "Undone";
  batch_id: string;
  voucher_type?: string;
  voucher_name?: string;
  amount: number;
  details?: string;
  owner: string;
  creation: string;
  undone_on?: string;
  transaction_date?: string;
  transaction_description?: string;
  transaction_amount: number;
}

export interface ActionBatch {
  batch_id: string;
  creation: string;
  owner: string;
  active_count: number;
  rows: ActionLogRow[];
}

export interface UndoResult {
  bank_transaction: string;
  status: "undone" | "error";
  message?: string;
  cancelled?: Array<{ voucher_type: string; voucher_name: string }>;
}

export interface UndoResponse {
  results: UndoResult[];
  summary: { total: number; undone: number; error: number };
}

export interface RuleSuggestionResult {
  evaluated: number;
  suggested: number;
}

export interface TransferCounterpart extends BankTransaction {
  account: string;
  days_apart: number;
}

export interface CreateTransferResponse {
  payment_entry: string;
  desk_url: string;
  batch_id: string;
  transaction: BankTransaction;
  counterpart: BankTransaction | null;
}

export type BankReportName =
  | "Bank Reconciliation Statement"
  | "Bank Clearance Summary"
  | "Cheques and Deposits Incorrectly cleared";

export type ReportColumn =
  | string
  | { fieldname?: string; label?: string; fieldtype?: string; options?: string };

export interface BankReportResponse {
  report_name: BankReportName;
  filters: Record<string, string>;
  columns: ReportColumn[];
  result: Array<Record<string, unknown> | unknown[] | null>;
  report_summary: Array<{ label: string; value: number; datatype?: string }>;
}

export interface ImportMapping {
  date_select: string;
  description_select: string;
  reference_number_select: string;
  same_amount_field: number;
  amount_select: string;
  positive_field: "Deposit" | "Withdrawal";
  deposit_select: string;
  withdrawal_select: string;
  particulars_select?: string;
  code_select?: string;
  other_party_select?: string;
  date_format: string;
}

export interface ParsedStatement {
  headers: string[];
  sample: string[][];
  row_count: number;
  mapping: ImportMapping;
  used_saved_mapping: boolean;
  date_formats: string[];
}

export interface PreviewRow {
  date: string | null;
  deposit: number;
  withdrawal: number;
  description: string | null;
  reference_number: string | null;
  duplicate: boolean;
}

export interface StatementPreview {
  bank_account: string;
  is_credit_card: boolean;
  total: number;
  duplicates: number;
  missing_date: number;
  new_count: number;
  deposit_total: number;
  withdrawal_total: number;
  from_date: string | null;
  to_date: string | null;
  rows: PreviewRow[];
  truncated: boolean;
}

export interface ImportResult {
  imported: number;
  skipped_duplicates: number;
  error_count: number;
  errors: Array<{ row: number; message: string }>;
}

export interface RuleCondition {
  field_name: string;
  condition: string;
  value: string;
}

export interface RuleDetail {
  name?: string;
  title: string;
  enabled: number;
  priority?: number;
  company?: string;
  bank_account: string;
  action_mode: "Auto Execute" | "Suggest Only";
  match_any_condition: number;
  entry_type: "Journal Entry" | "Payment Entry" | "";
  account?: string;
  party_type?: string;
  party?: string;
  cost_center?: string;
  project?: string;
  dimensions: Record<string, string>;
  conditions: RuleCondition[];
}

export interface RuleOptions {
  company: string;
  condition_fields: string[];
  condition_operators: string[];
  accounts: Array<{ name: string; account_name: string; account_type?: string }>;
  cost_centers: Array<{ name: string; cost_center_name: string }>;
  projects: Array<{ name: string; project_name: string }>;
  accounting_dimensions: AccountingDimension[];
  dimension_options: Record<string, DimensionOption[]>;
}

export interface RuleTestResult {
  matched: number;
  checked: number;
  samples: Array<{ name: string; date: string; description: string; amount: number }>;
}

export interface JobStatus {
  kind: "import" | "auto_reconcile";
  status: "queued" | "running" | "done" | "failed";
  stage?: string;
  message?: string;
  total: number;
  processed: number;
  // import
  imported?: number;
  skipped_duplicates?: number;
  error_count?: number;
  errors?: Array<{ row: number; message: string }>;
  from_date?: string | null;
  to_date?: string | null;
  rules?: { matched: number; unmatched: number; skipped: number; errors: number };
  auto_reconcile?: { matched: number; ambiguous: number; no_match: number; failed: number; batch_id?: string };
  // auto reconcile
  dry_run?: boolean;
  matched?: number;
  ambiguous?: number;
  no_match?: number;
  failed?: number;
  batch_id?: string | null;
  proposals?: Array<{
    bank_transaction: string;
    date: string;
    description: string;
    amount: number;
    voucher_type: string;
    voucher_name: string;
    reasons: string[];
  }>;
}

export interface AutoReconcileSettings {
  reference: boolean;
  party: boolean;
  date: boolean;
  date_days: number;
  one_only: boolean;
  unpaid_invoices: boolean;
}
