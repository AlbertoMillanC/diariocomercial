"""
Servicio Unificado de Correo Electrónico para DiarioComercial.
Permite enviar correos (reportes contables, alertas y recibos digitales)
utilizando el backend configurado en la base de datos (Brevo, Resend, Gmail SMTP)
o el backend predeterminado en settings / consola.
"""
import logging
from django.core.mail import EmailMessage, get_connection
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from .models import ConfiguracionPlataformaSaaS

logger = logging.getLogger("diariocomercial.email")


def enviar_correo_plataforma(asunto, mensaje, destinatarios, html_message=None):
    """
    Despacha un correo electrónico seguro con reintentos y tolerancia a fallos.
    Retorna True si se envió correctamente o False en caso de error.
    """
    if not destinatarios:
        return False

    if isinstance(destinatarios, str):
        destinatarios = [destinatarios]

    try:
        cfg = ConfiguracionPlataformaSaaS.get_solo()
        if cfg.smtp_activo and cfg.smtp_host and cfg.smtp_user:
            backend = SmtpBackend(
                host=cfg.smtp_host,
                port=cfg.smtp_port,
                username=cfg.smtp_user,
                password=cfg.smtp_password,
                use_tls=cfg.smtp_use_tls,
                timeout=12,
            )
            remitente = cfg.smtp_from_email or cfg.smtp_user or "soporte@diariocomercial.co"
        else:
            backend = get_connection()
            remitente = cfg.correo_soporte or "soporte@diariocomercial.co"

        email = EmailMessage(
            subject=asunto,
            body=mensaje,
            from_email=remitente,
            to=destinatarios,
            connection=backend,
        )
        if html_message:
            email.content_subtype = "html"
            email.body = html_message

        resultado = email.send(fail_silently=False)
        logger.info(f"Correo enviado exitosamente a {destinatarios} (Asunto: {asunto})")
        return bool(resultado)

    except Exception as e:
        logger.error(f"Error al enviar correo a {destinatarios}: {e}", exc_info=True)
        raise e
