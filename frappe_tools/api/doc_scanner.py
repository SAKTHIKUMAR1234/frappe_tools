import frappe
import time
local_signal_details = {}

@frappe.whitelist(allow_guest=True)
def ping_to_device(device_type, event, room):

    frappe.publish_realtime( event = room, message = {
        "device_type": device_type,
        "event": event
    })

@frappe.whitelist(allow_guest=True)
def add_scanner(room):
    ping_to_device('mobile', 'scanner_added', room)

@frappe.whitelist(allow_guest=True)
def add_signal_to_mobile(room, signal_data):
    signals = get_signals(room)
    signals.append(signal_data)


@frappe.whitelist(allow_guest=True)
def remove_scanner(room):
    ping_to_device('mobile', 'scanner_removed', room)

@frappe.whitelist(allow_guest=True)
def send_signal(room, signal_data, device):
    if device == 'web':
        add_to_signals(room, signal_data)
    elif device == 'mobile':
        frappe.publish_realtime( event = room, message = {
            "device_type": "mobile",
            "event": "signal",
            "data": signal_data
        } )

@frappe.whitelist(allow_guest=True)
def get_signal(room):
    """
    Long polling signal fetcher
    """
    timeout = 25 
    interval = 0.5     
    start_time = time.time()

    while True:
        signals = get_signals(room)

        if signals:
            data = signals.copy()
            clear_signals(room)
            return data
        if time.time() - start_time > timeout:
            return []

        time.sleep(interval)


def add_to_signals(room, signal_data):
    global local_signal_details
    if room not in local_signal_details:
        local_signal_details[room] = []
    local_signal_details[room].append(signal_data)

def get_signals(room):
    global local_signal_details
    if room not in local_signal_details:
        local_signal_details[room] = []
    return local_signal_details[room]

def clear_signals(room):
    global local_signal_details
    if room in local_signal_details:
        local_signal_details[room] = []