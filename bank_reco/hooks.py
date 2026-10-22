app_name = "bank_reco"
app_title = "Bank Reco"
app_publisher = "Enfono Technologies"
app_description = "Bank reconciliation for ERPNext: bulk matching, rules, undo history, transfers, statement balances"
app_email = ""
app_license = "gpl-3.0"
# required_apps = []

add_to_apps_screen = [
    {
        "name": "bank_rec",
        "title": "Bank Rec",
        "route": "/bank-rec",
        "has_permission": "bank_reco.api.permission.has_bank_rec_permission",
    }
]

website_route_rules = [
    {"from_route": "/bank-rec/<path:app_path>", "to_route": "bank_rec"},
    {"from_route": "/bank-rec", "to_route": "bank_rec"},
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/bank_reco/css/bank_reco.css"
# app_include_js = "/assets/bank_reco/js/bank_reco.js"

# include js, css files in header of web template
# web_include_css = "/assets/bank_reco/css/bank_reco.css"
# web_include_js = "/assets/bank_reco/js/bank_reco.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "bank_reco/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Journal Entry": "public/js/journal_entry_reference_query.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "bank_reco/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "bank_reco.utils.jinja_methods",
# 	"filters": "bank_reco.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "bank_reco.install.before_install"
after_install = "bank_reco.setup.after_install"
after_migrate = "bank_reco.setup.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "bank_reco.uninstall.before_uninstall"
# after_uninstall = "bank_reco.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "bank_reco.utils.before_app_install"
# after_app_install = "bank_reco.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "bank_reco.utils.before_app_uninstall"
# after_app_uninstall = "bank_reco.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "bank_reco.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
    "Bank Transaction": "bank_reco.bank_reco.overrides.bank_transaction.ExtendedBankTransaction",
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"bank_reco.tasks.all"
# 	],
# 	"daily": [
# 		"bank_reco.tasks.daily"
# 	],
# 	"hourly": [
# 		"bank_reco.tasks.hourly"
# 	],
# 	"weekly": [
# 		"bank_reco.tasks.weekly"
# 	],
# 	"monthly": [
# 		"bank_reco.tasks.monthly"
# 	],
# }

# Testing
# -------

before_tests = "bank_reco.tests.test_utils.before_tests"

# Overriding Methods
# ------------------------------

override_whitelisted_methods = {
    "erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool.update_bank_transaction": (
        "bank_reco.bank_reco.doctype."
        "advance_bank_reconciliation_tool.advance_bank_reconciliation_tool.update_bank_transaction"
    )
}

# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "bank_reco.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["bank_reco.utils.before_request"]
# after_request = ["bank_reco.utils.after_request"]

# Job Events
# ----------
# before_job = ["bank_reco.utils.before_job"]
# after_job = ["bank_reco.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"bank_reco.auth.validate"
# ]

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

bank_reconciliation_doctypes = [
    "Unpaid Sales Invoice",
    "Unpaid Purchase Invoice"
]

get_matching_queries = [
    "bank_reco.bank_reco.doctype.advance_bank_reconciliation_tool.advance_bank_reconciliation_tool.get_matching_queries"
]

required_apps = ["erpnext"]

scheduler_events = {
	"hourly": [
		"bank_reco.bank_reco.doctype.abr_bank_rule.abr_bank_rule.scheduled_rule_suggestions",
	],
}

# Extend ERPNext's accounting dimension doctypes so that custom dimensions
# (e.g., Location, Department) are automatically added to ABR Bank Rules.
accounting_dimension_doctypes = [
    "ABR Bank Rule",
]

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Bank"],
            ["fieldname", "in", ["bank_statement_date_format"]],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "=", "Bank Transaction"],
            ["fieldname", "in", ["custom_particulars", "custom_code"]],
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "in", ["Journal Entry", "Payment Entry"]],
            ["fieldname", "=", "abr_bank_rule"],
        ],
    },
]

company_data_to_be_ignored = ["ABR Bank Rule"]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True
