from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from .models import User


@login_required
@require_http_methods(["GET", "POST"])
def approve_accounts(request):
    """Admin page to approve pending accounts"""
    # Solo el owner puede acceder
    if request.user.email.lower() != "cristhiancruzado2002@gmail.com":
        return HttpResponse("No autorizado", status=403)

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        action = request.POST.get("action")

        user = get_object_or_404(User, id=user_id)

        if action == "approve":
            user.is_active = True
            user.registration_status = User.RegistrationStatus.ACTIVE
            user.save()
        elif action == "reject":
            user.registration_status = User.RegistrationStatus.REJECTED
            user.save()

        return redirect("approve_accounts")

    pending_users = User.objects.filter(registration_status=User.RegistrationStatus.PENDING).order_by("-id")

    return render(request, "approval/pending_accounts.html", {
        "pending_users": pending_users
    })
