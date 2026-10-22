import os
import uuid
import secrets
import hashlib
from pathlib import Path
from functools import wraps
from datetime import datetime, timedelta, timezone

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename
from supabase import create_client
import base64
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

# Admin authentication configuration
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
AUTH_SECRET_KEY = os.environ.get("AUTH_SECRET_KEY", "")

TOKEN_MAX_AGE = 60 * 60 * 8  # 8 hours


def get_token_serializer():
    if not AUTH_SECRET_KEY:
        raise RuntimeError("AUTH_SECRET_KEY is not configured")

    return URLSafeTimedSerializer(
        AUTH_SECRET_KEY,
        salt="admin-auth"
    )

MAX_FILE_SIZE = 10 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE + 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png"}

ALLOWED_PRINT_TYPES = {"bw", "color"}
ALLOWED_PAPER_SIZES = {"short", "long"}
ALLOWED_FULFILLMENT = {"pickup", "delivery"}


def get_db_connection():
    database_url = os.environ.get("SUPABASE_DB_URL")

    if not database_url:
        raise RuntimeError("SUPABASE_DB_URL is not configured")

    return psycopg2.connect(
        database_url,
        connect_timeout=10,
        sslmode="require"
    )


def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")

    if not url or not key:
        raise RuntimeError("Supabase storage configuration is missing")

    return create_client(url, key)


def create_quotation_response_token(cursor, order_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    cursor.execute(
        """
        INSERT INTO public.quotation_responses (
            order_id, token_hash, expires_at
        )
        VALUES (%s, %s, %s)
        """,
        (order_id, token_hash, expires_at)
    )

    return token




@app.after_request
def add_cors_headers(response):
    allowed_origins = {
        origin.strip().rstrip("/")
        for origin in os.environ.get(
            "FRONTEND_ORIGINS",
            "https://mrdev13740.github.io,https://4wtc75.csb.app"
        ).split(",")
        if origin.strip()
    }

    origin = request.headers.get("Origin", "").rstrip("/")

    if origin and origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization"
        )

    return response



@app.route("/")
def home():
    return jsonify({
        "message": "CUH Print & Delivery backend is running!"
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200

def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return "", 204
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Admin authentication required."}), 401

        token = auth_header.split(" ", 1)[1].strip()

        if not token:
            return jsonify({"error": "Invalid authentication token."}), 401

        try:
            serializer = get_token_serializer()
            token_data = serializer.loads(token, max_age=TOKEN_MAX_AGE)

            if token_data.get("username") != ADMIN_USERNAME:
                return jsonify({"error": "Invalid authentication token."}), 401
        except SignatureExpired:
            return jsonify({"error": "Session expired. Please log in again."}), 401
        except (BadSignature, RuntimeError):
            return jsonify({"error": "Invalid authentication token."}), 401

        return function(*args, **kwargs)

    return wrapper


@app.route("/api/admin/login", methods=["POST", "OPTIONS"])
def admin_login():
    if request.method == "OPTIONS":
        return "", 204

    if not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH or not AUTH_SECRET_KEY:
        app.logger.error("Admin authentication environment variables are missing.")
        return jsonify({"error": "Admin login is not configured."}), 503

    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify({"error": "Invalid username or password."}), 401

    if (
        username != ADMIN_USERNAME
        or not check_password_hash(ADMIN_PASSWORD_HASH, password)
    ):
        return jsonify({"error": "Invalid username or password."}), 401

    serializer = get_token_serializer()
    token = serializer.dumps({
        "username": ADMIN_USERNAME,
        "issued_at": datetime.now(timezone.utc).isoformat()
    })

    return jsonify({
        "token": token,
        "message": "Login successful."
    }), 200

@app.route("/api/orders", methods=["POST", "OPTIONS"])
def create_order():
    if request.method == "OPTIONS":
        return "", 204

    uploaded_file = request.files.get("printFile")

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"error": "Please attach a print file."}), 400

    original_name = secure_filename(uploaded_file.filename)

    if not original_name:
        return jsonify({"error": "Invalid file name."}), 400

    extension = Path(original_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "File type is not supported."}), 400

    file_bytes = uploaded_file.read(MAX_FILE_SIZE + 1)

    if not file_bytes:
        return jsonify({"error": "The selected file is empty."}), 400

    if len(file_bytes) > MAX_FILE_SIZE:
        return jsonify({"error": "File size must not exceed 10 MB."}), 400

    full_name = request.form.get("fullName", "").strip()
    contact = request.form.get("contact", "").strip()
    email = request.form.get("email", "").strip()
    print_type = request.form.get("printType", "").strip()
    paper_size = request.form.get("paperSize", "").strip()
    fulfillment = request.form.get("fulfillment", "").strip()
    preferred_date = request.form.get("preferredDate", "").strip()
    preferred_time = request.form.get("preferredTime", "").strip()
    delivery_location = request.form.get("deliveryLocation", "").strip()
    other_location = request.form.get("otherLocation", "").strip()
    instructions = request.form.get("instructions", "").strip()

    try:
        copies = int(request.form.get("copies", ""))
    except (ValueError, TypeError):
        return jsonify({"error": "Enter a valid number of copies."}), 400

    if not full_name or len(full_name) > 150:
        return jsonify({"error": "Enter a valid full name."}), 400

    if not contact or len(contact) > 50:
        return jsonify({"error": "Enter a valid contact number."}), 400

    if not email or len(email) > 254 or "@" not in email:
        return jsonify({"error": "Enter a valid email address."}), 400

    if print_type not in ALLOWED_PRINT_TYPES:
        return jsonify({"error": "Invalid printing type."}), 400

    if paper_size not in ALLOWED_PAPER_SIZES:
        return jsonify({"error": "Invalid paper size."}), 400

    if fulfillment not in ALLOWED_FULFILLMENT:
        return jsonify({"error": "Invalid receiving method."}), 400

    if not 1 <= copies <= 500:
        return jsonify({"error": "Copies must be between 1 and 500."}), 400

    if not delivery_location:
        return jsonify({"error": "Select a delivery location."}), 400

    if delivery_location == "Other Location":
        if not other_location:
            return jsonify({"error": "Specify your delivery location."}), 400
        delivery_location = other_location

    if len(instructions) > 5000:
        return jsonify({"error": "Instructions are too long."}), 400

    order_id = None
    storage_path = f"orders/{uuid.uuid4().hex}/{original_name}"
    storage = None
    file_uploaded = False

    try:
        storage = get_supabase()

        content_types = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }


        storage.storage.from_("print-orders").upload(
    path=storage_path,
    file=file_bytes,
    file_options={
        "content-type": content_types[extension],
        "upsert": "false"
    }
)
        file_uploaded = True

        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                                        """
                    INSERT INTO public.orders (
                        full_name, contact, email, print_type,
                        paper_size, copies, fulfillment,
                        preferred_date, preferred_time,
                        delivery_location, instructions,
                        file_name, storage_path, status
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, 'Pending'
                    )
                    RETURNING id
                    """,
                                        (
                        full_name,
                        contact,
                        email,
                        print_type,
                        paper_size,
                        copies,
                        fulfillment,
                        preferred_date,
                        preferred_time,
                        delivery_location,
                        instructions or None,
                        original_name,
                        storage_path
                    )
                )
                order_id = cursor.fetchone()[0]

        return jsonify({
            "status": "success",
            "message": "Your order was submitted successfully.",
            "order_id": order_id,
            "order_status": "Pending"
        }), 201

    except Exception:
        app.logger.exception("Order submission failed")

        if file_uploaded and storage is not None:
            try:
                storage.storage.from_("print-orders").remove([storage_path])
            except Exception:
                app.logger.exception("Failed to clean up uploaded file")

        return jsonify({
            "error": "We could not submit your order. Please try again."
        }), 500


# =========================
# ADMIN ORDER MANAGEMENT
# =========================


ALLOWED_ORDER_STATUSES = {
    "Pending",
    "New Order",
    "Reviewing File",
    "Quotation Sent",
    "Awaiting Customer Approval",
    "Approved for Printing",
    "Printing",
    "Ready for Pickup/Delivery",
    "Completed",
    "Cancelled",
    "Declined",
}



def get_order_or_none(cursor, order_id):
    cursor.execute(
        "SELECT * FROM public.orders WHERE id = %s",
        (order_id,)
    )
    return cursor.fetchone()


@app.route("/api/admin/orders", methods=["GET", "OPTIONS"])
@admin_required
def list_orders():
    if request.method == "OPTIONS":
        return "", 204

    try:
        with get_db_connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT *
                    FROM public.orders
                    ORDER BY created_at DESC
                """)
                orders = cursor.fetchall()

        storage = get_supabase()

        for order in orders:
            order["id"] = int(order["id"])

            if order.get("quotation") is not None:
                order["quotation"] = float(order["quotation"])

            for field in ("preferred_date", "preferred_time", "created_at"):
                if order.get(field) is not None:
                    order[field] = order[field].isoformat()

            order["file_url"] = ""

            if order.get("storage_path"):
                try:
                    signed = storage.storage.from_("print-orders").create_signed_url(
                        order["storage_path"], 3600
                    )
                    order["file_url"] = (
                        signed.get("signedURL")
                        or signed.get("signedUrl")
                        or ""
                    )
                except Exception:
                    app.logger.exception("Could not create signed file URL")

        return jsonify({"orders": orders}), 200

    except Exception:
        app.logger.exception("Failed to retrieve orders")
        return jsonify({"error": "Could not retrieve orders."}), 500


@app.route("/api/admin/orders/<int:order_id>", methods=["PATCH", "OPTIONS"])
@admin_required
def update_order(order_id):
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request body."}), 400

    allowed_fields = {"quotation", "status", "admin_notes"}

    if not data or not set(data).issubset(allowed_fields):
        return jsonify({"error": "Invalid order update fields."}), 400

    updates = {}

    if "quotation" in data:
        quotation = data["quotation"]

        if quotation is not None:
            try:
                quotation = float(quotation)
                if not (0 <= quotation <= 99999999.99):
                    raise ValueError()
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid quotation amount."}), 400

        updates["quotation"] = quotation

    if "status" in data:
        status = data["status"]

        if not isinstance(status, str) or status not in ALLOWED_ORDER_STATUSES:
            return jsonify({"error": "Invalid order status."}), 400

        updates["status"] = status

    if "admin_notes" in data:
        notes = data["admin_notes"]

        if not isinstance(notes, str) or len(notes) > 5000:
            return jsonify({"error": "Admin notes must be 5000 characters or fewer."}), 400

        updates["admin_notes"] = notes.strip() or None

    try:
        with get_db_connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                if not get_order_or_none(cursor, order_id):
                    return jsonify({"error": "Order not found."}), 404

                assignments = ", ".join(
                    f"{field} = %s" for field in updates
                )
                values = list(updates.values()) + [order_id]

                cursor.execute(
                    f"UPDATE public.orders SET {assignments} "
                    "WHERE id = %s RETURNING id, status, quotation, admin_notes",
                    values
                )
                updated = cursor.fetchone()

        if updated["quotation"] is not None:
            updated["quotation"] = float(updated["quotation"])

        return jsonify({"message": "Order updated.", "order": updated}), 200

    except Exception:
        app.logger.exception("Failed to update order")
        return jsonify({"error": "Could not update order."}), 500


@app.route("/api/admin/orders/<int:order_id>/cancel", methods=["POST", "OPTIONS"])
@admin_required
def cancel_order(order_id):
    if request.method == "OPTIONS":
        return "", 204

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.orders SET status = 'Cancelled' "
                    "WHERE id = %s RETURNING id",
                    (order_id,)
                )

                if cursor.fetchone() is None:
                    return jsonify({"error": "Order not found."}), 404

        return jsonify({"message": "Order cancelled."}), 200

    except Exception:
        app.logger.exception("Failed to cancel order")
        return jsonify({"error": "Could not cancel order."}), 500


@app.route("/api/admin/orders/<int:order_id>", methods=["DELETE", "OPTIONS"])
@admin_required
def delete_order(order_id):
    if request.method == "OPTIONS":
        return "", 204

    try:
        with get_db_connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "DELETE FROM public.orders WHERE id = %s "
                    "RETURNING id, storage_path",
                    (order_id,)
                )
                deleted = cursor.fetchone()

        if not deleted:
            return jsonify({"error": "Order not found."}), 404

        storage_path = deleted.get("storage_path")

        if storage_path:
            try:
                get_supabase().storage.from_("print-orders").remove(
                    [storage_path]
                )
            except Exception:
                app.logger.exception(
                    "Order deleted but uploaded file cleanup failed"
                )

        return jsonify({"message": "Order deleted."}), 200

    except Exception:
        app.logger.exception("Failed to delete order")
        return jsonify({"error": "Could not delete order."}), 500

@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"error": "The request or file is too large."}), 413


@app.route(
    "/api/admin/orders/<int:order_id>/send-quotation",
    methods=["POST", "OPTIONS"]
)
@admin_required
def send_quotation_email(order_id):
    if request.method == "OPTIONS":
        return "", 204

    
    gmail_address = os.environ.get("GMAIL_ADDRESS", "").strip()
    gmail_client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    gmail_client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    gmail_refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    
    if not all([
        gmail_address,
        gmail_client_id,
        gmail_client_secret,
        gmail_refresh_token
    ]):
        return jsonify({
            "error": "Gmail API environment variables are not configured."
        }), 503


    try:
        with get_db_connection() as connection:
            with connection.cursor(
                cursor_factory=RealDictCursor
            ) as cursor:
                cursor.execute(
                    """
                    SELECT id, full_name, contact, email,
                           print_type, paper_size, copies,
                           fulfillment, delivery_location,
                           instructions, file_name, storage_path,
                           quotation, status, created_at,
                           preferred_date, preferred_time,
                           admin_notes
                    FROM public.orders
                    WHERE id = %s
                    """
                    ,
                    (order_id,)
                )
                order = cursor.fetchone()

        if not order:
            return jsonify({"error": "Order not found."}), 404

        if order["quotation"] is None:
            return jsonify({
                "error": "Save a quotation amount before sending the email."
            }), 400

        
        if order["status"] == "Cancelled":
            return jsonify({
                "error": "Cannot send a quotation for a cancelled order."
            }), 400
        
        quotation = float(order["quotation"])
        
        
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE public.quotation_responses
                    SET expires_at = NOW()
                    WHERE order_id = %s
                      AND decision IS NULL
                    """,
                    (order_id,)
                )
        
                response_token = create_quotation_response_token(
                    cursor,
                    order_id
                )



        quotation = float(order["quotation"])

        customer_name = order["full_name"]
        customer_email = order["email"]
        admin_notes = order.get("admin_notes") or ""

        print_type = (
            "Black and White"
            if order["print_type"] == "bw"
            else "Color"
        )

        paper_size = (
            "Short"
            if order["paper_size"] == "short"
            else "Long"
        )

        contact = escape(str(order.get("contact") or "Not provided"))
        fulfillment = "Delivery" if order.get("fulfillment") == "delivery" else "Pickup"
        
        delivery_location = escape(
            str(order.get("delivery_location") or "Not provided")
        )
        instructions = escape(
            str(order.get("instructions") or "None")
        )
        file_name = escape(
            str(order.get("file_name") or "Not provided")
        )
        
        preferred_date = (
            order["preferred_date"].strftime("%B %d, %Y")
            if order.get("preferred_date")
            else "Not specified"
        )
        
        preferred_time = (
            order["preferred_time"].strftime("%I:%M %p").lstrip("0")
            if order.get("preferred_time")
            else "Not specified"
        )
        
        safe_name = escape(str(customer_name))
        safe_notes = escape(str(admin_notes or "")).replace("\n", "<br>")

        if not safe_notes.strip():
            safe_notes = "No additional notes."

        
        backend_url = "https://cuh-print-delivery-backend.onrender.com"
        
        approval_url = (
            f"{backend_url}/quotation-response/{response_token}"
            "?decision=Approved"
        )
        
        decline_url = (
            f"{backend_url}/quotation-response/{response_token}"
            "?decision=Declined"
        )


        email_params = {
            "from": f"Cuh Print & Delivery Service <{gmail_address}>",
            "to": [customer_email],
            "subject": f"Quotation for Your Print Order #{order_id}",
            "html": f"""
            <!DOCTYPE html>
            <html lang="en">
            <body style="margin:0; padding:0; background-color:#f3eee6;
                         font-family:Arial,Helvetica,sans-serif; color:#263247;">

              <table role="presentation" width="100%" cellspacing="0"
                     cellpadding="0" border="0"
                     style="background-color:#f3eee6; padding:28px 12px;">
                <tr>
                  <td align="center">

                    <table role="presentation" width="100%" cellspacing="0"
                           cellpadding="0" border="0"
                           style="max-width:600px; background-color:#ffffff;
                                  border-radius:12px; overflow:hidden;">

                      <!-- Business header -->
                      <tr>
                        <td align="center"
                            style="background-color:#11213a; padding:30px 20px;">
                          <img
                                src="https://raw.githubusercontent.com/mrdev13740/cuh-print-delivery-service/main/images/logo.png"
                                alt="Cuh Print &amp; Delivery Service"
                                width="100"
                                style="display:block; width:100px; height:auto; margin:0 auto 12px;"
                            >
                          <h1 style="margin:10px 0 8px; color:#ffffff;
                                     font-size:23px; line-height:1.4;">
                            Cuh Print &amp; Delivery Service
                          </h1>
                          <p style="margin:0; color:#f5ebdd; font-size:14px;">
                            You Click, We Print, We Deliver!
                          </p>
                        </td>
                      </tr>

                      <!-- Email content -->
                      <tr>
                        <td style="padding:28px 24px;">

                          <h2 style="margin:0 0 18px; color:#11213a;
                                     font-size:23px;">
                            Your Print Order Quotation
                          </h2>

                          <p style="font-size:15px; line-height:1.7;">
                            Hello {safe_name},
                          </p>

                          <p style="font-size:15px; line-height:1.7;">
                            We've reviewed your print order. Please review
                            the quotation and order details below.
                          </p>

                          <!-- Order details -->
                          <table role="presentation" width="100%"
                                 cellspacing="0" cellpadding="0" border="0"
                                 style="border:1px solid #e5e0d8;
                                        border-radius:8px;">
                            <tr>
                              <td colspan="2"
                                  style="background-color:#f5ebdd;
                                         padding:14px 16px;
                                         color:#11213a; font-weight:bold;">
                                ORDER DETAILS
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Order ID
                              </td>
                              <td align="right"
                                  style="padding:12px 16px;
                                         font-weight:bold; color:#11213a;">
                                #{order_id}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Printing Type
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {escape(str(print_type))}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Paper Size
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {escape(str(paper_size))}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Copies
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {int(order["copies"])}
                              </td>
                            </tr>

                            
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Contact
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {contact}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Fulfillment
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {fulfillment}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Delivery Location
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {delivery_location}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                File Name
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {file_name}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Preferred Date
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {escape(str(preferred_date))}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Preferred Time
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {escape(str(preferred_time))}
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:12px 16px; color:#666666;">
                                Instructions
                              </td>
                              <td align="right"
                                  style="padding:12px 16px; color:#263247;">
                                {instructions}
                              </td>
                            </tr>

                            
                          </table>

                          <!-- Quotation total -->
                          <table role="presentation" width="100%"
                                 cellspacing="0" cellpadding="0" border="0"
                                 style="margin-top:18px; background-color:#11213a;
                                        border-radius:8px;">
                            <tr>
                              <td style="padding:20px;">
                                <p style="margin:0 0 8px; color:#f5ebdd;
                                          font-size:13px; letter-spacing:1px;">
                                  TOTAL QUOTATION
                                </p>
                                <p style="margin:0; color:#ff9b2f;
                                          font-size:30px; font-weight:bold;">
                                  &#8369;{quotation:,.2f}
                                </p>
                              </td>
                            </tr>
                          </table>

                          <!-- Additional notes -->
                          <h3 style="margin:24px 0 8px; color:#11213a;">
                            Additional Notes
                          </h3>
                          <p style="margin:0; font-size:14px; line-height:1.7;
                                    color:#555555;">
                            {safe_notes}
                          </p>

                         
                        <!-- Approval instructions -->
                        <table role="presentation" width="100%"
                               cellspacing="0" cellpadding="0" border="0"
                               style="margin-top:24px; border:1px solid #ff9b2f;
                                      border-radius:8px;">
                          <tr>
                            <td style="padding:18px;">
                              <h3 style="margin:0 0 10px; color:#11213a;">
                                What happens next?
                              </h3>
                        
                              <p style="margin:0 0 18px; font-size:14px; line-height:1.7;">
                                Please review the quotation and select your response below.
                              </p>
                        
                              <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                                <tr>
                                  <td style="padding:0 10px 10px 0;">
                                    <a href="{approval_url}"
                                       style="display:inline-block; background-color:#ff9b2f;
                                              color:#11213a; padding:12px 18px;
                                              border-radius:6px; text-decoration:none;
                                              font-weight:bold;">
                                      Approve Quotation
                                    </a>
                                  </td>
                                  <td style="padding:0 0 10px 0;">
                                    <a href="{decline_url}"
                                       style="display:inline-block; background-color:#11213a;
                                              color:#ffffff; padding:12px 18px;
                                              border-radius:6px; text-decoration:none;
                                              font-weight:bold;">
                                      Decline Quotation
                                    </a>
                                  </td>
                                </tr>
                              </table>
                        
                              <p style="margin:8px 0 10px; font-size:13px;
                                        line-height:1.7; color:#666;">
                                You will be asked to confirm your decision before it is recorded.
                                These links expire after 7 days.
                              </p>
                        
                              <p style="margin:0; font-size:14px; line-height:1.7; color:#11213a;">
                                <strong>
                                  Printing will begin only after you approve the quotation.
                                </strong>
                              </p>
                            </td>
                          </tr>
                        </table>


                          <p style="margin:24px 0 0; font-size:14px;
                                    line-height:1.7;">
                            Thank you for choosing CUH Print &amp; Delivery
                            Express!
                          </p>

                        </td>
                      </tr>

                      <!-- Footer -->
                      <tr>
                        <td align="center"
                            style="background-color:#11213a; padding:18px 20px;">
                          <p style="margin:0; color:#f5ebdd; font-size:12px;
                                    line-height:1.7;">
                            CUH Print &amp; Delivery Express<br>
                            Thank you for your order.
                          </p>
                        </td>
                      </tr>

                    </table>
                  </td>
                </tr>
              </table>

            </body>
            </html>
            """
        }

        
        
        # Build the email using the existing HTML quotation template.
        message = MIMEMultipart("alternative")
        message["From"] = email_params["from"]
        message["To"] = ", ".join(email_params["to"])
        message["Subject"] = email_params["subject"]

        message.attach(MIMEText(
            "Please open this email in an HTML-compatible email application "
            "to view your quotation and response buttons.",
            "plain",
            "utf-8"
        ))

        message.attach(MIMEText(
            email_params["html"],
            "html",
            "utf-8"
        ))

        # Use Gmail API OAuth credentials instead of SMTP.
        credentials = Credentials(
            token=None,
            refresh_token=gmail_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=gmail_client_id,
            client_secret=gmail_client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.send"]
        )

        gmail_service = build(
            "gmail",
            "v1",
            credentials=credentials,
            cache_discovery=False
        )

        encoded_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode("utf-8")

        
        gmail_result = gmail_service.users().messages().send(
            userId="me",
            body={"raw": encoded_message}
        ).execute()
        
        app.logger.info(
            "Gmail accepted quotation email. Order ID: %s, Message ID: %s",
            order_id,
            gmail_result.get("id")
        )

        return jsonify({
            "message": "Quotation email sent successfully.",
            "order_id": order_id
        }), 200

        
    except Exception:
        app.logger.exception("Failed to send quotation email")
        return jsonify({
            "error": "Failed to send quotation email."
        }), 500



@app.route("/quotation-response/<token>", methods=["GET", "POST"])
def quotation_response(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    if request.method == "GET":
        decision = request.args.get("decision", "")

        if decision not in ("Approved", "Declined"):
            return "Invalid quotation response link.", 400

        with get_db_connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT
                        qr.id,
                        qr.expires_at,
                        qr.decision,
                        o.status
                    FROM public.quotation_responses AS qr
                    JOIN public.orders AS o ON o.id = qr.order_id
                    WHERE qr.token_hash = %s
                    """,
                    (token_hash,)
                )
                response_record = cursor.fetchone()

        if not response_record:
            return "This quotation response link is invalid.", 404

        if response_record["expires_at"] <= datetime.now(timezone.utc):
            return "This quotation response link has expired.", 410

        if response_record["decision"] is not None:
            return "A response has already been recorded for this quotation.", 409

        if response_record["status"] == "Cancelled":
            return "This order has been cancelled.", 409

        action_text = (
            "approve the quotation"
            if decision == "Approved"
            else "decline the quotation"
        )

        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Confirm Quotation Response</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background: #F5EBDD;
                    color: #11213a;
                    display: grid;
                    place-items: center;
                    min-height: 100vh;
                    margin: 0;
                    padding: 20px;
                    box-sizing: border-box;
                }}
                main {{
                    background: white;
                    width: 100%;
                    max-width: 480px;
                    padding: 28px;
                    border-radius: 14px;
                    box-sizing: border-box;
                    text-align: center;
                }}
                button {{
                    background: #ff9b2f;
                    color: #11213a;
                    border: 0;
                    border-radius: 8px;
                    padding: 12px 20px;
                    font-size: 16px;
                    font-weight: bold;
                    cursor: pointer;
                }}
                a {{ color: #11213a; }}
            </style>
        </head>
        <body>
            <main>
                <h1>CUH Print &amp; Delivery Express</h1>
                <h2>Confirm Your Decision</h2>
                <p>Are you sure you want to {action_text}?</p>
                <form method="POST">
                    <input type="hidden" name="decision" value="{decision}">
                    <button type="submit">Confirm</button>
                </form>
                <p><a href="javascript:history.back()">Go back</a></p>
            </main>
        </body>
        </html>
        """

    # POST: the customer explicitly confirms their decision.
    decision = request.form.get("decision", "")

    if decision not in ("Approved", "Declined"):
        return "Invalid quotation response.", 400

    with get_db_connection() as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    qr.id,
                    qr.order_id,
                    qr.expires_at,
                    qr.decision,
                    o.status
                FROM public.quotation_responses AS qr
                JOIN public.orders AS o ON o.id = qr.order_id
                WHERE qr.token_hash = %s
                FOR UPDATE OF qr
                """,
                (token_hash,)
            )
            response_record = cursor.fetchone()

            if not response_record:
                return "This quotation response link is invalid.", 404

            if response_record["expires_at"] <= datetime.now(timezone.utc):
                return "This quotation response link has expired.", 410

            if response_record["decision"] is not None:
                return "A response has already been recorded.", 409

            if response_record["status"] == "Cancelled":
                return "This order has been cancelled.", 409

            new_status = (
                "Approved for Printing"
                if decision == "Approved"
                else "Declined"
            )

            cursor.execute(
                """
                UPDATE public.quotation_responses
                SET decision = %s, responded_at = NOW()
                WHERE id = %s
                """,
                (decision, response_record["id"])
            )

            cursor.execute(
                """
                UPDATE public.orders
                SET status = %s
                WHERE id = %s
                """,
                (new_status, response_record["order_id"])
            )

    result_text = (
        "Thank you! Your quotation has been approved."
        if decision == "Approved"
        else "Your quotation has been declined."
    )

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Quotation Response Recorded</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #F5EBDD;
                color: #11213a;
                display: grid;
                place-items: center;
                min-height: 100vh;
                margin: 0;
                padding: 20px;
                box-sizing: border-box;
            }}
            main {{
                background: white;
                width: 100%;
                max-width: 480px;
                padding: 28px;
                border-radius: 14px;
                box-sizing: border-box;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <main>
            <h1>CUH Print &amp; Delivery Express</h1>
            <h2>Response Recorded</h2>
            <p>{result_text}</p>
            <p>You may close this page now.</p>
        </main>
    </body>
    </html>
    """

# Add this route to your existing Flask backend file.
# Your existing backend already imports jsonify, request, get_db_connection,
# and RealDictCursor. Do not replace your whole backend with this snippet.

@app.route("/api/track-order", methods=["POST", "OPTIONS"])
def track_order():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    raw_order_id = str(data.get("order_id", "")).strip()

    if not raw_order_id.isdigit() or int(raw_order_id) < 1:
        return jsonify({"error": "Please enter a valid order ID."}), 400

    order_id = int(raw_order_id)

    try:
        with get_db_connection() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, status, created_at, fulfillment,
                           preferred_date, preferred_time
                    FROM public.orders
                    WHERE id = %s
                    """,
                    (order_id,)
                )
                order = cursor.fetchone()

        if not order:
            return jsonify({
                "error": "We couldn't find an order with that ID. Please check it and try again."
            }), 404

        def safe_iso(value):
            return value.isoformat() if value is not None else None

        return jsonify({
            "order_id": order["id"],
            "status": order.get("status") or "Pending",
            "created_at": safe_iso(order.get("created_at")),
            "fulfillment": order.get("fulfillment"),
            "preferred_date": safe_iso(order.get("preferred_date")),
            "preferred_time": safe_iso(order.get("preferred_time"))
        }), 200

    except Exception:
        app.logger.exception("Order tracking lookup failed")
        return jsonify({
            "error": "We couldn't check your order right now. Please try again later."
        }), 500
