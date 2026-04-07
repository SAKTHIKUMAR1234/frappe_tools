__version__ = "0.0.1"

# Install the AI Bot permission patch as soon as this module is imported.
# Wrapped in try/except so a broken patch can never prevent the app from
# loading (which would brick the whole bench).
try:
	from frappe_tools.permissions import install_permission_patch

	install_permission_patch()
except Exception:
	import traceback

	traceback.print_exc()
