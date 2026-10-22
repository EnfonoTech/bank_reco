# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class BankRecoStatementBalance(Document):
	"""Closing balance of a bank account as printed on its statement, per date."""

	def validate(self):
		self.balance_date = getdate(self.balance_date)
		duplicate = frappe.db.exists(
			"Bank Reco Statement Balance",
			{
				"bank_account": self.bank_account,
				"balance_date": self.balance_date,
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("A statement balance for {0} on {1} already exists: {2}").format(
					self.bank_account, self.balance_date, duplicate
				)
			)
