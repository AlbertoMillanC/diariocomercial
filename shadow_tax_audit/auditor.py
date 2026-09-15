"""
Shadow Tax Auditing Engine — DiarioComercial
Auditoría Tributaria en la Sombra en tiempo real.

Recibe eventos emitidos asíncronamente desde un software de facturación o POS
principal, calcula la carga estimada de ICA (Tunja), detecta inconsistencias
arancelarias o de retención y sincroniza la contabilidad interna sin demorar
la atención en caja.
"""
from decimal import Decimal
from datetime import date
from django.contrib.auth.models import User
from django.utils import timezone
from registro.models import Establecimiento, ActividadCIIU, Venta, Retencion, Auditoria


class ShadowTaxAuditor:
    def __init__(self, establecimiento_id=None):
        self.establecimiento = (
            Establecimiento.objects.filter(pk=establecimiento_id).first()
            if establecimiento_id
            else Establecimiento.objects.first()
        )

    def procesar_evento_pos(self, evento):
        """
        Procesa una venta enviada desde el sistema POS principal.

        Estructura esperada de evento:
        {
            "ticket_pos": "POS-2026-0045",
            "valor": 85000,
            "fecha": "2026-09-15",
            "concepto": "Venta mostrador víveres",
            "tipo_cliente": "particular",  # 'particular' o 'empresa'
            "ciiu_codigo": "4711",         # Opcional, si el POS clasifica la línea
            "retencion_ica": 0,            # Retención practicada si aplica
            "operador": "carlos.ruiz"      # Usuario responsable
        }
        """
        if not self.establecimiento:
            return {"exito": False, "error": "No hay establecimiento configurado."}

        valor = Decimal(str(evento.get("valor", 0)))
        if valor <= 0:
            return {"exito": False, "error": "Valor de venta inválido."}

        ticket = evento.get("ticket_pos", "SIN-TICKET")
        fecha_str = evento.get("fecha")
        fecha = date.fromisoformat(fecha_str) if fecha_str else date.today()
        concepto = evento.get("concepto", f"Venta POS {ticket}")
        tipo_cliente = evento.get("tipo_cliente", "particular")
        username = evento.get("operador", "carlos.ruiz")

        usuario = User.objects.filter(username=username).first() or User.objects.first()

        # 1. Búsqueda y auditoría de Tarifa CIIU
        ciiu_codigo = evento.get("ciiu_codigo")
        actividad = None
        if ciiu_codigo:
            actividad = ActividadCIIU.objects.filter(
                establecimiento=self.establecimiento, codigo=ciiu_codigo
            ).first()

        if not actividad:
            actividad = ActividadCIIU.objects.filter(
                establecimiento=self.establecimiento
            ).first()

        # 2. Cálculo en la sombra del ICA estimado
        tarifa_mil = actividad.tarifa_x_mil if actividad else Decimal("6.0")
        ica_estimado = (valor * tarifa_mil) / Decimal("1000")

        # 3. Detección de banderas de auditoría (Shadow Audit Flags)
        alertas = []
        if valor >= Decimal("1000000"):
            alertas.append("VENTA_ALTO_VALOR: Supera $1.000.000 COP, requiere soporte documental.")
        if tipo_cliente == "empresa" and Decimal(str(evento.get("retencion_ica", 0))) <= 0:
            alertas.append("POSIBLE_OMISION_RETEICA: Venta a empresa sin retención informada.")

        # 4. Registro no bloqueante en DiarioComercial
        venta = Venta.objects.create(
            establecimiento=self.establecimiento,
            usuario=usuario,
            actividad=actividad,
            fecha=fecha,
            fecha_hora=timezone.now(),
            valor=valor,
            concepto=f"[{ticket}] {concepto}",
            tipo_cliente=tipo_cliente,
            ica_estimado=ica_estimado,
            estado="vigente",
        )

        ret_creada = None
        ret_val = Decimal(str(evento.get("retencion_ica", 0)))
        if tipo_cliente == "empresa" and ret_val > 0:
            ret_creada = Retencion.objects.create(
                establecimiento=self.establecimiento,
                usuario=usuario,
                venta=venta,
                fecha=fecha,
                tipo="ica",
                valor=ret_val,
                tercero=evento.get("tercero", "Cliente Empresa POS"),
                estado="vigente",
            )

        # 5. Trazabilidad de Auditoría en la sombra
        Auditoria.objects.create(
            usuario=usuario,
            entidad_afectada="shadow_audit",
            id_registro=venta.pk,
            accion="pos_shadow_ingest",
            valor_nuevo=f"ticket={ticket}|valor={valor}|ica={ica_estimado}|alertas={len(alertas)}",
            motivo=f"Ingesta en segundo plano desde POS principal: {', '.join(alertas) if alertas else 'Limpia'}",
        )

        return {
            "exito": True,
            "venta_id": venta.pk,
            "ticket": ticket,
            "valor": float(valor),
            "ica_estimado": float(ica_estimado),
            "tarifa_aplicada": f"{tarifa_mil} x mil",
            "alertas_auditoria": alertas,
            "retencion_registrada": float(ret_val) if ret_creada else 0.0,
            "timestamp": timezone.now().isoformat(),
        }
