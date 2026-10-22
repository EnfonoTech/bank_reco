# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import operator as op

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from bank_reco.api.permission import (
	assert_bank_account_access,
	assert_bank_transaction_access,
	assert_party_access,
)
from bank_reco.utils.action_log import log_new_links, new_batch_id, snapshot_links
from bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool.advance_bank_reconciliation_tool import (
	create_journal_entry_bts,
	create_payment_entry_bts,
	get_bank_transactions,
)
from bank_reco.utils.logger import get_logger


class ABRBankRule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from bank_reco.bank_reco.doctype.abr_bank_rule_condition.abr_bank_rule_condition import (
			ABRBankRuleCondition,
		)

		account: DF.Link | None
		action_mode: DF.Literal["Auto Execute", "Suggest Only"]
		bank_account: DF.Link
		company: DF.Link
		conditions: DF.Table[ABRBankRuleCondition]
		cost_center: DF.Link | None
		enabled: DF.Check
		entry_type: DF.Literal["", "Journal Entry", "Payment Entry"]
		match_any_condition: DF.Check
		party: DF.DynamicLink | None
		party_type: DF.Link | None
		priority: DF.Int
		project: DF.Link | None
		title: DF.Data
	# end: auto-generated types

	NUMERIC_ONLY_OPERATORS = {"Greater than", "Greater than or Equals", "Less Than", "Less Than or Equals"}
	TEXT_ONLY_OPERATORS = {"Contains", "Not Contains"}
	NUMERIC_FIELDS = {"Deposit", "Withdrawal"}
	VALID_OPERATORS = {
		"Equals", "Not Equals", "Greater than", "Greater than or Equals",
		"Less Than", "Less Than or Equals", "Contains", "Not Contains",
	}

	def on_trash(self):
		"""Clear the abr_bank_rule reference on any JE/PE that was created by this rule."""
		for dt in ("Journal Entry", "Payment Entry"):
			if not frappe.db.has_column(dt, "abr_bank_rule"):
				get_logger().warning(
					"Column 'abr_bank_rule' does not exist on '%s'. "
					"Skipping reference cleanup for rule '%s'.",
					dt, self.name,
				)
				continue
			frappe.db.set_value(
				dt, {"abr_bank_rule": self.name}, "abr_bank_rule", "", update_modified=False
			)

	def validate(self):
		if not self.conditions:
			frappe.throw("At least one condition is required")
		if not self.entry_type:
			frappe.throw("Entry Type is required")
		if self.entry_type == "Journal Entry" and not self.account:
			frappe.throw("Account is required for Journal Entry rules")
		if self.entry_type == "Payment Entry":
			if not self.party_type:
				frappe.throw("Party Type is required for Payment Entry rules")
			if not self.party:
				frappe.throw("Party is required for Payment Entry rules")

		assert_party_access(
			self.party_type,
			self.party,
			company=self.company,
		)
		self._validate_bank_account_company()
		self._validate_conditions()

	def _validate_bank_account_company(self):
		if self.bank_account and self.company:
			ba_company = frappe.db.get_value("Bank Account", self.bank_account, "company")
			if ba_company and ba_company != self.company:
				frappe.throw(
					"Bank Account '%s' belongs to company '%s', not '%s'"
					% (self.bank_account, ba_company, self.company)
				)

	def _validate_conditions(self):
		for cond in self.conditions:
			if not cond.value:
				frappe.throw("Value is required for condition on field '%s'" % cond.field_name)
			if cond.field_name not in CONDITION_FIELD_MAP:
				frappe.throw("Unknown condition field '%s'" % cond.field_name)
			if cond.condition not in self.VALID_OPERATORS:
				frappe.throw("Unknown operator '%s'" % cond.condition)
			if cond.condition in self.NUMERIC_ONLY_OPERATORS and cond.field_name not in self.NUMERIC_FIELDS:
				frappe.throw(
					"Operator '%s' is only valid for numeric fields (Deposit, Withdrawal), not '%s'"
					% (cond.condition, cond.field_name)
				)
			if cond.condition in self.TEXT_ONLY_OPERATORS and cond.field_name in self.NUMERIC_FIELDS:
				frappe.throw(
					"Operator '%s' is not valid for numeric fields, not '%s'"
					% (cond.condition, cond.field_name)
				)


# --- Field name to Bank Transaction fieldname mapping ---

CONDITION_FIELD_MAP = {
	"Deposit": ("deposit", True),
	"Withdrawal": ("withdrawal", True),
	"Currency": ("currency", False),
	"Description": ("description", False),
	"Reference Number": ("reference_number", False),
	"Particulars": ("custom_particulars", False),
	"Other Party": ("bank_party_name", False),
	"Code": ("custom_code", False),
}
"""Maps condition field label -> (transaction_fieldname, is_numeric)"""


# --- Rule engine ---


@frappe.whitelist()
def run_bank_rules(bank_account, from_date, to_date):
	"""Run all enabled ABR Bank Rules against unreconciled bank transactions."""
	frappe.has_permission("ABR Bank Rule", throw=True)
	logger = get_logger()

	if not bank_account or not from_date or not to_date:
		frappe.throw("Bank account, from date and to date are required")

	transactions = get_bank_transactions(bank_account, from_date, to_date)
	rules = _load_rules(bank_account, action_mode=AUTO_EXECUTE)
	batch_id = new_batch_id()

	logger.info(
		"Running %s rules against %s transactions for bank account '%s'",
		len(rules),
		len(transactions),
		bank_account,
	)

	matched_count = 0
	unmatched_count = 0
	error_count = 0
	skipped_count = 0

	for txn_summary in transactions:
		matched_rule = None
		try:
			transaction = frappe.get_doc("Bank Transaction", txn_summary.name)
			if not transaction.unallocated_amount or transaction.unallocated_amount <= 0:
				skipped_count += 1
				continue
			matched_rule = _match_transaction(transaction, rules, logger)
			if matched_rule:
				_execute_rule_action(transaction, matched_rule, logger, batch_id=batch_id)
				frappe.db.commit()
				matched_count += 1
			else:
				unmatched_count += 1
		except Exception:
			error_count += 1
			tb = frappe.get_traceback()
			rule_title = matched_rule.title if matched_rule else "N/A"
			logger.error(
				"Error processing transaction '%s' (rule: '%s'): %s",
				txn_summary.name, rule_title, tb,
			)
			frappe.db.rollback()
			try:
				frappe.log_error(
					message="Bank Account: %s\nRule: %s\nTransaction: %s\n\n%s"
					% (bank_account, rule_title, txn_summary.name, tb),
					title="ABR Bank Rule failed for %s" % txn_summary.name,
				)
				frappe.db.commit()
			except Exception:
				logger.error(
					"Failed to persist Error Log for transaction '%s': %s",
					txn_summary.name, frappe.get_traceback(),
				)

	result_msg = "Bank Rules Result: %s matched, %s unmatched, %s skipped, %s errors" % (
		matched_count,
		unmatched_count,
		skipped_count,
		error_count,
	)
	logger.info(
		"Bank Rules Result: %s matched, %s unmatched, %s skipped, %s errors",
		matched_count, unmatched_count, skipped_count, error_count,
	)

	indicator = "orange" if error_count else "green"
	frappe.msgprint(result_msg, indicator=indicator, title="Bank Rules")

	return {
		"matched": matched_count,
		"unmatched": unmatched_count,
		"skipped": skipped_count,
		"errors": error_count,
	}


@frappe.whitelist()
def create_bank_rule_from_voucher(
	bank_transaction_name,
	title,
	entry_type,
	conditions,
	match_any_condition=0,
	second_account=None,
	party_type=None,
	party=None,
	cost_center=None,
	project=None,
	dimensions=None,
):
	"""Create an ABR Bank Rule pre-populated from a manually created voucher."""
	frappe.has_permission("ABR Bank Rule", "create", throw=True)

	conditions = frappe.parse_json(conditions) if isinstance(conditions, str) else conditions
	if not conditions or not isinstance(conditions, list):
		frappe.throw(frappe._("At least one condition is required to create a bank rule"))

	bank_account = frappe.db.get_value("Bank Transaction", bank_transaction_name, "bank_account")
	company = frappe.db.get_value("Bank Transaction", bank_transaction_name, "company")
	if not bank_account or not company:
		frappe.throw(frappe._("Bank Transaction {0} not found").format(bank_transaction_name))

	dimensions = frappe.parse_json(dimensions) if isinstance(dimensions, str) else (dimensions or {})

	try:
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
			get_accounting_dimensions,
		)

		valid_dimension_keys = set(get_accounting_dimensions()) - {"cost_center", "project"}
	except ImportError:
		valid_dimension_keys = set()

	doc = frappe.get_doc({
		"doctype": "ABR Bank Rule",
		"title": title,
		"enabled": 1,
		"priority": 10,
		"company": company,
		"bank_account": bank_account,
		"match_any_condition": 1 if int(match_any_condition) else 0,
		"entry_type": entry_type,
		"account": second_account or None,
		"party_type": party_type or None,
		"party": party or None,
		"cost_center": cost_center or None,
		"project": project or None,
	})

	for key, value in dimensions.items():
		if key in valid_dimension_keys:
			doc.set(key, value)

	for cond in conditions:
		doc.append("conditions", {
			"field_name": cond.get("field_name"),
			"condition": cond.get("condition"),
			"value": cond.get("value"),
		})

	doc.insert()

	return {"name": doc.name, "title": title}


AUTO_EXECUTE = "Auto Execute"
SUGGEST_ONLY = "Suggest Only"


def _rule_mode(rule):
	# rules saved before action_mode existed have no value: they auto-execute
	return rule.get("action_mode") or AUTO_EXECUTE


def _load_rules(bank_account, action_mode=None):
	"""Load enabled rules for the bank account, ordered by priority.

	``action_mode`` limits the result to Auto Execute or Suggest Only rules;
	``None`` returns both.
	"""
	rule_names = frappe.get_all(
		"ABR Bank Rule",
		filters={"bank_account": bank_account, "enabled": 1},
		pluck="name",
		order_by="priority asc, creation asc",
	)
	rules = [frappe.get_doc("ABR Bank Rule", name) for name in rule_names]
	if action_mode:
		rules = [rule for rule in rules if _rule_mode(rule) == action_mode]
	return rules


def _match_transaction(transaction, rules, logger):
	"""Find the first matching rule for the transaction. Returns the rule or None."""
	for rule in rules:
		if _conditions_match(transaction, rule, logger):
			logger.info("Rule '%s' matched transaction '%s'", rule.title, transaction.name)
			return rule
	return None


def _conditions_match(transaction, rule, logger):
	"""Check if the rule's conditions match the transaction."""
	if not rule.conditions:
		return False
	if rule.match_any_condition:
		return any(evaluate_condition(transaction, cond, logger) for cond in rule.conditions)
	return all(evaluate_condition(transaction, cond, logger) for cond in rule.conditions)


def _get_rule_dimensions(rule):
	"""Read custom accounting dimension values from the rule."""
	try:
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
			get_accounting_dimensions,
		)
	except ImportError:
		get_logger().warning(
			"Could not import get_accounting_dimensions from ERPNext. "
			"Custom accounting dimensions will not be applied to entries "
			"created by ABR Bank Rules."
		)
		return {}

	excluded = {"cost_center", "project"}
	dimensions = {}
	for dim in get_accounting_dimensions():
		if dim in excluded:
			continue
		val = rule.get(dim)
		if val is not None and val != "":
			dimensions[dim] = val
	return dimensions


def _execute_rule_action(transaction, rule, logger, batch_id=None):
	"""Create a JE or PE based on the rule's action configuration and log it."""
	links_before = snapshot_links(transaction.name)
	_create_rule_voucher(transaction, rule, logger)
	log_new_links(
		transaction.name,
		links_before,
		batch_id=batch_id,
		details="Rule: " + (rule.title or rule.name),
	)
	if transaction.get("bank_reco_suggested_rule"):
		frappe.db.set_value(
			"Bank Transaction",
			transaction.name,
			"bank_reco_suggested_rule",
			"",
			update_modified=False,
		)


def _create_rule_voucher(transaction, rule, logger):
	dimensions = _get_rule_dimensions(rule)
	assert_party_access(
		rule.party_type,
		rule.party,
		company=transaction.company or rule.company,
	)

	if rule.entry_type == "Journal Entry":
		create_journal_entry_bts(
			transaction.name,
			transaction.reference_number or transaction.name,
			transaction.date,
			transaction.date,
			entry_type="Journal Entry",
			second_account=rule.account,
			party_type=rule.party_type,
			party=rule.party,
			cost_center=rule.cost_center,
			project=rule.project,
			abr_bank_rule=rule.name,
			dimensions=dimensions,
		)
	elif rule.entry_type == "Payment Entry":
		create_payment_entry_bts(
			transaction.name,
			transaction.reference_number or transaction.name,
			transaction.date,
			party_type=rule.party_type,
			party=rule.party,
			posting_date=transaction.date,
			cost_center=rule.cost_center,
			project=rule.project,
			abr_bank_rule=rule.name,
			dimensions=dimensions,
		)
	else:
		frappe.throw("Unsupported entry type '%s' in rule '%s'" % (rule.entry_type, rule.title))

	logger.info(
		"Created %s for transaction '%s' via rule '%s'",
		rule.entry_type,
		transaction.name,
		rule.title,
	)


NUMERIC_OPERATORS = {
	"Equals": op.eq,
	"Not Equals": op.ne,
	"Greater than": op.gt,
	"Greater than or Equals": op.ge,
	"Less Than": op.lt,
	"Less Than or Equals": op.le,
}


def evaluate_condition(transaction, condition, logger):
	"""Evaluate a single condition against a bank transaction."""
	field_info = CONDITION_FIELD_MAP.get(condition.field_name)
	if not field_info:
		logger.error("Unknown condition field '%s'", condition.field_name)
		return False

	fieldname, is_numeric = field_info
	field_value = transaction.get(fieldname)
	condition_value = condition.value
	operator = condition.condition

	logger.info(
		"Evaluating: field='%s' (%s) value='%s' op='%s' test='%s'",
		condition.field_name,
		fieldname,
		field_value,
		operator,
		condition_value,
	)

	numeric_op = NUMERIC_OPERATORS.get(operator)
	if numeric_op:
		if is_numeric or operator not in ("Equals", "Not Equals"):
			return numeric_op(flt(field_value), flt(condition_value))
		return numeric_op(str(field_value or "").lower(), str(condition_value or "").lower())

	if operator == "Contains":
		return bool(field_value) and condition_value.lower() in str(field_value).lower()
	if operator == "Not Contains":
		if not field_value:
			return False
		return condition_value.lower() not in str(field_value).lower()

	logger.error("Unknown operator '%s'", operator)
	return False


def _evaluate_suggestions(bank_account, from_date=None, to_date=None, force=False, limit=None):
	"""Flag unreconciled transactions that match an enabled rule (any mode).

	Nothing is posted. The first matching rule (by priority) is stored on the
	transaction as its suggestion, and the transaction is marked evaluated so
	the hourly job does not look at it again until ``force`` is used.
	"""
	logger = get_logger()
	rules = _load_rules(bank_account)
	if not rules:
		return {"evaluated": 0, "suggested": 0}

	filters = {
		"bank_account": bank_account,
		"docstatus": 1,
		"unallocated_amount": [">", 0],
	}
	if not force:
		filters["bank_reco_rule_evaluated"] = 0
	if from_date or to_date:
		filters["date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]

	names = frappe.get_all(
		"Bank Transaction",
		filters=filters,
		pluck="name",
		order_by="date asc",
		limit_page_length=limit or 0,
	)

	suggested = 0
	for name in names:
		transaction = frappe.get_doc("Bank Transaction", name)
		matched = _match_transaction(transaction, rules, logger)
		frappe.db.set_value(
			"Bank Transaction",
			name,
			{
				"bank_reco_suggested_rule": matched.name if matched else "",
				"bank_reco_rule_evaluated": 1,
			},
			update_modified=False,
		)
		if matched:
			suggested += 1

	return {"evaluated": len(names), "suggested": suggested}


@frappe.whitelist()
def run_rule_suggestions(bank_account, from_date=None, to_date=None, force=0):
	"""Evaluate rules against unreconciled transactions and store suggestions."""
	frappe.has_permission("ABR Bank Rule", throw=True)
	assert_bank_account_access(bank_account)
	return _evaluate_suggestions(
		bank_account,
		from_date=from_date,
		to_date=to_date,
		force=bool(frappe.utils.cint(force)),
	)


@frappe.whitelist()
def apply_suggested_rule(bank_transaction_name):
	"""Post the voucher of the rule suggested for a transaction and reconcile it."""
	transaction = assert_bank_transaction_access(bank_transaction_name, ptype="write")
	frappe.db.sql(
		"select name from `tabBank Transaction` where name=%s for update",
		transaction.name,
	)
	transaction.reload()

	rule_name = transaction.get("bank_reco_suggested_rule")
	if not rule_name:
		frappe.throw(_("No bank rule is suggested for this transaction."))
	if transaction.status == "Reconciled" or flt(transaction.unallocated_amount) <= 0:
		frappe.throw(_("This bank transaction is already reconciled."))

	rule = frappe.get_doc("ABR Bank Rule", rule_name)
	frappe.has_permission("ABR Bank Rule", "read", doc=rule, throw=True)
	if not rule.enabled:
		frappe.throw(_("Bank rule {0} is disabled.").format(rule.title))
	if rule.bank_account != transaction.bank_account:
		frappe.throw(_("Bank rule {0} belongs to another bank account.").format(rule.title))
	if not _conditions_match(transaction, rule, get_logger()):
		frappe.throw(_("Bank rule {0} no longer matches this transaction.").format(rule.title))

	batch_id = new_batch_id()
	_execute_rule_action(transaction, rule, get_logger(), batch_id=batch_id)
	return {"bank_transaction": transaction.name, "rule": rule.name, "action_batch": batch_id}


@frappe.whitelist()
def dismiss_suggested_rule(bank_transaction_name):
	"""Drop the suggestion; the transaction stays marked as evaluated."""
	transaction = assert_bank_transaction_access(bank_transaction_name, ptype="write")
	frappe.db.set_value(
		"Bank Transaction",
		transaction.name,
		"bank_reco_suggested_rule",
		"",
		update_modified=False,
	)
	return {"bank_transaction": transaction.name}


def scheduled_rule_suggestions():
	"""Hourly job: refresh suggestions when enabled in the settings."""
	if not frappe.db.get_single_value(
		"Advance Bank Reconciliation Settings", "suggest_rules_hourly"
	):
		return

	for bank_account in sorted(
		set(frappe.get_all("ABR Bank Rule", filters={"enabled": 1}, pluck="bank_account"))
	):
		try:
			_evaluate_suggestions(bank_account, limit=500)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Bank Reco rule suggestions failed for " + str(bank_account),
				message=frappe.get_traceback(),
			)
