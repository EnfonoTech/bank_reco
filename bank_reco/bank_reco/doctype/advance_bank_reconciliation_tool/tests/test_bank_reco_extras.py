# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt
"""Tests for the Bank Reco additions on top of the upstream ABR feature set:

* CR/DR amount markers in the statement importer
* saved statement balances
* suggest-only rules (evaluate -> apply) and the action log
* undo of an applied rule (voucher cancelled, transaction unreconciled)
"""
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from bank_reco.api.history import get_action_history, undo_batch
from bank_reco.api.statement_balance import get_statement_balance, set_statement_balance
from bank_reco.bank_reco.doctype.abr_bank_rule.abr_bank_rule import (
	_evaluate_suggestions,
	apply_suggested_rule,
	run_bank_rules,
)
from bank_reco.bank_reco.doctype.bank_statement_importer.bank_statement_importer import (
	split_cr_dr,
)

from .fixtures import (
	TEST_COMPANY,
	create_test_bank_transaction,
	setup_abr_test_data,
)

EXPENSE_ACCOUNT = "_Test Account Cost for Goods Sold - _TC"
RULE_TEXT = "_BANKRECO-RULE-TEXT"


class TestSplitCrDr(FrappeTestCase):
	def test_trailing_marker(self):
		self.assertEqual(split_cr_dr("1,250.00 CR"), ("1,250.00", "CR"))
		self.assertEqual(split_cr_dr("300Dr."), ("300", "DR"))

	def test_leading_marker(self):
		self.assertEqual(split_cr_dr("DR 99.5"), ("99.5", "DR"))

	def test_no_marker_is_untouched(self):
		self.assertEqual(split_cr_dr("-45.10"), ("-45.10", None))
		self.assertEqual(split_cr_dr(12.5), (12.5, None))


class TestBankRecoExtras(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.bank_account = setup_abr_test_data(TEST_COMPANY)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()

	def _make_rule(self, action_mode):
		return frappe.get_doc(
			{
				"doctype": "ABR Bank Rule",
				"title": "_BankReco rule " + action_mode,
				"enabled": 1,
				"priority": 1,
				"company": TEST_COMPANY,
				"bank_account": self.bank_account,
				"action_mode": action_mode,
				"match_any_condition": 0,
				"conditions": [
					{"field_name": "Description", "condition": "Contains", "value": RULE_TEXT}
				],
				"entry_type": "Journal Entry",
				"account": EXPENSE_ACCOUNT,
			}
		).insert(ignore_permissions=True)

	def _make_transaction(self):
		return create_test_bank_transaction(
			self.bank_account,
			withdrawal=123.45,
			date=add_days(nowdate(), -1),
			description=RULE_TEXT + " card fee",
		)

	def test_statement_balance_roundtrip(self):
		date = nowdate()
		saved = set_statement_balance(self.bank_account, date, "1500.50")
		self.assertEqual(saved["closing_balance"], 1500.5)
		# a second save for the same date updates instead of duplicating
		set_statement_balance(self.bank_account, date, "1600")
		self.assertEqual(get_statement_balance(self.bank_account, date)["closing_balance"], 1600)
		self.assertEqual(
			frappe.db.count(
				"Bank Reco Statement Balance",
				{"bank_account": self.bank_account, "balance_date": date},
			),
			1,
		)
		# an empty value clears it
		self.assertIsNone(set_statement_balance(self.bank_account, date, ""))
		self.assertIsNone(get_statement_balance(self.bank_account, date))

	def test_suggest_only_rule_is_flagged_but_not_executed(self):
		rule = self._make_rule("Suggest Only")
		transaction = self._make_transaction()

		# the auto runner ignores Suggest Only rules
		result = run_bank_rules(self.bank_account, add_days(nowdate(), -7), nowdate())
		self.assertEqual(result["matched"], 0)
		transaction.reload()
		self.assertEqual(transaction.status, "Unreconciled")

		# evaluating flags the transaction and marks it evaluated
		outcome = _evaluate_suggestions(self.bank_account, force=True)
		self.assertGreaterEqual(outcome["suggested"], 1)
		transaction.reload()
		self.assertEqual(transaction.bank_reco_suggested_rule, rule.name)
		self.assertEqual(transaction.bank_reco_rule_evaluated, 1)

	def test_apply_suggestion_logs_and_undo_reverses(self):
		rule = self._make_rule("Suggest Only")
		transaction = self._make_transaction()
		_evaluate_suggestions(self.bank_account, force=True)

		applied = apply_suggested_rule(transaction.name)
		self.assertEqual(applied["rule"], rule.name)
		transaction.reload()
		self.assertEqual(transaction.status, "Reconciled")
		self.assertFalse(transaction.bank_reco_suggested_rule)

		history = get_action_history(self.bank_account, mine_only=0)
		batch = next(b for b in history["batches"] if b["batch_id"] == applied["action_batch"])
		self.assertEqual(batch["active_count"], 1)
		voucher_name = batch["rows"][0]["voucher_name"]
		self.assertEqual(frappe.db.get_value("Journal Entry", voucher_name, "docstatus"), 1)

		outcome = undo_batch(applied["action_batch"])
		self.assertEqual(outcome["summary"]["undone"], 1)
		self.assertEqual(frappe.db.get_value("Journal Entry", voucher_name, "docstatus"), 2)
		transaction.reload()
		self.assertEqual(transaction.status, "Unreconciled")
		log_status = frappe.db.get_value("Bank Reco Action Log", batch["rows"][0]["name"], "status")
		self.assertEqual(log_status, "Undone")
