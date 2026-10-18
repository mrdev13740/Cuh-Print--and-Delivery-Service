
from pyscript import document, window, when
from pyodide.ffi import to_js
from js import fetch, Object
import js
from datetime import date


API_URL = "https://cuh-print-delivery-backend.onrender.com/api/orders"


def get_element(element_id):
    return document.getElementById(element_id)


@when("change", "#printFile")
def show_file_name(event):
    file_input = get_element("printFile")
    name_display = get_element("fileName")

    if file_input.files.length > 0:
        name_display.textContent = file_input.files.item(0).name
    else:
        name_display.textContent = "No file chosen"


@when("click", "#backButton")
def go_back(event):
    event.preventDefault()
    window.history.back()


@when("change", "#deliveryLocation")
def toggle_other_location(event):
    delivery_location = get_element("deliveryLocation").value
    location_group = get_element("otherLocationGroup")
    location_input = get_element("otherLocation")

    if delivery_location == "Other Location":
        location_group.hidden = False
        location_input.disabled = False
        location_input.required = True
    else:
        location_group.hidden = True
        location_input.disabled = True
        location_input.required = False
        location_input.value = ""


@when("submit", "#orderForm")
def submit_order(event):
    event.preventDefault()

    full_name = get_element("fullName").value.strip()
    contact = get_element("contact").value.strip()
    email = get_element("email").value.strip()
    delivery_location = get_element("deliveryLocation").value
    other_location = get_element("otherLocation").value.strip()
    paper_size = get_element("paperSize").value
    print_type = get_element("printType").value
    copies_text = get_element("copies").value.strip()
    fulfillment = get_element("fulfillment").value
    instructions = get_element("instructions").value.strip()
    file_input = get_element("printFile")
    preferred_date = get_element("preferredDate").value
    preferred_time = get_element("preferredTime").value

    if not preferred_date:
        window.alert("Please select your preferred date.")
        return

    if not preferred_time:
        window.alert("Please select your preferred time.")
        return

    try:
        requested_date = date.fromisoformat(preferred_date)
        if requested_date < date.today():
            window.alert("Your preferred date cannot be in the past.")
            get_element("preferredDate").focus()
            return
    except ValueError:
        window.alert("Please select a valid date.")
        return

    if not full_name:
        window.alert("Please enter your full name.")
        return

    if not contact:
        window.alert("Please enter your contact number.")
        return

    if not email:
        window.alert("Please enter your email address.")
        return

    if not delivery_location:
        window.alert("Please select a delivery location.")
        return

    if delivery_location == "Other Location" and not other_location:
        window.alert("Please specify your delivery location.")
        get_element("otherLocation").focus()
        return

    if file_input.files.length == 0:
        window.alert("Please choose a file to print.")
        return

    try:
        copies = int(copies_text)
    except (ValueError, TypeError):
        window.alert("Please enter a valid number of copies.")
        return

    if not 1 <= copies <= 500:
        window.alert("Copies must be between 1 and 500.")
        return

    if delivery_location == "Other Location":
        delivery_location = other_location

    selected_file = file_input.files.item(0)

    details = [
        ("Full Name", full_name),
        ("Contact Number", contact),
        ("Email", email),
        ("Delivery Location", delivery_location),
        ("Paper Size", paper_size),
        ("Print Type", print_type),
        ("Number of Copies", str(copies)),
        ("Receiving Method", fulfillment),
        ("Preferred Date", preferred_date),
        ("Preferred Time", preferred_time),
        ("File Name", selected_file.name),
        ("Instructions", instructions or "None"),
    ]

    summary_box = get_element("orderSummary")
    summary_box.innerHTML = ""

    for label, value in details:
        item = document.createElement("div")
        item.className = "summary-item"

        label_element = document.createElement("span")
        label_element.className = "summary-label"
        label_element.textContent = label

        value_element = document.createElement("span")
        value_element.className = "summary-value"
        value_element.textContent = str(value)

        item.appendChild(label_element)
        item.appendChild(value_element)
        summary_box.appendChild(item)

    get_element("confirmationModal").hidden = False


@when("click", "#cancelOrder")
def cancel_order(event):
    get_element("confirmationModal").hidden = True



@when("click", "#confirmOrder")
async def confirm_order(event):
    button = get_element("confirmOrder")
    result_message = get_element("resultMessage")
    success_modal = get_element("successModal")

    if button.disabled:
        return

    button.disabled = True
    button.textContent = "Submitting..."
    result_message.textContent = ""

    try:
        form_data = js.FormData.new()

        field_ids = [
            "fullName",
            "contact",
            "email",
            "printType",
            "paperSize",
            "copies",
            "fulfillment",
            "instructions",
            "preferredDate",
            "preferredTime",
        ]

        for field_id in field_ids:
            form_data.append(field_id, get_element(field_id).value)

        form_data.append(
            "deliveryLocation",
            get_element("deliveryLocation").value
        )
        form_data.append(
            "otherLocation",
            get_element("otherLocation").value
        )
        form_data.append(
            "printFile",
            get_element("printFile").files.item(0)
        )

        options = Object.new()
        options.method = "POST"
        options.body = form_data

        response = await fetch(API_URL, to_js(options))
        response_text = await response.text()

        # Convert the JSON response into a Python dictionary.
        import json
        data = json.loads(response_text)

        if not response.ok:
            result_message.textContent = data.get(
                "error", "Could not submit your order. Please try again."
            )
            return

        
        get_element("confirmationModal").hidden = True

        get_element("successOrderId").textContent = str(
            data.get("order_id", "N/A")
        )
        get_element("successOrderStatus").textContent = str(
            data.get("order_status", "Pending")
        )

        success_modal.hidden = False

        result_message.textContent = (
            "Order submitted successfully. Order ID: "
            + str(data.get("order_id", "N/A"))
        )

        get_element("orderForm").reset()
        get_element("fileName").textContent = "No file chosen"

        location_group = get_element("otherLocationGroup")
        location_group.hidden = True
        get_element("otherLocation").disabled = True
        get_element("otherLocation").required = False


    except Exception as error:
        window.console.error("Order submission failed:", error)
        result_message.textContent = (
            "Your order may have been submitted, but the page encountered "
            "an error. Please check your order status before trying again."
        )

    finally:
        button.disabled = False
        button.textContent = "Order Now"

    @when("click", "#successContinue")
    def close_success_modal(event):
        get_element("successModal").hidden = True

    @when("click", "#successContinue")
    def close_success_modal(event):
        get_element("successModal").hidden = True
