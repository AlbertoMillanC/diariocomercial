# DiarioComercial

Proyecto Integrado I - Desarrollo de Aplicaciones  
IU Digital de Antioquia

MVP en Django para que un comerciante de Tunja registre ventas, compras y retenciones, vea el consolidado y le mande el resumen al contador por correo.

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

El correo del consolidado por ahora sale en la consola (no hay SMTP real en el MVP).
