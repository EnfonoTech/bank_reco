"""Progress of background jobs (statement import, auto reconcile).

State lives in the site's Redis cache for a couple of hours; the browser polls
``bank_reco.api.jobs.get_job_status``. Nothing here is a source of truth: the
real result of every job is in the database (Bank Transactions, action log).
"""

import frappe

KEY_PREFIX = "bank_reco_job::"
TTL_SECONDS = 2 * 60 * 60


def get_job(job_id):
	return frappe.cache().get_value(KEY_PREFIX + job_id)


def set_job(job_id, **values):
	current = get_job(job_id) or {}
	current.update(values)
	frappe.cache().set_value(KEY_PREFIX + job_id, current, expires_in_sec=TTL_SECONDS)
	return current


def new_job(kind, **values):
	job_id = frappe.generate_hash(length=12)
	set_job(
		job_id,
		kind=kind,
		status="queued",
		user=frappe.session.user,
		total=0,
		processed=0,
		**values,
	)
	return job_id
