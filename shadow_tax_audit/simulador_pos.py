"""
Simulador de Terminal POS con Emisión de Shadow Tax Audit
Permite probar la captura de ventas en tiempo real sin modificar el flujo de caja.

Uso:
  python shadow_tax_audit/simulador_pos.py
"""
import os
import sys
import time

# Configurar Django para ejecución directa
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "diariocomercial.settings")

import django
django.setup()

from shadow_tax_audit.auditor import ShadowTaxAuditor

VENTAS_SIMULADAS = [
    {
        "ticket_pos": "TICKET-101",
        "valor": 45000,
        "concepto": "Canasta familiar y abarrotes",
        "tipo_cliente": "particular",
        "ciiu_codigo": "4711",
        "retencion_ica": 0,
        "operador": "carlos.ruiz",
    },
    {
        "ticket_pos": "TICKET-102",
        "valor": 1250000,
        "concepto": "Pedido institucional para cafetería",
        "tipo_cliente": "empresa",
        "ciiu_codigo": "4711",
        "retencion_ica": 7500,
        "tercero": "Inversiones Boyacá SAS (NIT 901234567)",
        "operador": "maria.gomez",
    },
    {
        "ticket_pos": "TICKET-103",
        "valor": 18000,
        "concepto": "Gaseosas y pasabocas",
        "tipo_cliente": "particular",
        "ciiu_codigo": "4711",
        "retencion_ica": 0,
        "operador": "carlos.ruiz",
    },
]


def correr_simulacion():
    print("=" * 65)
    print("  SIMULADOR DE CAJA REGISTRADORA / POS (SHADOW TAX AUDITING)")
    print("=" * 65)
    print("Emitiendo transacciones desde el POS principal a DiarioComercial...\n")

    auditor = ShadowTaxAuditor()

    for idx, v in enumerate(VENTAS_SIMULADAS, 1):
        print(f"[{idx}/3] Emitiendo ticket {v['ticket_pos']} por ${v['valor']:,} COP...")
        time.sleep(0.5)

        res = auditor.procesar_evento_pos(v)

        if res.get("exito"):
            print(f"   -> Sincronizado en DiarioComercial (ID Venta: #{res['venta_id']})")
            print(f"   -> Tarifa aplicada: {res['tarifa_aplicada']} | ICA calculado: ${res['ica_estimado']:,.2f} COP")
            if res.get("alertas_auditoria"):
                for a in res["alertas_auditoria"]:
                    print(f"   [ALERTA DE AUDITORÍA]: {a}")
            print()
        else:
            print(f"   [ERROR]: {res.get('error')}\n")

    print("=" * 65)
    print("Simulación finalizada con éxito. Registros visibles en el Historial.")
    print("=" * 65)


if __name__ == "__main__":
    correr_simulacion()
