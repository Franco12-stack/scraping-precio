"""
Dashboard web para gestión de cobros ePagos.
"""
import os
import uuid
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import func
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
