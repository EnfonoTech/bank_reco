# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt
"""Unit tests for the high-severity review fixes.

These need no site or database: Frappe is only used for ``_``, ``flt``, ``getdate`` and
``throw``, and every database touch is mocked. Run them from the bench ``sites`` directory
(Frappe writes its logs next to it):

	cd ~/frappe-bench/sites && PYTHONPATH=<path to bank_reco repo> \
		../env/bin/python -m unittest bank_reco.api.test_high_severity_fixes -v
"""

import contextlib
import json
import unittest
from unittest import mock

import frappe

from bank_reco.api import matching
from bank_reco.api import transfer
from bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool import (
	advance_bank_reconciliation_tool as tool,
)
from bank_reco.bank_reco.overrides import bank_transaction as override

HOSTILE_DATE = "2026-01-01' UNION SELECT password FROM __Auth -- "


_settings_patch = mock.patch.object(frappe, "get_system_settings", return_value=None)


def setUpModule():
	# flt(x, precision) reads the rounding method from System Settings, which does not exist
	# without a site (it silently rounds everything to 0.0), so give it the default.
	_settings_patch.start()
	# Only the pieces frappe.throw / frappe._ / frappe.flags need when no site is initialised.
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
	frappe.local.db = db
	try:
		yield db
	finally:
		if original is missing:
			del frappe.local.db
		else:
			frappe.local.db = original


class _Transaction(frappe._dict):
	def precision(self, fieldname):
		return 2


def bank_transaction(deposit=0, withdrawal=0, unallocated=1000, currency="AED", **extra):
	fields = dict(
		name="BT-1",
		bank_account="BANK-ACC-1",
		deposit=deposit,
		withdrawal=withdrawal,
		unallocated_amount=unallocated,
		currency=currency,
		reference_number="",
		party_type=None,
		party=None,
	)
	fields.update(extra)
	return _Transaction(**fields)


def candidate(source_type, amount, currency="AED"):
	return {
		"source_type": source_type,
		"amount": amount,
		"currency": currency,
		"reference_number": "",
		"party_type": None,
		"party": None,
	}


class TestMatchDirectionAndCurrency(unittest.TestCase):
	"""Equal numbers are only an 'Amount' match when the voucher moves money the same way."""

	DEPOSIT_CASES = [
		# (source type, signed amount, expected Amount match)
		("Payment Entry", 1000, True),
		("Payment Entry", -1000, False),
		("Journal Entry", 1000, True),
		("Journal Entry", -1000, False),
		("Sales Invoice", 1000, True),
		("Sales Invoice", -1000, False),
		("Unpaid Sales Invoice", 1000, True),
		("Unpaid Sales Invoice", -1000, False),
		("Purchase Invoice", -1000, True),
		("Purchase Invoice", 1000, False),
		("Unpaid Purchase Invoice", -1000, True),
		("Unpaid Purchase Invoice", 1000, False),
	]
	WITHDRAWAL_CASES = [
		("Payment Entry", 1000, True),
		("Payment Entry", -1000, False),
		("Sales Invoice", -1000, True),
		("Sales Invoice", 1000, False),
		("Unpaid Sales Invoice", -1000, True),
		("Unpaid Sales Invoice", 1000, False),
		("Purchase Invoice", 1000, True),
		("Purchase Invoice", -1000, False),
		("Unpaid Purchase Invoice", 1000, True),
		("Unpaid Purchase Invoice", -1000, False),
	]

	def assert_cases(self, transaction, cases):
		for source_type, amount, expected in cases:
			with self.subTest(source_type=source_type, amount=amount):
				reasons = matching._candidate_reasons(candidate(source_type, amount), transaction)
				self.assertEqual("Amount" in reasons, expected, reasons)

	def test_deposit_direction_matrix(self):
		self.assert_cases(bank_transaction(deposit=1000), self.DEPOSIT_CASES)

	def test_withdrawal_direction_matrix(self):
		self.assert_cases(bank_transaction(withdrawal=1000), self.WITHDRAWAL_CASES)

	def test_different_currency_is_not_an_amount_match(self):
		reasons = matching._candidate_reasons(
			candidate("Unpaid Sales Invoice", 1000, currency="USD"),
			bank_transaction(deposit=1000, currency="AED"),
		)
		self.assertNotIn("Amount", reasons)

	def test_missing_currency_is_not_held_against_a_candidate(self):
		reasons = matching._candidate_reasons(
			candidate("Payment Entry", 1000, currency=None), bank_transaction(deposit=1000)
		)
		self.assertIn("Amount", reasons)

	def test_voucher_types_the_check_does_not_know_are_not_judged(self):
		for source_type in ("Bank Transaction", "Loan Disbursement"):
			self.assertTrue(
				matching.candidate_same_direction(
					candidate(source_type, -1000), bank_transaction(deposit=1000)
				)
			)

	def test_amounts_compare_at_currency_precision(self):
		reasons = matching._candidate_reasons(
			candidate("Payment Entry", 100.00000001), bank_transaction(deposit=100, unallocated=100)
		)
		self.assertIn("Amount", reasons)


class TestMatchingQueriesAreParameterised(unittest.TestCase):
	def setUp(self):
		self._flag = frappe.flags.get("auto_reconcile_vouchers")
		frappe.flags.auto_reconcile_vouchers = True
		self.transaction = bank_transaction(withdrawal=100, reference_number="A'B")

	def tearDown(self):
		frappe.flags.auto_reconcile_vouchers = self._flag

	def test_payment_entry_query_binds_dates_and_reference(self):
		sql = tool.get_pe_matching_query(
			False, "paid_from", self.transaction, None, None, 1, HOSTILE_DATE, "2026-01-31"
		)
		self.assertNotIn("UNION", sql)
		self.assertNotIn("A'B", sql)
		self.assertIn("%(from_reference_date)s", sql)
		self.assertIn("%(to_reference_date)s", sql)
		self.assertIn("reference_no = %(reference_no)s", sql)

	def test_journal_entry_query_binds_dates_reference_and_direction(self):
		sql = tool.get_je_matching_query(
			False, self.transaction, None, None, 1, HOSTILE_DATE, "2026-01-31"
		)
		self.assertNotIn("UNION", sql)
		self.assertNotIn("A'B", sql)
		self.assertIn("je.cheque_date between %(from_reference_date)s", sql)
		self.assertIn("je.cheque_no = %(reference_no)s", sql)
		self.assertIn("%(is_deposit)s", sql)

	def test_bank_transaction_query_binds_name_and_bank_account(self):
		sql = tool.get_bt_matching_query(False, self.transaction)
		self.assertNotIn("BT-1", sql)
		self.assertNotIn("BANK-ACC-1", sql)
		self.assertIn("%(bt_name)s", sql)
		self.assertIn("%(bt_bank_account)s", sql)

	def test_missing_reference_dates_fall_back_to_posting_date(self):
		for sql in (
			tool.get_pe_matching_query(False, "paid_from", self.transaction, None, None, 1, None, None),
			tool.get_je_matching_query(False, self.transaction, None, None, 1, None, None),
		):
			self.assertIn("%(from_date)s", sql)
			self.assertNotIn("%(from_reference_date)s", sql)


class TestGetLinkedPaymentsInput(unittest.TestCase):
	def run_get_linked_payments(self, **kwargs):
		stub = bank_transaction(deposit=100)
		with mock.patch.object(tool, "assert_bank_transaction_access", return_value=stub), fake_db() as db, mock.patch.object(
			tool, "check_matching", return_value=[]
		) as check, mock.patch.object(tool, "subtract_allocations", side_effect=lambda gl, rows: rows):
			db.get_values.return_value = [frappe._dict(account="1000", company="C")]
			result = tool.get_linked_payments("BT-1", **kwargs)
		return result, check

	def test_reference_dates_that_are_not_dates_are_rejected_before_any_sql(self):
		with mock.patch.object(tool, "check_matching") as check:
			with self.assertRaises(frappe.ValidationError):
				self.run_get_linked_payments(
					document_types=["payment_entry"],
					filter_by_reference_date=1,
					from_reference_date=HOSTILE_DATE,
					to_reference_date="2026-01-31",
				)
			check.assert_not_called()

	def test_valid_reference_dates_reach_the_matcher_as_dates(self):
		_, check = self.run_get_linked_payments(
			document_types=["payment_entry"],
			filter_by_reference_date=1,
			from_reference_date="2026-01-01",
			to_reference_date="2026-01-31",
		)
		args = check.call_args[0]
		self.assertEqual(str(args[7]), "2026-01-01")
		self.assertEqual(str(args[8]), "2026-01-31")

	def test_missing_document_types_no_longer_crash(self):
		result, _ = self.run_get_linked_payments()
		self.assertEqual(result, [])


class TestLegacyAutoReconcileIsCapped(unittest.TestCase):
	def run_auto_reconcile(self, unallocated, linked_payments):
		updated = frappe._dict(status="Unreconciled", name="BT-1", unallocated_amount=0)
		with mock.patch.object(tool, "get_linked_payments", return_value=linked_payments), mock.patch.object(
			tool, "reconcile_vouchers", return_value=updated
		) as reconcile, mock.patch.object(frappe, "msgprint"):
			tool.start_auto_reconcile(
				[frappe._dict(name="BT-1", unallocated_amount=unallocated)],
				None,
				None,
				None,
				None,
				None,
			)
		return reconcile

	def test_total_is_capped_and_opposite_direction_vouchers_are_skipped(self):
		reconcile = self.run_auto_reconcile(
			100,
			[
				(5, "Payment Entry", "PE-1", 70.0),
				(4, "Payment Entry", "PE-2", -30.0),
				(3, "Payment Entry", "PE-3", 60.0),
				(2, "Payment Entry", "PE-4", 10.0),
			],
		)
		sent = json.loads(reconcile.call_args[0][1])
		self.assertEqual(
			[(row["payment_name"], row["amount"]) for row in sent],
			[("PE-1", 70.0), ("PE-3", 30.0)],
		)

	def test_nothing_is_reconciled_when_only_opposite_direction_vouchers_match(self):
		reconcile = self.run_auto_reconcile(100, [(5, "Payment Entry", "PE-2", -100.0)])
		reconcile.assert_not_called()


class TestBulkJobCleanup(unittest.TestCase):
	def test_cleanup_cancels_submitted_payment_entries_and_never_deletes(self):
		pe = mock.MagicMock(docstatus=1)
		pe.name = "PE-1"
		with mock.patch.object(frappe, "get_doc", return_value=pe), mock.patch.object(
			frappe, "delete_doc"
		) as delete_doc:
			cancelled = tool.cleanup_failed_reconciliation(
				[
					{"payment_doctype": "Payment Entry", "payment_name": "PE-1"},
					{"payment_doctype": "Journal Entry", "payment_name": "JV-1"},
				]
			)
		self.assertEqual(cancelled, ["PE-1"])
		pe.cancel.assert_called_once()
		delete_doc.assert_not_called()

	def test_cleanup_skips_entries_that_are_already_cancelled(self):
		pe = mock.MagicMock(docstatus=2)
		with mock.patch.object(frappe, "get_doc", return_value=pe):
			cancelled = tool.cleanup_failed_reconciliation(
				[{"payment_doctype": "Payment Entry", "payment_name": "PE-1"}]
			)
		self.assertEqual(cancelled, [])
		pe.cancel.assert_not_called()

	def test_one_failed_cancel_does_not_stop_the_rest(self):
		broken = mock.MagicMock(docstatus=1)
		broken.cancel.side_effect = frappe.ValidationError("period closed")
		good = mock.MagicMock(docstatus=1)
		good.name = "PE-2"
		with mock.patch.object(frappe, "get_doc", side_effect=[broken, good]):
			cancelled = tool.cleanup_failed_reconciliation(
				[
					{"payment_doctype": "Payment Entry", "payment_name": "PE-1"},
					{"payment_doctype": "Payment Entry", "payment_name": "PE-2"},
				]
			)
		self.assertEqual(cancelled, ["PE-2"])


class TestVoucherSelectionAccess(unittest.TestCase):
	def test_each_voucher_kind_is_checked_with_the_right_guard(self):
		with mock.patch.object(tool, "assert_voucher_access") as voucher_access, mock.patch.object(
			tool, "assert_bank_transaction_access"
		) as bt_access:
			tool._assert_voucher_selection_access(
				[
					{"payment_doctype": "Payment Entry", "payment_name": "PE-1"},
					{"payment_doctype": "Bank Transaction", "payment_name": "BT-2"},
					{"payment_doctype": "Loan Disbursement", "payment_name": "LD-1"},
				]
			)
		voucher_access.assert_called_once_with("Payment Entry", "PE-1")
		bt_access.assert_called_once_with("BT-2")

	def test_a_denied_voucher_stops_the_whole_selection(self):
		with mock.patch.object(tool, "assert_voucher_access", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				tool._assert_voucher_selection_access(
					[{"payment_doctype": "Sales Invoice", "payment_name": "SINV-1"}]
				)

	def test_empty_selection_is_a_no_op(self):
		tool._assert_voucher_selection_access(None)
		tool._assert_voucher_selection_access([])


class _AllocationStub:
	"""Just enough of a Bank Transaction for validate_allocation_not_exceeded."""

	name = "BT-1"
	bank_account = "BANK-ACC-1"

	def __init__(self, unallocated, rows):
		self.unallocated_amount = unallocated
		self._rows = rows

	def get(self, key):
		return self._rows if key == "payment_entries" else None


def allocation_row(name="PE-1", amount=50, document="Payment Entry"):
	return frappe._dict(payment_document=document, payment_entry=name, allocated_amount=amount)


class TestAllocationCaps(unittest.TestCase):
	def validate(
		self,
		unallocated=0,
		rows=(),
		previously_unallocated=10,
		saved=(),
		voucher_amount=100,
		already_allocated=60,
		has_gl=True,
	):
		stub = _AllocationStub(unallocated, list(rows))

		def get_value(doctype, name, fieldname):
			return {"unallocated_amount": previously_unallocated, "account": "BANK-GL"}[fieldname]

		gl = {("Payment Entry", "PE-1"): {"BANK-GL": voucher_amount}} if has_gl else {}
		allocations = {("Payment Entry", "PE-1"): {"BANK-GL": {"total": already_allocated}}}
		with fake_db() as db, mock.patch.object(
			frappe, "get_all", return_value=[allocation_row(*saved_row) for saved_row in saved]
		), mock.patch.object(override, "get_related_bank_gl_entries", return_value=gl), mock.patch.object(
			override, "get_total_allocated_amount", return_value=allocations
		):
			db.get_value.side_effect = get_value
			override.ExtendedBankTransaction.validate_allocation_not_exceeded(stub)

	def test_a_save_that_pushes_the_transaction_negative_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(unallocated=-5, previously_unallocated=10)

	def test_a_transaction_that_was_already_over_allocated_can_still_be_saved(self):
		self.validate(unallocated=-5, previously_unallocated=-5)

	def test_over_allocation_can_be_reduced(self):
		self.validate(unallocated=-2, previously_unallocated=-5)

	def test_a_voucher_cannot_be_allocated_beyond_what_is_left_of_it(self):
		# 100 on the books, 60 already held by other bank transactions -> 40 left
		with self.assertRaises(frappe.ValidationError):
			self.validate(rows=[allocation_row(amount=50)])

	def test_the_remaining_amount_can_be_allocated(self):
		self.validate(rows=[allocation_row(amount=40)])

	def test_a_signed_refund_allocation_is_capped_by_its_magnitude(self):
		with self.assertRaises(frappe.ValidationError):
			self.validate(rows=[allocation_row(amount=-45)])

	def test_rows_that_were_already_saved_are_not_judged_again(self):
		self.validate(rows=[allocation_row(amount=50)], saved=[("PE-1", 50, "Payment Entry")])

	def test_vouchers_without_a_bank_gl_entry_are_left_to_erpnext(self):
		self.validate(rows=[allocation_row(amount=500)], has_gl=False)

	def test_bank_transaction_rows_are_not_capped_here(self):
		self.validate(rows=[allocation_row(name="BT-9", amount=500, document="Bank Transaction")])


class TestInternalTransferExchangeRate(unittest.TestCase):
	def create_transfer(self, company_currency="AED", own_currency="AED", counter_currency="AED", rate=None):
		transaction = bank_transaction(withdrawal=100, unallocated=100, company="Test Co", date="2026-10-01")
		transaction.status = "Unreconciled"
		transaction.reload = lambda: None
		account = mock.MagicMock(company="Test Co", is_group=0, account_type="Bank")
		currencies = {
			("Company", "Test Co", "default_currency"): company_currency,
			("Account", "OWN-GL", "account_currency"): own_currency,
			("Account", "OTHER-GL", "account_currency"): counter_currency,
			("Bank Account", "BANK-ACC-1", "account"): "OWN-GL",
		}
		payment = mock.MagicMock()
		payment.name = "PE-NEW"
		created = []

		def get_doc(arg, *rest):
			if isinstance(arg, dict):
				created.append(arg)
				return payment
			return account

		patches = [
			mock.patch.object(transfer, "require_bank_rec_permission"),
			mock.patch.object(transfer, "assert_bank_transaction_access", return_value=transaction),
			mock.patch.object(transfer, "_lock_bank_transaction"),
			mock.patch.object(transfer, "assert_company_access", return_value="Test Co"),
			mock.patch.object(transfer, "new_batch_id", return_value="B1"),
			mock.patch.object(
				transfer,
				"_submit_match",
				return_value={"transaction": {}},
			),
			mock.patch.object(frappe, "get_cached_value", side_effect=lambda *key: currencies[key]),
			mock.patch.object(frappe, "get_doc", side_effect=get_doc),
			mock.patch.object(frappe, "has_permission"),
			mock.patch("erpnext.setup.utils.get_exchange_rate", return_value=rate),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		transfer._create_internal_transfer("BT-1", None, "OTHER-GL", None, None)
		return created[0]

	def test_company_currency_transfer_uses_rate_one(self):
		doc = self.create_transfer()
		self.assertEqual((doc["source_exchange_rate"], doc["target_exchange_rate"]), (1, 1))

	def test_foreign_currency_transfer_uses_the_real_rate_on_both_legs(self):
		doc = self.create_transfer(company_currency="AED", own_currency="USD", counter_currency="USD", rate=3.6725)
		self.assertEqual((doc["source_exchange_rate"], doc["target_exchange_rate"]), (3.6725, 3.6725))

	def test_missing_exchange_rate_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.create_transfer(own_currency="USD", counter_currency="USD", rate=0)

	def test_transfer_between_different_currencies_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.create_transfer(own_currency="USD", counter_currency="AED")


if __name__ == "__main__":
	unittest.main()
