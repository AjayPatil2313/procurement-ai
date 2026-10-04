from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from apps.accounts.models import User


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        
        user = authenticate(request, email=email, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Welcome back, {user.first_name or user.email}!")
            return redirect("dashboard")
        else:
            messages.error(request, "Invalid email or password.")

    return render(request, "auth/login.html")


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("login")
