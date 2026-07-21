app_name = "frappe_tools"
app_title = "Frappe Tools"
app_publisher = "sakthi123msd@gmail.com"
app_description = "Utilities App For Frappe Apps"
app_email = "sakthi123msd@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "frappe_tools",
# 		"logo": "/assets/frappe_tools/logo.png",
# 		"title": "Frappe Tools",
# 		"route": "/frappe_tools",
# 		"has_permission": "frappe_tools.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/frappe_tools/css/frappe_tools.css"
# app_include_js = "/assets/frappe_tools/js/frappe_tools.js"

# include js, css files in header of web template
# web_include_css = "/assets/frappe_tools/css/frappe_tools.css"
# web_include_js = "/assets/frappe_tools/js/frappe_tools.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "frappe_tools/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "frappe_tools/public/icons.svg"

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
# 	"methods": "frappe_tools.utils.jinja_methods",
# 	"filters": "frappe_tools.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "frappe_tools.install.before_install"
after_install = "frappe_tools.setup.ai_bot_permissions.setup_ai_bot_permissions"

# Uninstallation
# ------------

# before_uninstall = "frappe_tools.uninstall.before_uninstall"
# after_uninstall = "frappe_tools.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "frappe_tools.utils.before_app_install"
# after_app_install = "frappe_tools.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "frappe_tools.utils.before_app_uninstall"
# after_app_uninstall = "frappe_tools.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "frappe_tools.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"*": "frappe_tools.permissions.ai_bot_query_conditions",
}

has_permission = {
	"*": "frappe_tools.permissions.ai_bot_has_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"frappe_tools.tasks.all"
# 	],
# 	"daily": [
# 		"frappe_tools.tasks.daily"
# 	],
# 	"hourly": [
# 		"frappe_tools.tasks.hourly"
# 	],
# 	"weekly": [
# 		"frappe_tools.tasks.weekly"
# 	],
# 	"monthly": [
# 		"frappe_tools.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "frappe_tools.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "frappe_tools.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "frappe_tools.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["frappe_tools.utils.before_request"]
# after_request = ["frappe_tools.utils.after_request"]

# Job Events
# ----------
# before_job = ["frappe_tools.utils.before_job"]
# after_job = ["frappe_tools.utils.after_job"]

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
# 	"frappe_tools.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

ignore_links_on_delete = ['Scanned Document Detail']
app_include_js = ["tools_plugin.bundle.js"]

doc_events = {
	# Hard write guard: the AI Bot role may write ONLY the allowlisted DocTypes
	# (AI Bot Page + AI Bot Settings' write list). Every other write is refused
	# at the document lifecycle — catches paths the permission hook can't (e.g.
	# Frappe's built-in User self-edit, which would otherwise allow role
	# self-assignment / privilege escalation).
	"*": {
		"before_insert": "frappe_tools.permissions.ai_bot_guard_write",
		"before_save": "frappe_tools.permissions.ai_bot_guard_write",
		"on_trash": "frappe_tools.permissions.ai_bot_guard_write",
	}
}

scheduler_events = {
    "cron": {
        "*/30 * * * *": [
           "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.poll_update_status_processing_data_share",
           "frappe_tools.frappe_tools.doctype.custom_data_builder.custom_data_builder.delete_old_previews"
        ],
        "0 * * * *" : [
            "frappe_tools.frappe_tools.doctype.scanned_document_detail.scanned_document_detail.remove_old_deletable_documents"
        ]
    }
}

# 2026-05-29 outage: AI Bot Custom DocPerm rows triggered Frappe's
# wholesale-override rule (any Custom DocPerm row on a doctype makes Frappe
# ignore that doctype's standard DocPerm entirely), suppressing other roles'
# write/create/delete and leaving users with read-only access.
# 2026-06-07 fix: setup_doctype_permissions now mirrors every standard DocPerm
# row into Custom DocPerm BEFORE adding AI Bot (see
# ai_bot_permissions._mirror_standard_into_custom), exactly as Frappe's own
# setup_custom_perms does — so seeding is additive and can't revoke any role's
# access. Verified: frappe_tools.setup.verify_perm_safety.verify reported ZERO
# regressions across 2491 granted perms on a real-data copy. Auto-reseed
# re-enabled. NOTE: on a live site, run `bench restart` after migrate — perms
# are cached per gunicorn worker, so the DB change isn't visible until workers
# recycle.
after_migrate = ["frappe_tools.setup.ai_bot_permissions.setup_ai_bot_permissions"]

fixtures =[
        {
            'dt' : 'Role',
            'filters' : [['name', 'in', ['Scanner User', 'AI Bot']]]
        }
    ]