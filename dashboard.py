"""
Dashboard web para gestión de cobros ePagos.
"""
import os
import uuid
from calendar import month_abbr
from datetime import date, datetime, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from db import Cliente, Cuenta, Cobro, get_session, init_db
from epagos import EpagosClient, EpagosError

load_dotenv()

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_SECRET = os.getenv("DASHBOARD_SECRET", "cambiar-en-produccion-32chars!!")
_ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
_ADMIN_PASS = os.getenv("DASHBOARD_PASSWORD", "admin")
_signer = URLSafeTimedSerializer(_SECRET)
_SESSION_MAX_AGE = 60 * 60 * 8  # 8 horas


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _set_session(response, username: str):
    token = _signer.dumps(username)
    response.set_cookie("session", token, httponly=True, max_age=_SESSION_MAX_AGE)


def _get_current_user(request: Request) -> Optional[str]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        return _signer.loads(token, max_age=_SESSION_MAX_AGE)
    except BadSignature:
        return None


def _require_auth(request: Request):
    if not _get_current_user(request):
        raise _redirect_login()


def _redirect_login():
    return RedirectResponse("/login", status_code=302)


def _epagos() -> EpagosClient:
    return EpagosClient()


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if _get_current_user(request):
        return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == _ADMIN_USER and password == _ADMIN_PASS:
        resp = RedirectResponse("/dashboard", status_code=302)
        _set_session(resp, username)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "Usuario o contraseña incorrectos"})


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ---------------------------------------------------------------------------
# Dashboard principal
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_home(request: Request):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        total_clientes = db.query(func.count(Cliente.id)).scalar()
        total_cuentas  = db.query(func.count(Cuenta.id)).scalar()
        total_cobros   = db.query(func.count(Cobro.id)).scalar()
        monto_total    = db.query(func.sum(Cobro.importe)).filter(
            Cobro.estado == "acreditado"
        ).scalar() or 0.0
        ultimos_cobros = (
            db.query(Cobro)
            .join(Cliente)
            .order_by(Cobro.creado_en.desc())
            .limit(10)
            .all()
        )
        return templates.TemplateResponse("dashboard/index.html", {
            "request": request,
            "total_clientes": total_clientes,
            "total_cuentas":  total_cuentas,
            "total_cobros":   total_cobros,
            "monto_total":    monto_total,
            "ultimos_cobros": ultimos_cobros,
        })


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------

@router.get("/dashboard/clientes", response_class=HTMLResponse)
def listar_clientes(request: Request):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        clientes = db.query(Cliente).order_by(Cliente.apellido).all()
        return templates.TemplateResponse("dashboard/clientes.html", {
            "request": request,
            "clientes": clientes,
        })


@router.post("/dashboard/clientes")
def crear_cliente(
    request: Request,
    nombre:               str = Form(...),
    apellido:             str = Form(...),
    email:                str = Form(...),
    dni:                  int = Form(...),
    cuit:                 int = Form(...),
    identificador_cliente: str = Form(...),
):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        if len(identificador_cliente) < 6:
            return templates.TemplateResponse("dashboard/clientes.html", {
                "request": request,
                "clientes": db.query(Cliente).all(),
                "error": "El identificador debe tener al menos 6 caracteres",
            })
        cliente = Cliente(
            identificador_cliente=identificador_cliente,
            nombre=nombre,
            apellido=apellido,
            email=email,
            dni=dni,
            cuit=cuit,
        )
        db.add(cliente)
        db.commit()
    return RedirectResponse("/dashboard/clientes", status_code=302)


@router.get("/dashboard/clientes/{cliente_id}", response_class=HTMLResponse)
def detalle_cliente(request: Request, cliente_id: int):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return RedirectResponse("/dashboard/clientes", status_code=302)
        cobros = (
            db.query(Cobro)
            .filter(Cobro.cliente_id == cliente_id)
            .order_by(Cobro.creado_en.desc())
            .all()
        )
        return templates.TemplateResponse("dashboard/cliente_detalle.html", {
            "request": request,
            "cliente": cliente,
            "cobros": cobros,
            "hoy": date.today().isoformat(),
        })


@router.post("/dashboard/clientes/{cliente_id}/sync_cbu")
def sincronizar_cbu(request: Request, cliente_id: int):
    """Trae las cuentas CBU del cliente desde ePagos y las guarda localmente."""
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return RedirectResponse("/dashboard/clientes", status_code=302)
        try:
            ep = _epagos()
            cuentas_api = ep.obtener_cuentas_cliente(cliente.identificador_cliente)
            nuevas = 0
            for c in cuentas_api:
                id_cuenta = str(c.get("identificador_cuenta", ""))
                if not id_cuenta:
                    continue
                existe = db.query(Cuenta).filter(
                    Cuenta.identificador_cuenta == id_cuenta
                ).first()
                if not existe:
                    db.add(Cuenta(
                        cliente_id=cliente_id,
                        identificador_cuenta=id_cuenta,
                        alias=c.get("alias") or c.get("descripcion") or "",
                        cbu=c.get("cbu") or c.get("numero_cuenta") or "",
                    ))
                    nuevas += 1
            db.commit()
        except EpagosError:
            pass
    return RedirectResponse(f"/dashboard/clientes/{cliente_id}", status_code=302)


@router.post("/dashboard/clientes/{cliente_id}/eliminar")
def eliminar_cliente(request: Request, cliente_id: int):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if cliente:
            db.delete(cliente)
            db.commit()
    return RedirectResponse("/dashboard/clientes", status_code=302)


# ---------------------------------------------------------------------------
# Cobros
# ---------------------------------------------------------------------------

@router.get("/dashboard/cobros", response_class=HTMLResponse)
def historial_cobros(request: Request):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        cobros = (
            db.query(Cobro)
            .join(Cliente)
            .order_by(Cobro.creado_en.desc())
            .limit(200)
            .all()
        )
        return templates.TemplateResponse("dashboard/cobros.html", {
            "request": request,
            "cobros": cobros,
        })


@router.post("/dashboard/cobros/nuevo")
def nuevo_cobro(
    request: Request,
    cliente_id:   int   = Form(...),
    cuenta_id:    int   = Form(...),
    importe:      float = Form(...),
    descripcion:  str   = Form("Cobro recurrente"),
    tipo:         str   = Form("inmediato"),   # inmediato | programado
    fecha_cobro:  Optional[str] = Form(None),
):
    if not _get_current_user(request):
        return _redirect_login()

    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        cuenta  = db.get(Cuenta, cuenta_id)
        if not cliente or not cuenta:
            return RedirectResponse(f"/dashboard/clientes/{cliente_id}", status_code=302)

        numero_op = f"OP-{uuid.uuid4().hex[:12].upper()}"
        cobro = Cobro(
            cliente_id=cliente_id,
            cuenta_id=cuenta_id,
            numero_operacion=numero_op,
            importe=importe,
            descripcion=descripcion,
            tipo=tipo,
            fecha_cobro=date.fromisoformat(fecha_cobro) if fecha_cobro else None,
            estado="enviado",
        )
        db.add(cobro)
        db.flush()  # get cobro.id before commit

        try:
            ep = _epagos()
            if tipo == "programado" and fecha_cobro:
                resultado = ep.solicitud_pago_recurrente_suscripcion(
                    identificador_cliente=cliente.identificador_cliente,
                    identificador_cuenta=cuenta.identificador_cuenta,
                    importe=importe,
                    numero_operacion=numero_op,
                    fecha_cobro=date.fromisoformat(fecha_cobro),
                    nombre_pagador=cliente.nombre,
                    apellido_pagador=cliente.apellido,
                    email_pagador=cliente.email,
                    dni_pagador=cliente.dni,
                    cuit_pagador=cliente.cuit,
                    descripcion=descripcion,
                )
                cobro.estado = "programado"
            else:
                resultado = ep.solicitud_pago_recurrente(
                    identificador_cliente=cliente.identificador_cliente,
                    identificador_cuenta=cuenta.identificador_cuenta,
                    importe=importe,
                    numero_operacion=numero_op,
                    nombre_pagador=cliente.nombre,
                    apellido_pagador=cliente.apellido,
                    email_pagador=cliente.email,
                    dni_pagador=cliente.dni,
                    cuit_pagador=cliente.cuit,
                    descripcion=descripcion,
                )
                cobro.id_transaccion = str(resultado.get("id_transaccion", ""))
                cobro.estado = "pendiente"
        except EpagosError as e:
            cobro.estado = "error"
            cobro.error = str(e)

        db.commit()
    return RedirectResponse(f"/dashboard/clientes/{cliente_id}", status_code=302)


@router.post("/dashboard/webhook_estado")
async def webhook_actualizar_estado(request: Request):
    """Recibe el webhook de ePagos y actualiza el estado del cobro en la BD."""
    data = await request.json()
    id_op  = data.get("id_operacion") or data.get("numero_operacion", "")
    estado = data.get("estado", "")
    if not id_op:
        return {"ok": False}
    with get_session() as db:
        cobro = db.query(Cobro).filter(
            (Cobro.numero_operacion == id_op) | (Cobro.id_transaccion == id_op)
        ).first()
        if cobro:
            cobro.estado = estado
            db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# JSON API — /api/*
# ---------------------------------------------------------------------------

def _api_require_auth(request: Request):
    if not _get_current_user(request):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    return None


_MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
             "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


@router.get("/api/stats")
def api_stats(request: Request):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        total_clientes = db.query(func.count(Cliente.id)).scalar() or 0
        total_cuentas  = db.query(func.count(Cuenta.id)).scalar() or 0
        total_cobros   = db.query(func.count(Cobro.id)).scalar() or 0
        monto_acreditado = db.query(func.sum(Cobro.importe)).filter(
            Cobro.estado == "acreditado"
        ).scalar() or 0.0

        # Cobros por estado
        rows = db.query(Cobro.estado, func.count(Cobro.id)).group_by(Cobro.estado).all()
        cobros_por_estado = {estado: cnt for estado, cnt in rows}

        # Cobros por mes — últimos 6 meses
        hoy = date.today()
        cobros_por_mes = []
        for i in range(5, -1, -1):
            mes_dt = hoy - relativedelta(months=i)
            mes_num = mes_dt.month
            anio = mes_dt.year
            total_mes = db.query(func.count(Cobro.id)).filter(
                extract("month", Cobro.creado_en) == mes_num,
                extract("year",  Cobro.creado_en) == anio,
            ).scalar() or 0
            monto_mes = db.query(func.sum(Cobro.importe)).filter(
                extract("month", Cobro.creado_en) == mes_num,
                extract("year",  Cobro.creado_en) == anio,
            ).scalar() or 0.0
            cobros_por_mes.append({
                "mes":   _MESES_ES[mes_num - 1],
                "total": total_mes,
                "monto": round(float(monto_mes), 2),
            })

        return {
            "total_clientes":   total_clientes,
            "total_cuentas":    total_cuentas,
            "total_cobros":     total_cobros,
            "monto_acreditado": round(float(monto_acreditado), 2),
            "cobros_por_estado": cobros_por_estado,
            "cobros_por_mes":   cobros_por_mes,
        }


@router.get("/api/clientes")
def api_listar_clientes(request: Request):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        clientes = db.query(Cliente).order_by(Cliente.apellido).all()
        result = []
        for cl in clientes:
            total_cobrado = db.query(func.sum(Cobro.importe)).filter(
                Cobro.cliente_id == cl.id,
                Cobro.estado == "acreditado",
            ).scalar() or 0.0
            result.append({
                "id":                    cl.id,
                "nombre":                cl.nombre,
                "apellido":              cl.apellido,
                "email":                 cl.email,
                "dni":                   cl.dni,
                "cuit":                  cl.cuit,
                "identificador_cliente": cl.identificador_cliente,
                "num_cuentas":           len(cl.cuentas),
                "total_cobrado":         round(float(total_cobrado), 2),
                "creado_en":             cl.creado_en.isoformat(),
            })
        return result


@router.post("/api/clientes")
async def api_crear_cliente(request: Request):
    err = _api_require_auth(request)
    if err:
        return err
    data = await request.json()
    nombre                = data.get("nombre", "").strip()
    apellido              = data.get("apellido", "").strip()
    email                 = data.get("email", "").strip()
    dni                   = data.get("dni")
    cuit                  = data.get("cuit")
    identificador_cliente = data.get("identificador_cliente", "").strip()

    if not all([nombre, apellido, email, dni, cuit, identificador_cliente]):
        return JSONResponse({"error": "Faltan campos obligatorios"}, status_code=422)
    if len(identificador_cliente) < 6:
        return JSONResponse(
            {"error": "El identificador debe tener al menos 6 caracteres"}, status_code=422
        )
    with get_session() as db:
        existe = db.query(Cliente).filter(
            Cliente.identificador_cliente == identificador_cliente
        ).first()
        if existe:
            return JSONResponse({"error": "El identificador ya existe"}, status_code=409)
        cliente = Cliente(
            identificador_cliente=identificador_cliente,
            nombre=nombre,
            apellido=apellido,
            email=email,
            dni=int(dni),
            cuit=int(cuit),
        )
        db.add(cliente)
        db.commit()
        db.refresh(cliente)
        return JSONResponse({
            "id":                    cliente.id,
            "nombre":                cliente.nombre,
            "apellido":              cliente.apellido,
            "email":                 cliente.email,
            "dni":                   cliente.dni,
            "cuit":                  cliente.cuit,
            "identificador_cliente": cliente.identificador_cliente,
            "num_cuentas":           0,
            "total_cobrado":         0.0,
            "creado_en":             cliente.creado_en.isoformat(),
        }, status_code=201)


@router.get("/api/clientes/{cliente_id}")
def api_detalle_cliente(request: Request, cliente_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
        cobros = (
            db.query(Cobro)
            .filter(Cobro.cliente_id == cliente_id)
            .order_by(Cobro.creado_en.desc())
            .all()
        )
        cuentas_data = [
            {
                "id":                   c.id,
                "identificador_cuenta": c.identificador_cuenta,
                "alias":                c.alias or "",
                "cbu":                  c.cbu or "",
                "sincronizado_en":      c.sincronizado_en.isoformat(),
            }
            for c in cliente.cuentas
        ]
        cobros_data = [
            {
                "id":               co.id,
                "numero_operacion": co.numero_operacion,
                "importe":          co.importe,
                "descripcion":      co.descripcion,
                "tipo":             co.tipo,
                "fecha_cobro":      co.fecha_cobro.isoformat() if co.fecha_cobro else None,
                "estado":           co.estado,
                "id_transaccion":   co.id_transaccion or "",
                "error":            co.error or "",
                "creado_en":        co.creado_en.isoformat(),
                "cuenta_id":        co.cuenta_id,
            }
            for co in cobros
        ]
        return {
            "id":                    cliente.id,
            "nombre":                cliente.nombre,
            "apellido":              cliente.apellido,
            "email":                 cliente.email,
            "dni":                   cliente.dni,
            "cuit":                  cliente.cuit,
            "identificador_cliente": cliente.identificador_cliente,
            "creado_en":             cliente.creado_en.isoformat(),
            "cuentas":               cuentas_data,
            "cobros":                cobros_data,
        }


@router.delete("/api/clientes/{cliente_id}")
def api_eliminar_cliente(request: Request, cliente_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
        db.delete(cliente)
        db.commit()
    return {"ok": True, "id": cliente_id}


@router.post("/api/clientes/{cliente_id}/sync_cbu")
def api_sync_cbu(request: Request, cliente_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
        try:
            ep = _epagos()
            cuentas_api = ep.obtener_cuentas_cliente(cliente.identificador_cliente)
            nuevas = 0
            for c in cuentas_api:
                id_cuenta = str(c.get("identificador_cuenta", ""))
                if not id_cuenta:
                    continue
                existe = db.query(Cuenta).filter(
                    Cuenta.identificador_cuenta == id_cuenta
                ).first()
                if not existe:
                    db.add(Cuenta(
                        cliente_id=cliente_id,
                        identificador_cuenta=id_cuenta,
                        alias=c.get("alias") or c.get("descripcion") or "",
                        cbu=c.get("cbu") or c.get("numero_cuenta") or "",
                    ))
                    nuevas += 1
            db.commit()
            db.refresh(cliente)
            cuentas_data = [
                {
                    "id":                   c.id,
                    "identificador_cuenta": c.identificador_cuenta,
                    "alias":                c.alias or "",
                    "cbu":                  c.cbu or "",
                    "sincronizado_en":      c.sincronizado_en.isoformat(),
                }
                for c in cliente.cuentas
            ]
            return {"ok": True, "nuevas": nuevas, "cuentas": cuentas_data}
        except EpagosError as e:
            return JSONResponse({"error": str(e)}, status_code=502)


@router.get("/api/cobros")
def api_listar_cobros(request: Request, estado: Optional[str] = None):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        q = db.query(Cobro).join(Cliente).order_by(Cobro.creado_en.desc()).limit(200)
        if estado:
            q = db.query(Cobro).join(Cliente).filter(
                Cobro.estado == estado
            ).order_by(Cobro.creado_en.desc()).limit(200)
        cobros = q.all()
        return [
            {
                "id":               co.id,
                "cliente_id":       co.cliente_id,
                "cliente_nombre":   f"{co.cliente.apellido}, {co.cliente.nombre}",
                "numero_operacion": co.numero_operacion,
                "importe":          co.importe,
                "descripcion":      co.descripcion,
                "tipo":             co.tipo,
                "fecha_cobro":      co.fecha_cobro.isoformat() if co.fecha_cobro else None,
                "estado":           co.estado,
                "id_transaccion":   co.id_transaccion or "",
                "error":            co.error or "",
                "creado_en":        co.creado_en.isoformat(),
            }
            for co in cobros
        ]


@router.post("/api/cobros")
async def api_crear_cobro(request: Request):
    err = _api_require_auth(request)
    if err:
        return err
    data = await request.json()
    cliente_id  = data.get("cliente_id")
    cuenta_id   = data.get("cuenta_id")
    importe     = data.get("importe")
    descripcion = data.get("descripcion", "Cobro recurrente")
    tipo        = data.get("tipo", "inmediato")
    fecha_cobro = data.get("fecha_cobro")

    if not all([cliente_id, cuenta_id, importe]):
        return JSONResponse({"error": "Faltan campos obligatorios"}, status_code=422)

    with get_session() as db:
        cliente = db.get(Cliente, int(cliente_id))
        cuenta  = db.get(Cuenta, int(cuenta_id))
        if not cliente or not cuenta:
            return JSONResponse({"error": "Cliente o cuenta no encontrados"}, status_code=404)

        numero_op = f"OP-{uuid.uuid4().hex[:12].upper()}"
        cobro = Cobro(
            cliente_id=int(cliente_id),
            cuenta_id=int(cuenta_id),
            numero_operacion=numero_op,
            importe=float(importe),
            descripcion=descripcion,
            tipo=tipo,
            fecha_cobro=date.fromisoformat(fecha_cobro) if fecha_cobro else None,
            estado="enviado",
        )
        db.add(cobro)
        db.flush()

        try:
            ep = _epagos()
            if tipo == "programado" and fecha_cobro:
                ep.solicitud_pago_recurrente_suscripcion(
                    identificador_cliente=cliente.identificador_cliente,
                    identificador_cuenta=cuenta.identificador_cuenta,
                    importe=float(importe),
                    numero_operacion=numero_op,
                    fecha_cobro=date.fromisoformat(fecha_cobro),
                    nombre_pagador=cliente.nombre,
                    apellido_pagador=cliente.apellido,
                    email_pagador=cliente.email,
                    dni_pagador=cliente.dni,
                    cuit_pagador=cliente.cuit,
                    descripcion=descripcion,
                )
                cobro.estado = "programado"
            else:
                resultado = ep.solicitud_pago_recurrente(
                    identificador_cliente=cliente.identificador_cliente,
                    identificador_cuenta=cuenta.identificador_cuenta,
                    importe=float(importe),
                    numero_operacion=numero_op,
                    nombre_pagador=cliente.nombre,
                    apellido_pagador=cliente.apellido,
                    email_pagador=cliente.email,
                    dni_pagador=cliente.dni,
                    cuit_pagador=cliente.cuit,
                    descripcion=descripcion,
                )
                cobro.id_transaccion = str(resultado.get("id_transaccion", ""))
                cobro.estado = "pendiente"
        except EpagosError as e:
            cobro.estado = "error"
            cobro.error = str(e)

        db.commit()
        return JSONResponse({
            "id":               cobro.id,
            "numero_operacion": cobro.numero_operacion,
            "importe":          cobro.importe,
            "estado":           cobro.estado,
            "tipo":             cobro.tipo,
            "creado_en":        cobro.creado_en.isoformat(),
            "error":            cobro.error or "",
        }, status_code=201)


# ---------------------------------------------------------------------------
# Cobros masivos
# ---------------------------------------------------------------------------

@router.get("/dashboard/cobros/masivo", response_class=HTMLResponse)
def cobros_masivo_page(request: Request):
    if not _get_current_user(request):
        return _redirect_login()
    with get_session() as db:
        clientes = db.query(Cliente).order_by(Cliente.apellido).all()
        clientes_data = [
            {
                "id":                    cl.id,
                "nombre":                cl.nombre,
                "apellido":              cl.apellido,
                "email":                 cl.email,
                "identificador_cliente": cl.identificador_cliente,
                "cuentas": [
                    {
                        "id":                   ct.id,
                        "identificador_cuenta": ct.identificador_cuenta,
                        "alias":                ct.alias or "",
                        "cbu":                  ct.cbu or "",
                    }
                    for ct in cl.cuentas
                ],
            }
            for cl in clientes
        ]
    return templates.TemplateResponse("dashboard/cobros_masivo.html", {
        "request":  request,
        "clientes": clientes_data,
    })


@router.post("/api/cobros/masivo")
async def api_cobros_masivo(request: Request):
    """Genera cobros en lote. Body: {descripcion, tipo, fecha_cobro, cobros:[{cliente_id,cuenta_id,importe}]}"""
    err = _api_require_auth(request)
    if err:
        return err

    data        = await request.json()
    descripcion = data.get("descripcion", "Cobro recurrente")
    tipo        = data.get("tipo", "inmediato")
    fecha_cobro = data.get("fecha_cobro")
    items       = data.get("cobros", [])

    if not items:
        return JSONResponse({"error": "No hay cobros para procesar"}, status_code=422)

    resultados = []
    exitosos = fallidos = 0

    with get_session() as db:
        ep = _epagos()
        for item in items:
            cliente_id = item.get("cliente_id")
            cuenta_id  = item.get("cuenta_id")
            importe    = item.get("importe")

            if not all([cliente_id, cuenta_id, importe]):
                resultados.append({"cliente_id": cliente_id, "cliente_nombre": "—",
                                    "estado": "error", "numero_operacion": None,
                                    "error": "Faltan datos"})
                fallidos += 1
                continue

            cliente = db.get(Cliente, int(cliente_id))
            cuenta  = db.get(Cuenta,  int(cuenta_id))
            if not cliente or not cuenta:
                resultados.append({"cliente_id": cliente_id,
                                    "cliente_nombre": str(cliente_id),
                                    "estado": "error", "numero_operacion": None,
                                    "error": "Cliente o cuenta no encontrados"})
                fallidos += 1
                continue

            numero_op = f"OP-{uuid.uuid4().hex[:12].upper()}"
            cobro = Cobro(cliente_id=int(cliente_id), cuenta_id=int(cuenta_id),
                          numero_operacion=numero_op, importe=float(importe),
                          descripcion=descripcion, tipo=tipo,
                          fecha_cobro=date.fromisoformat(fecha_cobro) if fecha_cobro else None,
                          estado="enviado")
            db.add(cobro)
            db.flush()
            try:
                if tipo == "programado" and fecha_cobro:
                    ep.solicitud_pago_recurrente_suscripcion(
                        identificador_cliente=cliente.identificador_cliente,
                        identificador_cuenta=cuenta.identificador_cuenta,
                        importe=float(importe), numero_operacion=numero_op,
                        fecha_cobro=date.fromisoformat(fecha_cobro),
                        nombre_pagador=cliente.nombre, apellido_pagador=cliente.apellido,
                        email_pagador=cliente.email, dni_pagador=cliente.dni,
                        cuit_pagador=cliente.cuit, descripcion=descripcion)
                    cobro.estado = "programado"
                else:
                    res = ep.solicitud_pago_recurrente(
                        identificador_cliente=cliente.identificador_cliente,
                        identificador_cuenta=cuenta.identificador_cuenta,
                        importe=float(importe), numero_operacion=numero_op,
                        nombre_pagador=cliente.nombre, apellido_pagador=cliente.apellido,
                        email_pagador=cliente.email, dni_pagador=cliente.dni,
                        cuit_pagador=cliente.cuit, descripcion=descripcion)
                    cobro.id_transaccion = str(res.get("id_transaccion", ""))
                    cobro.estado = "pendiente"
                exitosos += 1
                resultados.append({"cliente_id": cliente.id,
                                    "cliente_nombre": f"{cliente.apellido}, {cliente.nombre}",
                                    "importe": float(importe), "estado": cobro.estado,
                                    "numero_operacion": numero_op, "error": None})
            except EpagosError as e:
                cobro.estado = "error"
                cobro.error  = str(e)
                fallidos += 1
                resultados.append({"cliente_id": cliente.id,
                                    "cliente_nombre": f"{cliente.apellido}, {cliente.nombre}",
                                    "importe": float(importe), "estado": "error",
                                    "numero_operacion": numero_op, "error": str(e)})
        db.commit()

    return {"total": len(items), "exitosos": exitosos, "fallidos": fallidos, "resultados": resultados}
