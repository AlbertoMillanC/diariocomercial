"""Vistas de ingesta webhook para Shadow Tax Auditing."""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .auditor import ShadowTaxAuditor

# Token estático de desarrollo para la API del POS
SHADOW_API_KEY = "shadow-tunja-pos-key-2026"


@csrf_exempt
def webhook_pos_ingest(request):
    """
    Endpoint HTTP POST para recibir ventas en tiempo real desde el software de caja.
    Header opcional de seguridad: X-Shadow-Token
    """
    if request.method != "POST":
        return JsonResponse({"error": "Método no permitido. Use POST."}, status=405)

    token = request.headers.get("X-Shadow-Token") or request.GET.get("token")
    if token != SHADOW_API_KEY:
        return JsonResponse({"error": "No autorizado. Token de POS inválido."}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception as e:
        return JsonResponse({"error": f"JSON malformado: {e}"}, status=400)

    auditor = ShadowTaxAuditor()
    resultado = auditor.procesar_evento_pos(data)

    if not resultado.get("exito"):
        return JsonResponse(resultado, status=400)

    return JsonResponse(resultado, status=201)
