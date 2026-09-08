# DiarioComercial

Proyecto Integrado I - Desarrollo de Aplicaciones  
IU Digital de Antioquia

Aplicación web para que un comerciante de Tunja registre ventas, compras y retenciones, vea el consolidado del periodo y le mande el resumen al contador por correo. No liquida el ICA oficial ni se conecta con la DIAN.

**Estudiante:** Carlos Alberto Millán Castaño  
**Docente:** Jose Nelson Palacio Londoño

## Cómo correrlo

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py cargar_demo
python manage.py runserver
```

Luego abrir http://127.0.0.1:8000/login/

- Propietaria: `maria.gomez` / `tunja2026`
- Dependiente: `carlos.ruiz` / `tunja2026`

El correo del consolidado sale en la consola (no hay SMTP real). Si el envío falla, se puede descargar el PDF o el CSV.

## Qué cubre

| ID | Requisito | Dónde está |
|----|-----------|------------|
| RF01 | Login por rol; usuario inactivo no entra | Login + Configuración |
| RF02 | Venta con fecha, valor, concepto y observación | Ventas |
| RF03 | Compra con proveedor | Compras |
| RF04 | Retención (en venta a empresa o suelta) | Ventas / Retenciones |
| RF05 | Historial con filtro de fechas y tipo | Historial |
| RF06 | Editar con auditoría | Historial (propietario) |
| RF07 | Anular sin borrar, con motivo | Historial (propietario) |
| RF08 | Consolidado de vigentes | Inicio e Historial |
| RF09 | Correo del contador | Configuración |
| RF10 | Envío del consolidado con traza | Enviar (PDF/CSV de respaldo) |

El propietario crea y activa/desactiva dependientes. Toda edición o anulación queda en Auditoría.

## Pruebas

```
python manage.py test registro
```
