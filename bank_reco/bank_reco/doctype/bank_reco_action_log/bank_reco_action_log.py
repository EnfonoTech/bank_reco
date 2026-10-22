# Copyright (c) 2026, Enfono Technologies and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class BankRecoActionLog(Document):
	"""One row per voucher that a Bank Reco action linked to a Bank Transaction.

	Rows are written by ``bank_reco.utils.action_log`` and are read-only in the
	desk. Undoing an action flips ``status`` to Undone; nothing is deleted.
	"""

	pass
