"""Create and manage bank rules from the Bank Reco screen.

A rule is an ``ABR Bank Rule``: conditions on a bank transaction (description,
amounts, reference ...) and an action (post a Journal Entry to an account, or
a Payment Entry for a party). The screen offers a simple form (direction,
description text, amount range) on top of the raw condition list.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from bank_reco.api.accounting_dimensions import get_accounting_dimension_context
from bank_reco.api.permission import (
	assert_bank_account_access,
	log_unexpected_api_exception,
	require_bank_rec_permission,
)
from bank_reco.bank_reco.doctype.abr_bank_rule.abr_bank_rule import (
	_conditions_match,
	_get_rule_dimensions,
	_rule_mode,
)
from bank_reco.utils.logger import get_logger

RULE_DOCTYPE = "ABR Bank Rule"
TEST_LIMIT = 500


def _condition_meta():
	"""Field and operator names exactly as the DocType stores them."""
	meta = frappe.get_meta("ABR Bank Rule Condition")
	return {
		"fields": [o for o in (meta.get_field("field_name").options or "").split("\n") if o],
		"operators": [o for o in (meta.get_field("condition").options or "").split("\n") if o],
	}


def _rule_dto(rule):
	return {
		"name": rule.name,
		"title": rule.title,
		"enabled": cint(rule.enabled),
		"priority": cint(rule.priority),
		"company": rule.company,
		"bank_account": rule.bank_account,
		"action_mode": _rule_mode(rule),
		"match_any_condition": cint(rule.match_any_condition),
		"entry_type": rule.entry_type,
		"account": rule.account,
		"party_type": rule.party_type,
		"party": rule.party,
		"cost_center": rule.cost_center,
		"project": rule.project,
		"dimensions": _get_rule_dimensions(rule),
		"conditions": [
			{"field_name": c.field_name, "condition": c.condition, "value": c.value}
			for c in rule.conditions
		],
	}


@frappe.whitelist()
def get_rule_options(bank_account):
	"""Everything the rule form needs for one bank account."""
	require_bank_rec_permission()
	bank_account_doc = assert_bank_account_access(bank_account)
	company = bank_account_doc.company
	meta = _condition_meta()

	return {
		"company": company,
		"condition_fields": meta["fields"],
		"condition_operators": meta["operators"],
		"accounts": frappe.get_list(
			"Account",
			filters={"company": company, "is_group": 0, "disabled": 0},
			fields=["name", "account_name", "account_type", "root_type"],
			order_by="account_name asc",
			limit_page_length=500,
		),
		"cost_centers": frappe.get_list(
			"Cost Center",
			filters={"company": company, "is_group": 0},
			fields=["name", "cost_center_name"],
			order_by="cost_center_name asc",
			limit_page_length=200,
		),
		"projects": frappe.get_list(
			"Project",
			filters={"company": company},
			fields=["name", "project_name"],
			order_by="modified desc",
			limit_page_length=200,
		),
		**get_accounting_dimension_context(company),
	}


@frappe.whitelist()
def get_rule(name):
	require_bank_rec_permission()
	rule = frappe.get_doc(RULE_DOCTYPE, name)
	frappe.has_permission(RULE_DOCTYPE, "read", doc=rule, throw=True)
	assert_bank_account_access(rule.bank_account)
	return _rule_dto(rule)


def _clean_conditions(conditions):
	meta = _condition_meta()
	rows = frappe.parse_json(conditions) if isinstance(conditions, str) else (conditions or [])
	cleaned = []
	for row in rows:
		field_name = row.get("field_name")
		operator = row.get("condition")
		value = row.get("value")
		if not field_name or not operator or value in (None, ""):
			continue
		if field_name not in meta["fields"]:
			frappe.throw(_("Unknown condition field: ") + str(field_name))
		if operator not in meta["operators"]:
			frappe.throw(_("Unknown condition operator: ") + str(operator))
		cleaned.append({"field_name": field_name, "condition": operator, "value": str(value)})
	return cleaned


def _build_rule_doc(payload, rule=None):
	bank_account_doc = assert_bank_account_access(payload.get("bank_account"))
	conditions = _clean_conditions(payload.get("conditions"))
	if not conditions:
		frappe.throw(_("Add at least one condition."))

	values = {
		"title": payload.get("title"),
		"enabled": cint(payload.get("enabled", 1)),
		"company": bank_account_doc.company,
		"bank_account": bank_account_doc.name,
		"action_mode": payload.get("action_mode") or "Auto Execute",
		"match_any_condition": cint(payload.get("match_any_condition")),
		"entry_type": payload.get("entry_type"),
		"account": payload.get("account") or None,
		"party_type": payload.get("party_type") or None,
		"party": payload.get("party") or None,
		"cost_center": payload.get("cost_center") or None,
		"project": payload.get("project") or None,
	}
	if not values["title"]:
		frappe.throw(_("Give the rule a title."))

	for fieldname, value in (payload.get("dimensions") or {}).items():
		values[fieldname] = value or None

	if rule is None:
		last = frappe.get_all(
			RULE_DOCTYPE,
			filters={"bank_account": bank_account_doc.name},
			pluck="priority",
			order_by="priority desc",
			limit_page_length=1,
		)
		values["priority"] = (cint(last[0]) if last else 0) + 1
		rule = frappe.new_doc(RULE_DOCTYPE)
	rule.update(values)
	rule.set("conditions", [])
	for row in conditions:
		rule.append("conditions", row)
	return rule


@frappe.whitelist()
def save_rule(payload, name=None):
	"""Create (no ``name``) or update a bank rule."""
	try:
		require_bank_rec_permission()
		payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
		if name:
			rule = frappe.get_doc(RULE_DOCTYPE, name)
			frappe.has_permission(RULE_DOCTYPE, "write", doc=rule, throw=True)
			assert_bank_account_access(rule.bank_account)
			if payload.get("bank_account") != rule.bank_account:
				frappe.throw(_("A rule cannot be moved to another bank account."))
			rule = _build_rule_doc(payload, rule)
			rule.save()
		else:
			frappe.has_permission(RULE_DOCTYPE, "create", throw=True)
			rule = _build_rule_doc(payload)
			rule.insert()
		return _rule_dto(rule)
	except Exception as exc:
		log_unexpected_api_exception(exc, "Bank Reco save_rule failed", rollback=True)
		raise


@frappe.whitelist()
def set_rule_enabled(name, enabled):
	require_bank_rec_permission()
	rule = frappe.get_doc(RULE_DOCTYPE, name)
	frappe.has_permission(RULE_DOCTYPE, "write", doc=rule, throw=True)
	assert_bank_account_access(rule.bank_account)
	rule.enabled = cint(enabled)
	rule.save()
	return _rule_dto(rule)


@frappe.whitelist()
def delete_rule(name):
	require_bank_rec_permission()
	rule = frappe.get_doc(RULE_DOCTYPE, name)
	frappe.has_permission(RULE_DOCTYPE, "delete", doc=rule, throw=True)
	assert_bank_account_access(rule.bank_account)
	frappe.delete_doc(RULE_DOCTYPE, name)
	return {"name": name}


@frappe.whitelist()
def reorder_rules(bank_account, names):
	"""Priority 1..n in the order given (the first rule that matches wins)."""
	require_bank_rec_permission()
	assert_bank_account_access(bank_account)
	names = frappe.parse_json(names) if isinstance(names, str) else names
	for position, name in enumerate(names, start=1):
		rule = frappe.get_doc(RULE_DOCTYPE, name)
		if rule.bank_account != bank_account:
			frappe.throw(_("Rule {0} belongs to another bank account.").format(name))
		frappe.has_permission(RULE_DOCTYPE, "write", doc=rule, throw=True)
		if cint(rule.priority) != position:
			frappe.db.set_value(RULE_DOCTYPE, name, "priority", position)
	return {"count": len(names)}


@frappe.whitelist()
def test_rule(payload):
	"""How many unreconciled transactions would this rule match (nothing is saved)."""
	require_bank_rec_permission()
	payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
	bank_account_doc = assert_bank_account_access(payload.get("bank_account"))
	rule = frappe.get_doc(
		{
			"doctype": RULE_DOCTYPE,
			"title": payload.get("title") or "test",
			"bank_account": bank_account_doc.name,
			"match_any_condition": cint(payload.get("match_any_condition")),
			"conditions": _clean_conditions(payload.get("conditions")),
		}
	)
	if not rule.conditions:
		return {"matched": 0, "checked": 0, "samples": []}

	logger = get_logger()
	names = frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": bank_account_doc.name,
			"docstatus": 1,
			"unallocated_amount": [">", 0],
		},
		pluck="name",
		order_by="date desc",
		limit_page_length=TEST_LIMIT,
	)
	samples, matched = [], 0
	for name in names:
		transaction = frappe.get_doc("Bank Transaction", name)
		if _conditions_match(transaction, rule, logger):
			matched += 1
			if len(samples) < 5:
				samples.append(
					{
						"name": name,
						"date": transaction.date,
						"description": transaction.description,
						"amount": flt(transaction.deposit) or -flt(transaction.withdrawal),
					}
				)
	return {"matched": matched, "checked": len(names), "samples": samples}
