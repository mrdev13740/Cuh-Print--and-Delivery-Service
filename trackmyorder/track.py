
import asyncio
import json
from datetime import datetime

from pyodide.ffi import create_proxy
from pyodide.http import pyfetch
from pyscript import document

API_BASE_URL = "https://cuh-print-delivery-backend.onrender.com"

STATUS_STEPS = [
    "Pending",
    "New Order",
    "Reviewing File",
    "Quotation Sent",
    "Awaiting Customer Approval",
    "Approved for Printing",
    "Printing",
    "Ready for Pickup/Delivery",
    "Completed",
]

FINAL_STATUSES = ["Cancelled", "Declined"]

# Get the HTML elements.
form = document.querySelector("#trackingForm")
order_id_input = document.querySelector("#orderId")
track_button = document.querySelector("#trackButton")
message = document.querySelector("#message")
result = document.querySelector("#result")
status_badge = document.querySelector("#statusBadge")
result_order_id = document.querySelector("#resultOrderId")
created_at = document.querySelector("#createdAt")
fulfillment = document.querySelector("#fulfillment")
preferred_date_row = document.querySelector("#preferredDateRow")
preferred_date = document.querySelector("#preferredDate")
preferred_time_row = document.querySelector("#preferredTimeRow")
preferred_time = document.querySelector("#preferredTime")
status_timeline = document.querySelector("#statusTimeline")
progress_note = document.querySelector("#progressNote")


def show_message(text, kind="info"):
    message.textContent = text
    message.className = f"message {kind}"
    message.hidden = False


def hide_message():
    message.hidden = True
    message.textContent = ""


def format_date(value):
    if not value:
        return "Not provided"

    try:
        date_part = str(value).split("T")[0]
        parsed = datetime.strptime(date_part, "%Y-%m-%d")
        return parsed.strftime("%B %d, %Y")
    except (ValueError, TypeError):
        return str(value)


def format_time(value):
    if not value:
        return "Not provided"

    try:
        time_part = str(value).split("T")[-1]
        parsed = datetime.strptime(time_part[:8], "%H:%M:%S")
        return parsed.strftime("%I:%M %p").lstrip("0")
    except (ValueError, TypeError):
        try:
            parsed = datetime.strptime(str(value)[:5], "%H:%M")
            return parsed.strftime("%I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            return str(value)


def render_timeline(status):
    status_timeline.replaceChildren()
    status_badge.className = "status-badge"

    status = str(status or "Pending")

    if status == "Completed":
        progress_note.textContent = "This order is marked as completed."
    elif status in FINAL_STATUSES:
        progress_note.textContent = f"This order is marked as {status.lower()}."
    else:
        progress_note.textContent = (
            "Your order's latest recorded status is shown below."
        )

    if status in FINAL_STATUSES:
        item = document.createElement("li")
        item.className = "is-final"

        dot = document.createElement("span")
        dot.className = "step-dot"
        dot.textContent = "!"

        label = document.createElement("span")
        label.textContent = status

        item.append(dot, label)
        status_timeline.appendChild(item)
        return

    if status not in STATUS_STEPS:
        item = document.createElement("li")
        item.className = "is-current"

        dot = document.createElement("span")
        dot.className = "step-dot"
        dot.textContent = "•"

        label = document.createElement("span")
        label.textContent = status

        item.append(dot, label)
        status_timeline.appendChild(item)

        progress_note.textContent = (
            "Your order has a status not listed in the standard progress steps."
        )
        return

    active_index = STATUS_STEPS.index(status)

    for index, step in enumerate(STATUS_STEPS):
        item = document.createElement("li")

        if index < active_index:
            item.className = "is-done"
        elif index == active_index:
            item.className = "is-current"

        dot = document.createElement("span")
        dot.className = "step-dot"

        if index < active_index:
            dot.textContent = "✓"
        elif index == active_index:
            dot.textContent = "•"

        label = document.createElement("span")
        label.textContent = step

        item.append(dot, label)
        status_timeline.appendChild(item)


def display_order(order):
    result_order_id.textContent = f"#{order.get('order_id', order.get('id', ''))}"

    status = order.get("status") or "Pending"
    status_badge.textContent = status

    created_at.textContent = format_date(order.get("created_at"))

    fulfillment_value = str(order.get("fulfillment") or "")
    fulfillment.textContent = (
        fulfillment_value.capitalize()
        if fulfillment_value
        else "Not provided"
    )

    preferred_date_value = order.get("preferred_date")
    preferred_date_row.hidden = not bool(preferred_date_value)
    preferred_date.textContent = format_date(preferred_date_value)

    preferred_time_value = order.get("preferred_time")
    preferred_time_row.hidden = not bool(preferred_time_value)
    preferred_time.textContent = format_time(preferred_time_value)

    render_timeline(status)
    result.hidden = False



async def track_order():
    print("DEBUG: track_order started")
    show_message("Tracking function started.", "info")
    

    hide_message()
    result.hidden = True

    order_id = order_id_input.value.strip()

    if not order_id.isdigit() or int(order_id) < 1:
        show_message("Please enter a valid numeric Order ID.", "error")
        order_id_input.focus()
        return

    track_button.disabled = True
    track_button.textContent = "Checking..."

    show_message("Step 1/3: Sending request to the server...", "info")

    try:
        import json

        response = await pyfetch(
            url=f"{API_BASE_URL}/api/track-order",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"order_id": int(order_id)}),
        )

        show_message(
            f"Step 2/3: Server responded with HTTP {response.status}. Reading result...",
            "info"
        )

        try:
            data = await response.json()
        except Exception:
            data = {}

        if response.status < 200 or response.status >= 300:
            show_message(
                f"Tracking failed (HTTP {response.status}): "
                + str(data.get("error", "Check the backend server logs.")),
                "error"
            )
            return

        display_order(data)
        print("DEBUG: Order displayed successfully")
        print("DEBUG: Result hidden =", result.hidden)

        hide_message()

    except Exception as error:
        print("TRACKING ERROR:", repr(error))
        show_message(
            "The request failed before a response was received. "
            "Open F12 > Console and check the red error message.",
            "error"
        )

    finally:
        track_button.disabled = False
        track_button.textContent = "Track Order"

def on_submit(event):
    event.preventDefault()
    print("DEBUG: Form submission prevented")
    asyncio.ensure_future(track_order())


# Check that the required HTML elements exist before registering the handler.
if form is None:
    print("ERROR: Could not find the form with ID trackingForm.")
elif order_id_input is None or track_button is None:
    print("ERROR: Could not find the Order ID input or tracking button.")
else:
    form.addEventListener("submit", create_proxy(on_submit))
    print("Track My Order Python code loaded successfully.")
