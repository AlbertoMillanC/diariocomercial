"""
Middleware de Control de Acceso y Aislamiento de Comercios Suspendidos.
Garantiza que la información contable permanezca 100% íntegra y protegida,
pero restringe la operativa de ventas y compras a cuentas en mora o suspendidas,
redirigiéndolas a la pantalla amigable de reactivación y pago Bre-B.
"""
from django.shortcuts import redirect
from django.urls import reverse


class TenantSuspensionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.is_superuser and not request.user.is_staff:
            perfil = getattr(request.user, "perfil", None)
            if perfil and perfil.establecimiento and perfil.establecimiento.estado == "suspendido":
                # Rutas permitidas para usuarios suspendidos
                allowed_paths = [
                    reverse("cuenta_suspendida"),
                    reverse("logout"),
                    "/static/",
                ]
                current_path = request.path
                if not any(current_path.startswith(p) for p in allowed_paths):
                    return redirect("cuenta_suspendida")

        response = self.get_response(request)
        return response
