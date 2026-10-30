# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt
"""Unit tests for the medium-severity review fixes.

These need no site or database: every database touch is mocked. Run them from the bench
``sites`` directory (Frappe writes its logs next to it):

	cd ~/frappe-bench/sites && PYTHONPATH=<path to bank_reco repo> \
		../env/bin/python -m unittest bank_reco.api.test_medium_severity_fixes -v
"""

import contextlib
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from bank_reco.api import auto_match, statement_balance, statement_import
from bank_reco.bank_reco.doctype.abr_bank_rule import abr_bank_rule as rules
from bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool import (
	advance_bank_reconciliation_tool as tool,
)
from bank_reco.bank_reco.doctype.bank_statement_importer import bank_statement_importer as importer

_settings_patch = mock.patch.object(frappe, "get_system_settings", return_value=None)


def setUpModule():
	# flt(x, precision) reads the rounding method from System Settings, which does not exist
	# without a site (it silently rounds everything to 0.0), so give it the default.
	_settings_patch.start()
	local = frappe.local
	if not hasattr(local, "flags"):
		local.flags = frappe._dict()
	if not hasattr(local, "message_log"):
		local.message_log = []
	if not hasattr(local, "lang"):
		local.lang = "en"
	if not hasattr(local, "session"):
		local.session = frappe._dict(user="Administrator")


def tearDownModule():
	_settings_patch.stop()


@contextlib.contextmanager
def fake_db():
	"""frappe.db is a proxy that cannot be patched; swap the object behind it instead."""
	missing = object()
	original = getattr(frappe.local, "db", missing)
	db = mock.MagicMock()
	db.exists.return_value = None
	frappe.local.db = db
	try:
		yield db
	finally:
		if original is missing:
			del frappe.local.db
		else:
			frappe.local.db = original


class TestStatementBalanceAmounts(unittest.TestCase):
	"""``flt`` reads ``1.250,50`` as 1.2505 and a bare ``1,250`` as 1: use the statement parser."""

	def save(self, typed):
		doc = mock.MagicMock()
		with fake_db(), mock.patch.object(
			statement_balance, "assert_bank_account_access"
		), mock.patch.object(frappe, "get_doc", return_value=doc) as get_doc, mock.patch.object(
			statement_balance, "get_saved_balance", return_value=None
		):
			statement_balance.set_statement_balance("BA-1", "2026-03-31", typed)
		return get_doc.call_args.args[0]["closing_balance"]

	def test_european_grouping(self):
		self.assertEqual(self.save("1.250,50"), 1250.5)

	def test_thousands_comma(self):
		self.assertEqual(self.save("1,250"), 1250)

	def test_plain_number_and_negative_in_brackets(self):
		self.assertEqual(self.save("1500.50"), 1500.5)
		self.assertEqual(self.save("(1,250.00)"), -1250)

	def test_a_number_value_is_kept(self):
		self.assertEqual(self.save(99.5), 99.5)

	def test_garbage_is_refused_not_stored_as_zero(self):
		with fake_db(), mock.patch.object(statement_balance, "assert_bank_account_access"), mock.patch.object(
			frappe, "get_doc"
		) as get_doc:
			with self.assertRaises(frappe.ValidationError):
				statement_balance.set_statement_balance("BA-1", "2026-03-31", "twelve")
		get_doc.assert_not_called()


class TestBulkJobSavepoints(unittest.TestCase):
	"""One bad invoice must not leave a half-made Payment Entry in the batch that is committed."""

	def run_job(self, outcomes):
		invoices = [
			{"name": "SINV-%d" % i, "doctype": "Sales Invoice", "allocated_amount": 10}
			for i in range(len(outcomes))
		]
		created = []

		def make_pe(*args, **kwargs):
			outcome = outcomes[len(created)]
			created.append(outcome)
			if isinstance(outcome, Exception):
				raise outcome
			return SimpleNamespace(name=outcome)

		bank_transaction = SimpleNamespace(name="BT-1", unallocated_amount=1000, customer="CUST")
		with fake_db() as db, mock.patch.object(frappe, "set_user"), mock.patch.object(
			frappe, "get_single_value", create=True, return_value=0
		), mock.patch.object(frappe, "get_doc", return_value=bank_transaction), mock.patch.object(
			frappe, "cache"
		), mock.patch.object(tool, "get_logger"), mock.patch.object(
			tool, "publish_progress"
		), mock.patch.object(tool, "publish_completion"), mock.patch.object(
			tool, "_validate_bulk_transfer_selection"
		), mock.patch.object(tool, "create_payment_entry_for_invoice", side_effect=make_pe), mock.patch.object(
			tool, "reconcile_vouchers", return_value=SimpleNamespace(name="BT-1")
		), mock.patch(
			"time.sleep"
		):
			tool.process_bulk_reconciliation("BT-1", invoices, [], "JOB-1", "Administrator")
		return db

	def test_failed_invoice_is_rolled_back_to_its_own_savepoint(self):
		db = self.run_job(["PE-1", frappe.ValidationError("period closed"), "PE-3"])

		taken = [c.args[0] for c in db.savepoint.call_args_list]
		released = [c.args[0] for c in db.release_savepoint.call_args_list]
		self.assertEqual(len(set(taken)), 3)
		self.assertEqual(db.rollback.call_args_list, [mock.call(save_point=taken[1])])
		self.assertEqual(released, [taken[0], taken[2]])

	def test_all_good_rolls_nothing_back(self):
		db = self.run_job(["PE-1", "PE-2"])

		self.assertEqual(db.savepoint.call_count, 2)
		db.rollback.assert_not_called()


class TestPositiveFieldIsRemembered(unittest.TestCase):
	"""A single amount column carries its sign: which side a positive amount is must survive the next import."""

	def save(self, data, has_field=True):
		bank = mock.MagicMock()
		bank.bank_transaction_mapping = []
		bank.bank_statement_positive_field = ""
		bank.meta.has_field.return_value = has_field
		with mock.patch.object(
			importer, "assert_bank_account_access", return_value=SimpleNamespace(bank="BANK-A")
		), mock.patch.object(importer.frappe, "get_doc", return_value=bank):
			importer.save_bank_mapping_for_future_use({"bank_account": "BA-1", **data})
		return bank

	def test_saved_for_a_single_amount_column(self):
		bank = self.save(
			{"same_amount_field": 1, "amount_select": "Amount", "positive_field": "Withdrawal"}
		)
		self.assertEqual(bank.bank_statement_positive_field, "Withdrawal")
		bank.save.assert_called_once_with(ignore_permissions=True)

	def test_not_touched_for_separate_columns(self):
		bank = self.save(
			{
				"same_amount_field": 0,
				"deposit_select": "Credit",
				"withdrawal_select": "Debit",
				"positive_field": "Withdrawal",
			}
		)
		self.assertEqual(bank.bank_statement_positive_field, "")

	def test_only_a_known_side_is_stored(self):
		bank = self.save({"same_amount_field": 1, "amount_select": "Amount", "positive_field": "<script>"})
		self.assertEqual(bank.bank_statement_positive_field, "")

	def test_a_site_without_the_new_field_still_saves(self):
		bank = self.save(
			{"same_amount_field": 1, "amount_select": "Amount", "positive_field": "Withdrawal"},
			has_field=False,
		)
		self.assertEqual(bank.bank_statement_positive_field, "")
		bank.save.assert_called_once_with(ignore_permissions=True)

	def test_start_import_hands_the_saved_side_back(self):
		bank = SimpleNamespace(
			bank_transaction_mapping=[],
			bank_statement_date_format="Auto",
			bank_statement_positive_field="Withdrawal",
		)

		def get_doc(doctype, name):
			return SimpleNamespace(bank="BANK-A") if doctype == "Bank Account" else bank

		with fake_db() as db, mock.patch.object(importer.frappe, "get_doc", side_effect=get_doc):
			db.exists.return_value = True
			mapping = importer.start_import("/private/files/s.csv", "BA-1", include_rows=False)["bank_mapping"]
		self.assertEqual(mapping["positive_field"], "Withdrawal")

	def parse(self, saved):
		with mock.patch.object(statement_import, "require_bank_rec_permission"), mock.patch.object(
			statement_import, "assert_bank_account_access"
		), mock.patch.object(
			statement_import,
			"_read_file",
			return_value=(["Date", "Amount", "Description"], [["2026-03-14", "10.00", "x"]], [2]),
		), mock.patch.object(statement_import, "start_import", return_value={"bank_mapping": saved}):
			return statement_import.parse_statement("/private/files/s.csv", "BA-1")["mapping"]

	def test_parse_statement_applies_the_saved_side(self):
		mapping = self.parse(
			{"date": "Date", "deposit": "Amount", "withdrawal": "Amount", "positive_field": "Withdrawal"}
		)
		self.assertEqual(mapping["same_amount_field"], 1)
		self.assertEqual(mapping["positive_field"], "Withdrawal")

	def test_parse_statement_defaults_to_deposit_when_nothing_was_saved(self):
		mapping = self.parse({"date": "Date", "deposit": "Amount", "withdrawal": "Amount"})
		self.assertEqual(mapping["positive_field"], "Deposit")


class TestHourlyAutoReconcileIsQueued(unittest.TestCase):
	def run_hourly(self, accounts, enqueue_effect=None):
		with mock.patch.object(auto_match, "_single_flag", return_value=True), mock.patch.object(
			frappe, "get_all", return_value=accounts
		), mock.patch.object(auto_match, "new_job", return_value="JOB"), mock.patch.object(
			frappe, "enqueue", side_effect=enqueue_effect
		) as enqueue, mock.patch.object(auto_match, "run_auto_reconcile") as inline, mock.patch.object(
			frappe, "log_error"
		) as log_error:
			auto_match.scheduled_auto_reconcile()
		return enqueue, inline, log_error

	def test_each_account_is_queued_on_the_long_queue_not_run_inline(self):
		enqueue, inline, _log = self.run_hourly(["BA-1", "BA-2"])

		inline.assert_not_called()
		self.assertEqual(enqueue.call_count, 2)
		for call in enqueue.call_args_list:
			self.assertEqual(call.kwargs["queue"], "long")
			self.assertEqual(call.kwargs["timeout"], auto_match.JOB_TIMEOUT)
			self.assertTrue(call.kwargs["deduplicate"])
			self.assertEqual(call.kwargs["user"], "Administrator")
			self.assertFalse(call.kwargs["dry_run"])
		job_ids = {call.kwargs["job_id"] for call in enqueue.call_args_list}
		self.assertEqual(len(job_ids), 2)

	def test_one_account_failing_to_queue_does_not_stop_the_rest(self):
		enqueue, _inline, log_error = self.run_hourly(["BA-1", "BA-2"], [RuntimeError("redis down"), None])

		self.assertEqual(enqueue.call_count, 2)
		log_error.assert_called_once()

	def test_switched_off_queues_nothing(self):
		with mock.patch.object(auto_match, "_single_flag", return_value=False), mock.patch.object(
			frappe, "enqueue"
		) as enqueue:
			auto_match.scheduled_auto_reconcile()
		enqueue.assert_not_called()


def _rule(name, mode):
	return frappe._dict(
		name=name,
		title=name,
		action_mode=mode,
		match_any_condition=0,
		conditions=[frappe._dict(field_name="Description", condition="Contains", value="fee")],
	)


class TestRulesRun(unittest.TestCase):
	def run_rules(self, rule_list, existing_voucher=False):
		transaction = frappe._dict(
			name="BT-1", date="2026-03-14", description="card fee", unallocated_amount=50
		)

		def load_rules(bank_account, action_mode=None):
			return [r for r in rule_list if not action_mode or rules._rule_mode(r) == action_mode]

		with fake_db(), mock.patch.object(frappe, "has_permission"), mock.patch.object(
			rules, "get_bank_transactions", return_value=[SimpleNamespace(name="BT-1")]
		), mock.patch.object(rules, "_load_rules", side_effect=load_rules), mock.patch.object(
			frappe, "get_doc", return_value=transaction
		), mock.patch.object(rules, "get_logger"), mock.patch.object(
			rules, "new_batch_id", return_value="B1"
		), mock.patch.object(frappe, "msgprint"), mock.patch.object(
			rules, "_execute_rule_action"
		) as execute, mock.patch.object(
			rules, "has_existing_voucher_match", create=True, return_value=existing_voucher
		):
			result = rules.run_bank_rules("BA-1", "2026-03-01", "2026-03-31")
		return result, execute

	def test_a_suggest_only_rule_holds_the_transaction_from_lower_auto_rules(self):
		result, execute = self.run_rules(
			[_rule("suggest first", "Suggest Only"), _rule("auto second", "Auto Execute")]
		)

		execute.assert_not_called()
		self.assertEqual(result["matched"], 0)
		self.assertEqual(result["held_for_review"], 1)

	def test_an_auto_rule_above_a_suggest_rule_still_runs(self):
		result, execute = self.run_rules(
			[_rule("auto first", "Auto Execute"), _rule("suggest second", "Suggest Only")]
		)

		execute.assert_called_once()
		self.assertEqual(result["matched"], 1)

	def test_a_transaction_with_a_matching_voucher_gets_no_second_one(self):
		result, execute = self.run_rules([_rule("auto", "Auto Execute")], existing_voucher=True)

		execute.assert_not_called()
		self.assertEqual(result["matched"], 0)
		self.assertEqual(result["existing_voucher"], 1)

	def test_without_an_existing_voucher_the_rule_posts(self):
		result, execute = self.run_rules([_rule("auto", "Auto Execute")], existing_voucher=False)

		execute.assert_called_once()
		self.assertEqual(result["matched"], 1)
		self.assertEqual(result["existing_voucher"], 0)


class TestExistingVoucherMatch(unittest.TestCase):
	def check(self, candidates):
		transaction = SimpleNamespace(name="BT-1", date="2026-03-14")
		with mock.patch.object(
			auto_match, "get_auto_match_settings", return_value={"s": 1}
		), mock.patch.object(auto_match, "qualifying_candidates", return_value=candidates) as qualifying:
			found = rules.has_existing_voucher_match(transaction)
		return found, qualifying

	def test_true_when_the_auto_reconcile_criteria_find_a_voucher(self):
		found, qualifying = self.check([{"voucher_name": "PE-1"}])

		self.assertTrue(found)
		self.assertEqual(qualifying.call_args.args[0], "BT-1")
		self.assertEqual(qualifying.call_args.args[2], {"s": 1})

	def test_false_when_nothing_qualifies(self):
		found, _qualifying = self.check([])

		self.assertFalse(found)


if __name__ == "__main__":
	unittest.main()
