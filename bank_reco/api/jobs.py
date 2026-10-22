import frappe
from frappe import _

from bank_reco.api.permission import require_bank_rec_permission
from bank_reco.utils.jobs import get_job


@frappe.whitelist()
def get_job_status(job_id):
	"""Progress and result of a Bank Reco background job (own jobs only)."""
	require_bank_rec_permission()
	job = get_job(job_id)
	if not job:
		frappe.throw(_("This job is unknown or has expired."))
	if job.get("user") != frappe.session.user and "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError(_("This job belongs to another user."))
	return job
