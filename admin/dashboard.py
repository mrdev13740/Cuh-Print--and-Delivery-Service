
from pyscript import document, window, when
from pyodide.http import pyfetch
import json
from pyodide.ffi import create_proxy
import asyncio

# Replace this with your actual CUH printing backend URL.
API_BASE = "https://cuh-print-delivery-backend.onrender.com/api"

TOKEN = window.sessionStorage.getItem("adminToken")
ORDERS = []
SELECTED = None

if not TOKEN:
    window.location.href = "admin-login.html"
else:
    el = document.getElementById
    el("dashboardView").hidden = False
    el("connectionBadge").textContent = "Connecting..."

def el(element_id):
    return document.getElementById(element_id)


def msg(element_id, text, error=True):
    el(element_id).textContent = text
    el(element_id).style.color = (
        "#a83232" if error else "#276749"
    )


def val(order, *keys, default="—"):
    for key in keys:
        if order.get(key) is not None:
            return order[key]
    return default


def fmt_money(amount):
    try:
        return "₱{:,.2f}".format(float(amount))
    except (TypeError, ValueError):
        return "—"


def headers():
    result = {"Content-Type": "application/json"}
    if TOKEN:
        result["Authorization"] = "Bearer " + TOKEN
    return result


async def api(path, method="GET", payload=None, auth=True):
    if "YOUR-CUH-BACKEND.example.com" in API_BASE:
        raise Exception(
            "Configure API_BASE in admin.py with your real backend URL."
        )

    request_headers = headers() if auth else {
        "Content-Type": "application/json"
    }

    options = {
        "method": method,
        "headers": request_headers
    }

    if payload is not None:
        options["body"] = json.dumps(payload)

    response = await pyfetch(
        API_BASE.rstrip("/") + path,
        **options
    )

    raw = await response.string()

    if not response.ok:
        raise Exception(
            "HTTP {}: {}".format(response.status, raw[:200])
        )

    return json.loads(raw) if raw.strip() else {}

@when("click", "#logoutButton")
def logout(event):
    global TOKEN, ORDERS, SELECTED

    TOKEN = None
    ORDERS = []
    SELECTED = None

    el("password").value = ""
    el("orderDetails").hidden = True
    el("connectionBadge").textContent = "Logged out"
    el("connectionBadge").className = "badge"

# =========================
# LOAD AND FILTER ORDERS
# =========================

@when("click", "#refreshButton")
async def refresh(event):
    await load_orders()


@when("input", "#searchInput")
def search(event):
    render_orders()


@when("change", "#statusFilter")
def filter_status(event):
    render_orders()


async def load_orders():
    global ORDERS

    try:
        result = await api("/admin/orders")

        if isinstance(result, list):
            ORDERS = result
        else:
            ORDERS = result.get("orders", [])

        render_orders()
        update_stats()

        el("connectionBadge").textContent = "Connected"
        el("connectionBadge").className = "badge connected"

        msg("ordersMessage", "Loaded {} order(s).".format(len(ORDERS)), False)

    except Exception as exc:
        msg("ordersMessage", str(exc))

        el("ordersTableBody").innerHTML = (
            '<tr><td colspan="7" class="empty">'
            'Could not load orders. Check your backend URL '
            'and API response format.</td></tr>'
        )


def render_orders():
    body = el("ordersTableBody")
    body.innerHTML = ""

    query = el("searchInput").value.strip().lower()
    selected_status = el("statusFilter").value

    filtered = []

    for order in ORDERS:
        order_id = str(val(order, "id", "order_id", default=""))
        name = str(val(
            order, "full_name", "customer_name", "name",
            default="Unknown"
        ))
        email = str(val(order, "email", default=""))
        status = str(val(order, "status", default="New Order"))

        if selected_status and status != selected_status:
            continue

        searchable = (order_id + " " + name + " " + email).lower()

        if query and query not in searchable:
            continue

        filtered.append((order, order_id, name, email, status))

    if not filtered:
        body.innerHTML = (
            '<tr><td colspan="7" class="empty">'
            'No orders match these filters.</td></tr>'
        )
        return

    for order, order_id, name, email, status in filtered:
        row = document.createElement("tr")

        values = [
            "#" + order_id,
            name + (" · " + email if email else ""),
            str(val(order, "print_type", default="—"))
            + " · "
            + str(val(order, "paper_size", default="—")),
            status,
            fmt_money(val(
                order, "quotation", "quote_amount", "final_price",
                default=None
            )),
            str(val(order, "created_at", "created", default="—"))[:16]
        ]

        for index, text in enumerate(values):
            cell = document.createElement("td")

            if index == 3:
                badge = document.createElement("span")
                badge.className = "pill"
                badge.textContent = text
                cell.appendChild(badge)
            else:
                cell.textContent = text

            row.appendChild(cell)

        action_cell = document.createElement("td")
        button = document.createElement("button")
        button.className = "review"
        button.type = "button"
        button.textContent = "Review"

        # Inside render_orders(), where each Review button is created:
        def handle_review(event, oid=order_id):
            open_order(oid)

        button.addEventListener(
            "click",
            create_proxy(handle_review)
        )

        action_cell.appendChild(button)
        row.appendChild(action_cell)
        body.appendChild(row)


# =========================
# DASHBOARD STATISTICS
# =========================

def update_stats():
    el("statTotal").textContent = str(len(ORDERS))

    needs_review = sum(
        1 for order in ORDERS
        if val(order, "status", default="New Order")
        in ("New Order", "Reviewing File")
    )

    in_progress = sum(
        1 for order in ORDERS
        if val(order, "status", default="")
        in (
            "Approved for Printing",
            "Printing",
            "Ready for Pickup/Delivery"
        )
    )

    sales = 0.0

    for order in ORDERS:
        if val(order, "status", default="") == "Completed":
            try:
                sales += float(val(
                    order,
                    "quotation",
                    "quote_amount",
                    "final_price",
                    default=0
                ) or 0)
            except (TypeError, ValueError):
                pass

    el("statReview").textContent = str(needs_review)
    el("statProgress").textContent = str(in_progress)
    el("statSales").textContent = fmt_money(sales)


# =========================
# VIEW AN ORDER
# =========================

def open_order(order_id):
    global SELECTED

    SELECTED = next(
        (
            order for order in ORDERS
            if str(val(order, "id", "order_id", default=""))
            == str(order_id)
        ),
        None
    )

    if not SELECTED:
        return

    order = SELECTED

    el("orderDetails").hidden = False
    el("detailTitle").textContent = (
        "Order #" + str(val(order, "id", "order_id"))
    )

    details = [
        ("Customer", val(order, "full_name", "customer_name", "name")),
        ("Contact", val(order, "contact", "contact_number", "phone")),
        ("Email", val(order, "email")),
        ("Paper size", val(order, "paper_size")),
        ("Print type", val(order, "print_type")),
        ("Copies", val(order, "copies")),
        ("Fulfillment", val(order, "fulfillment")),
        ("Delivery location", val(
            order, "delivery_location", "location"
        )),
        ("Instructions", val(order, "instructions", default="None"))
    ]

    grid = el("detailGrid")
    grid.innerHTML = ""

    for label_text, value_text in details:
        box = document.createElement("div")
        box.className = "detail"

        label = document.createElement("span")
        label.textContent = label_text

        strong = document.createElement("strong")
        strong.textContent = str(value_text)

        box.appendChild(label)
        box.appendChild(strong)
        grid.appendChild(box)

    file_url = val(
        order, "file_url", "uploaded_file_url", default=""
    )

    el("fileInfo").textContent = str(
        val(order, "file_name", "filename", default="No file name")
    )

    el("fileLink").hidden = not bool(file_url)

    if file_url:
        el("fileLink").href = str(file_url)
    else:
        el("fileLink").removeAttribute("href")

    quote = val(
        order, "quotation", "quote_amount", "final_price",
        default=""
    )

    el("quoteAmount").value = (
        "" if quote is None else str(quote)
    )

    el("statusSelect").value = str(
        val(order, "status", default="New Order")
    )

    el("adminNotes").value = str(
        val(order, "admin_notes", default="")
    )

    el("detailMessage").textContent = ""

    el("orderDetails").scrollIntoView({
        "behavior": "smooth",
        "block": "start"
    })


@when("click", "#closeDetailsButton")
def close_details(event):
    el("orderDetails").hidden = True


# =========================
# SAVE QUOTATION AND STATUS
# =========================

@when("click", "#saveOrderButton")
async def save_order(event):
    if not SELECTED:
        return

    order_id = val(SELECTED, "id", "order_id")
    quote_text = el("quoteAmount").value.strip()
    quote = None

    if quote_text:
        try:
            quote = float(quote_text)
            if quote < 0:
                raise ValueError()
        except ValueError:
            msg("detailMessage", "Enter a valid quotation amount.")
            return

    status = el("statusSelect").value

    if status == "Approved for Printing" and quote is None:
        msg(
            "detailMessage",
            "Set a quotation before approving an order for printing."
        )
        return

    payload = {
        "status": status,
        "quotation": quote,
        "admin_notes": el("adminNotes").value.strip()
    }

    try:
        await api(
            "/admin/orders/" + str(order_id),
            "PATCH",
            payload
        )

        msg("detailMessage", "Order updated.", False)
        await load_orders()
        open_order(order_id)

    except Exception as exc:
        msg("detailMessage", str(exc))



# =========================
# EMAIL CUSTOMER
# =========================

@when("click", "#emailCustomerButton")
async def email_customer(event):
    if not SELECTED:
        msg("detailMessage", "Please select an order first.")
        return

    order_id = val(SELECTED, "id", "order_id")
    email = val(SELECTED, "email", default="")
    quote_text = el("quoteAmount").value.strip()

    if not email or email == "—":
        msg("detailMessage", "This order has no customer email.")
        return

    if not quote_text:
        msg("detailMessage", "Enter a quotation amount first.")
        return

    try:
        quote = float(quote_text)

        if not (0 <= quote <= 99999999.99):
            raise ValueError()

    except (ValueError, TypeError):
        msg("detailMessage", "Enter a valid quotation amount.")
        return

    button = el("emailCustomerButton")
    button.disabled = True
    original_text = button.textContent
    button.textContent = "Sending..."

    try:
        # First, save the quotation in the database.
        await api(
            "/admin/orders/" + str(order_id),
            "PATCH",
            {"quotation": quote}
        )

        # Then, ask the backend to send the quotation email.
        await api(
            "/admin/orders/" + str(order_id) + "/send-quotation",
            "POST",
            {}
        )

        msg(
            "detailMessage",
            "Quotation email sent successfully.",
            False
        )

        await load_orders()
        open_order(order_id)

        msg(
            "detailMessage",
            "Quotation email sent successfully.",
            False
        )

    except Exception as exc:
        msg(
            "detailMessage",
            "Could not send quotation email: " + str(exc)
        )

    finally:
        button.disabled = False
        button.textContent = original_text



# =========================
# CUSTOM CONFIRMATION POPUP
# =========================

PENDING_ACTION = None


def show_confirm(action):
    global PENDING_ACTION

    if not SELECTED:
        msg("detailMessage", "Please select an order first.")
        return

    PENDING_ACTION = action

    if action == "cancel":
        el("confirmIcon").textContent = "!"
        el("confirmTitle").textContent = "Cancel this order?"
        el("confirmMessage").textContent = (
            "This order will be marked as cancelled. "
            "Its record will be retained."
        )
        el("confirmActionButton").textContent = "Cancel Order"
        el("confirmActionButton").className = (
            "btn cancel-confirm"
        )

    elif action == "delete":
        el("confirmIcon").textContent = "×"
        el("confirmTitle").textContent = "Delete this order?"
        el("confirmMessage").textContent = (
            "This will permanently delete the order. "
            "This action cannot be undone."
        )
        el("confirmActionButton").textContent = "Delete Permanently"
        el("confirmActionButton").className = (
            "btn delete-confirm"
        )

    el("confirmModal").hidden = False


def hide_confirm():
    global PENDING_ACTION

    el("confirmModal").hidden = True
    PENDING_ACTION = None


@when("click", "#cancelOrderButton")
def request_cancel(event):
    show_confirm("cancel")


@when("click", "#deleteOrderButton")
def request_delete(event):
    show_confirm("delete")


@when("click", "#confirmBackButton")
def dismiss_confirm(event):
    hide_confirm()


@when("click", "#confirmModal")
def dismiss_confirm_outside(event):
    # Close only when the dark overlay itself is clicked.
    if event.target == el("confirmModal"):
        hide_confirm()


@when("click", "#confirmActionButton")
async def confirm_pending_action(event):
    global PENDING_ACTION

    action = PENDING_ACTION

    if not SELECTED or action not in ("cancel", "delete"):
        hide_confirm()
        return

    order_id = val(SELECTED, "id", "order_id")

    if order_id is None:
        hide_confirm()
        msg("detailMessage", "Could not identify this order.")
        return

    button = el("confirmActionButton")
    button.disabled = True
    button.textContent = "Please wait..."

    try:
        if action == "cancel":
            await api(
                "/admin/orders/" + str(order_id) + "/cancel",
                "POST",
                {}
            )

            hide_confirm()
            msg("detailMessage", "Order cancelled.", False)

            await load_orders()
            open_order(order_id)

        elif action == "delete":
            await api(
                "/admin/orders/" + str(order_id),
                "DELETE"
            )

            hide_confirm()
            el("orderDetails").hidden = True

            await load_orders()
            msg("ordersMessage", "Order deleted.", False)

    except Exception as exc:
        hide_confirm()
        msg("detailMessage", str(exc))

    finally:
        button.disabled = False

if TOKEN:
    asyncio.create_task(load_orders())