"""Progress of background jobs (statement import, auto reconcile).

State lives in the site's Redis cache for a couple of hours; the browser polls
``bank_reco.api.jobs.get_job_status``. Nothing here is a source of truth: the
real result of every job is in the database (Bank Transactions, action log).
"""

import json

import frappe

KEY_PREFIX = "bank_reco_job::"
TTL_SECONDS = 2 * 60 * 60


# Plain Redis get/set on purpose: ``cache().get_value`` remembers a miss in the
# process-local request cache, so a job that reads before it writes would never
# see its own later writes.
def _key(job_id):
	return frappe.cache().make_key(KEY_PREFIX + job_id)


def get_job(job_id):
	raw = frappe.cache().get(_key(job_id))
	if not raw:
		return None
	return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def set_job(job_id, **values):
	current = get_job(job_id) or {}
	current.update(values)
	frappe.cache().set(_key(job_id), json.dumps(current, default=str), ex=TTL_SECONDS)
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
