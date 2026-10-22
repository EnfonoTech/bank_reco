# Copyright (c) 2026, HighFlyer and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from bank_reco.api.create_voucher import (
	create_voucher_draft_from_transaction,
	create_voucher_from_transaction,
)
from bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool.advance_bank_reconciliation_tool import (
	create_payment_entry_for_invoice,
)

from .fixtures import (
	TEST_COMPANY,
	TEST_CUSTOMER,
	TEST_SUPPLIER,
	create_test_bank_transaction,
	create_test_purchase_invoice,
	create_test_sales_invoice,
	ensure_bank_account_for_company,
	setup_abr_test_data,
)


class TestPaymentEntryBankAccount(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.bank_account = setup_abr_test_data(TEST_COMPANY)
		cls.unrelated_bank_account = ensure_bank_account_for_company(TEST_COMPANY)
		frappe.db.commit()

		selected = frappe.get_doc("Bank Account", cls.bank_account)
		selected.bank_account_no = "_ABR-SELECTED-001"
		selected.is_default = 0
		selected.save(ignore_permissions=True)

		unrelated = frappe.get_doc("Bank Account", cls.unrelated_bank_account)
		unrelated.bank_account_no = "_ABR-UNRELATED-002"
		unrelated.is_default = 1
		unrelated.save(ignore_permissions=True)

	def assert_transaction_bank_account(self, payment_entry, bank_side_field):
		selected = frappe.get_doc("Bank Account", self.bank_account)
		selected_ledger = frappe.get_doc("Account", selected.account)
		self.assertEqual(payment_entry.bank_account, selected.name)
		self.assertEqual(payment_entry.bank, selected.bank)
		self.assertEqual(payment_entry.bank_account_no, selected.bank_account_no)
		self.assertEqual(payment_entry.get(bank_side_field), selected.account)
		self.assertEqual(
			payment_entry.get(f"{bank_side_field}_account_currency"),
			selected_ledger.account_currency,
		)
		self.assertEqual(
			payment_entry.get(f"{bank_side_field}_account_type"),
			selected_ledger.account_type,
		)

	def test_invoice_payment_uses_transaction_bank_account(self):
		invoice = create_test_sales_invoice(outstanding=125)
		bank_transaction = create_test_bank_transaction(self.bank_account, deposit=125)

		payment_entry = create_payment_entry_for_invoice(
			invoice_doc=invoice,
			bank_transaction=bank_transaction,
			allocated_amount=125,
			payment_type="Receive",
			party_type="Customer",
			party=invoice.customer,
		)

		self.assert_transaction_bank_account(payment_entry, "paid_to")

	def test_invoice_supplier_payment_uses_transaction_bank_account(self):
		invoice = create_test_purchase_invoice(outstanding=90)
		bank_transaction = create_test_bank_transaction(self.bank_account, withdrawal=90)

		payment_entry = create_payment_entry_for_invoice(
			invoice_doc=invoice,
			bank_transaction=bank_transaction,
			allocated_amount=90,
			payment_type="Pay",
			party_type="Supplier",
			party=invoice.supplier,
		)

		self.assert_transaction_bank_account(payment_entry, "paid_from")

	def test_direct_customer_payment_uses_transaction_bank_account(self):
		bank_transaction = create_test_bank_transaction(self.bank_account, deposit=35)

		result = create_voucher_draft_from_transaction(
			bank_transaction.name,
			{
				"party_type": "Customer",
				"party": TEST_CUSTOMER,
				"reference_date": bank_transaction.date,
				"posting_date": bank_transaction.date,
			},
		)

		payment_entry = frappe.get_doc("Payment Entry", result["voucher_name"])
		self.assert_transaction_bank_account(payment_entry, "paid_to")

	def test_direct_supplier_payment_uses_transaction_bank_account(self):
		bank_transaction = create_test_bank_transaction(self.bank_account, withdrawal=40)

		result = create_voucher_draft_from_transaction(
			bank_transaction.name,
			{
				"party_type": "Supplier",
				"party": TEST_SUPPLIER,
				"reference_date": bank_transaction.date,
				"posting_date": bank_transaction.date,
			},
		)

		payment_entry = frappe.get_doc("Payment Entry", result["voucher_name"])
		self.assert_transaction_bank_account(payment_entry, "paid_from")

	def test_submitted_direct_payment_uses_transaction_bank_account(self):
		bank_transaction = create_test_bank_transaction(self.bank_account, deposit=45)

		result = create_voucher_from_transaction(
			bank_transaction.name,
			{
				"party_type": "Customer",
				"party": TEST_CUSTOMER,
				"reference_date": bank_transaction.date,
				"posting_date": bank_transaction.date,
			},
		)

		payment_entry = frappe.get_doc("Payment Entry", result["voucher_name"])
		self.assertEqual(payment_entry.docstatus, 1)
		self.assert_transaction_bank_account(payment_entry, "paid_to")
