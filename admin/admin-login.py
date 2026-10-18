from pyscript import document, window
from pyodide.http import pyfetch
from pyodide.ffi import create_proxy
import asyncio
import json

API_BASE = "https://cuh-print-delivery-backend.onrender.com/api"


async def login():
    username = document.getElementById("username").value.strip()
    password = document.getElementById("password").value
    message = document.getElementById("loginMessage")
    button = document.getElementById("loginButton")

    if not username or not password:
        message.textContent = "Enter your username and password."
        return

    button.disabled = True
    button.textContent = "Logging in..."
    message.textContent = ""

    try:
        response = await pyfetch(
            API_BASE + "/admin/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps({
                "username": username,
                "password": password
            })
        )

        raw = await response.string()
        result = json.loads(raw) if raw.strip() else {}

        if not response.ok:
            raise Exception(result.get("error", "Login failed."))

        token = result.get("token")

        if not token:
            raise Exception("The backend did not return an authentication token.")

        window.sessionStorage.setItem("adminToken", token)
        document.getElementById("password").value = ""

        # Redirect to the dashboard in the same admin folder.
        window.location.href = "dashboard.html"

    except Exception as exc:
        print("LOGIN ERROR:", str(exc))
        message.textContent = str(exc)

    finally:
        button.disabled = False
        button.textContent = "Log In"


def handle_submit(event):
    event.preventDefault()
    asyncio.create_task(login())


# Keep the callback proxy alive so Pyodide can use it.
submit_proxy = create_proxy(handle_submit)

form = document.getElementById("loginForm")

if form is not None:
    form.addEventListener("submit", submit_proxy)
    print("LOGIN SUBMIT LISTENER REGISTERED")
else:
    print("ERROR: LOGIN FORM NOT FOUND")