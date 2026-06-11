"""
Dashboard web para gestión de cobros ePagos.
"""
import hashlib
import io
import json
import os
import secrets
import uuid
from calendar import month_abbr
from datetime import date, datetime, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import pandas as pd
from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import func, extract, or_
from sqlalchemy.orm import Session

from db import Cliente, Cuenta, Cobro, Usuario, get_session, init_db
from epagos import EpagosClient, EpagosError

load_dotenv()

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_SECRET = os.getenv("DASHBOARD_SECRET", "cambiar-en-produccion-32chars!!")
_ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
_ADMIN_PASS = os.getenv("DASHBOARD_PASSWORD", "admin")
_WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
_signer = URLSafeTimedSerializer(_SECRET)
_SESSION_MAX_AGE = 60 * 60 * 8  # 8 horas


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${h.hex()}"


def _check_password(password: str, hash_str: str) -> bool:
    try:
        salt, h = hash_str.split("$")
        expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return expected.hex() == h
    except Exception:
        return False


def _seed_admin():
    """Crea el usuario admin inicial si no existe ninguno."""
    with get_session() as db:
        if db.query(func.count(Usuario.id)).scalar() == 0:
            db.add(Usuario(
                username=_ADMIN_USER,
                password_hash=_hash_password(_ADMIN_PASS),
                rol="admin",
                activo=True,
            ))
            db.commit()


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


def _get_usuario(request: Request) -> Optional[Usuario]:
    username = _get_current_user(request)
    if not username:
        return None
    with get_session() as db:
        return db.query(Usuario).filter(Usuario.username == username, Usuario.activo == True).first()


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
    with get_session() as db:
        usuario = db.query(Usuario).filter(
            Usuario.username == username,
            Usuario.activo == True,
        ).first()
    if usuario and _check_password(password, usuario.password_hash):
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
            "entorno":        os.getenv("EPAGOS_ENTORNO", "sandbox"),
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


@router.post("/dashboard/clientes/importar")
def importar_clientes(
    request: Request,
    archivo: UploadFile = File(...),
):
    if not _get_current_user(request):
        return _redirect_login()

    import openpyxl

    def _parsear_nombre(titular: str):
        titular = str(titular).strip()
        if "," in titular:
            partes = titular.split(",", 1)
            return partes[1].strip(), partes[0].strip()
        partes = titular.split()
        if len(partes) == 1:
            return "-", partes[0]
        return partes[0], " ".join(partes[1:])

    def _limpiar_cuit(val) -> Optional[int]:
        try:
            return int(str(val).replace("-", "").replace(".", "").strip())
        except Exception:
            return None

    def _dni_desde_cuit(cuit: int) -> Optional[int]:
        try:
            s = str(cuit)
            if len(s) == 11:
                return int(s[2:10])
        except Exception:
            pass
        return None

    contenido = archivo.file.read()
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    ws = wb.active

    creados = 0
    omitidos = 0
    epagos_ok = 0
    epagos_error = 0
    errores = []

    ep = _epagos()

    with get_session() as db:
        for i, fila in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not fila or not fila[0]:
                continue
            titular = fila[0]
            cuit_raw = fila[1]
            cbu_raw = fila[2]
            banco = fila[3] if len(fila) > 3 else None

            cuit = _limpiar_cuit(cuit_raw)
            if not cuit:
                errores.append(f"Fila {i}: CUIT inválido ({cuit_raw})")
                continue

            cbu = str(cbu_raw).strip() if cbu_raw else None

            identificador = f"CLI-{cuit}"
            if db.query(Cliente).filter(Cliente.identificador_cliente == identificador).first():
                omitidos += 1
                continue

            nombre, apellido = _parsear_nombre(titular)
            dni = _dni_desde_cuit(cuit)

            cliente = Cliente(
                identificador_cliente=identificador,
                nombre=nombre,
                apellido=apellido,
                email="",
                dni=dni,
                cuit=cuit,
            )
            db.add(cliente)
            db.flush()

            if cbu:
                id_cuenta = cbu
                try:
                    res = ep.registrar_cuenta_cliente(
                        identificador_cliente=identificador,
                        cbu=cbu,
                        cuit=cuit,
                    )
                    id_cuenta = res.get("identificador_cuenta") or cbu
                    epagos_ok += 1
                except Exception as e:
                    epagos_error += 1
                    errores.append(f"Fila {i} ({titular}): ePagos — {e}")

                db.add(Cuenta(
                    cliente_id=cliente.id,
                    identificador_cuenta=id_cuenta,
                    alias=str(banco).strip() if banco else None,
                    cbu=cbu,
                ))

            creados += 1

        db.commit()

    return JSONResponse({
        "creados": creados,
        "omitidos": omitidos,
        "epagos_ok": epagos_ok,
        "epagos_error": epagos_error,
        "errores": errores,
    })


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
    descripcion:  str   = Form("fundcolab"),
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
                cobro.id_transaccion = str(resultado.get("id_transaccion") or "")
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
    if _WEBHOOK_SECRET:
        token = request.headers.get("X-Webhook-Token", "")
        if not secrets.compare_digest(token, _WEBHOOK_SECRET):
            return JSONResponse({"ok": False, "error": "No autorizado"}, status_code=401)
    data = await request.json()
    id_op  = data.get("numero_operacion") or data.get("id_transaccion") or data.get("id_operacion", "")
    estado = data.get("estado", "")
    if not id_op or not estado:
        return JSONResponse({"ok": False, "error": "Faltan campos"}, status_code=400)
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
        except Exception as exc:
            return JSONResponse({"error": f"Error al conectar con ePagos: {exc}"}, status_code=502)


@router.post("/api/clientes/{cliente_id}/link_adhesion")
def api_link_adhesion(request: Request, cliente_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
        try:
            ep = _epagos()
            url = ep.generar_link_adhesion(
                identificador_cliente = cliente.identificador_cliente,
                email_pagador         = cliente.email or "sin@email.com",
                importe               = 1.0,
                descripcion           = "Adhesión débito directo",
            )
            return {"ok": True, "url": url}
        except EpagosError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        except Exception as exc:
            return JSONResponse({"error": f"Error al conectar con ePagos: {exc}"}, status_code=502)


@router.post("/api/clientes/{cliente_id}/agregar_cbu")
async def api_agregar_cbu(request: Request, cliente_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    body = await request.json()
    cbu = (body.get("cbu") or "").strip().replace(" ", "")
    alias = (body.get("alias") or "").strip()
    id_manual = (body.get("identificador_cuenta") or "").strip()
    if not cbu or len(cbu) != 22 or not cbu.isdigit():
        return JSONResponse({"error": "El CBU debe tener exactamente 22 dígitos"}, status_code=400)
    with get_session() as db:
        cliente = db.get(Cliente, cliente_id)
        if not cliente:
            return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
        existe = db.query(Cuenta).filter(Cuenta.cbu == cbu).first()
        if existe:
            return JSONResponse({"error": "Ese CBU ya está registrado"}, status_code=409)

        # Si el usuario ingresó el ID manual, usarlo directamente
        if id_manual:
            id_cuenta = id_manual
            epagos_ok = True
            epagos_advertencia = None
            print(f"[agregar_cbu] ID manual: {id_cuenta}", flush=True)
        else:
            # Registrar en ePagos v2.1
            epagos_advertencia = None
            epagos_id_cuenta = None
            try:
                ep = _epagos()
                resultado = ep.registrar_cuenta_cliente(
                    identificador_cliente=cliente.identificador_cliente,
                    cbu=cbu,
                    cuit=cliente.cuit,
                )
                epagos_id_cuenta = resultado.get("identificador_cuenta") or None
                print(f"[agregar_cbu] ePagos OK → identificador_cuenta={epagos_id_cuenta}", flush=True)
            except (EpagosError, Exception) as exc:
                epagos_advertencia = str(exc)
                print(f"[agregar_cbu] ePagos FALLÓ: {exc}", flush=True)
            id_cuenta = epagos_id_cuenta or f"CBU-{cbu}"
            epagos_ok = epagos_id_cuenta is not None
        nueva = Cuenta(
            cliente_id=cliente_id,
            identificador_cuenta=id_cuenta,
            alias=alias or f"CBU {cbu[-4:]}",
            cbu=cbu,
        )
        db.add(nueva)
        db.commit()
        db.refresh(nueva)
        return {
            "ok": True,
            "epagos_ok": epagos_ok,
            "advertencia": epagos_advertencia,
            "cuenta": {
                "id": nueva.id,
                "identificador_cuenta": nueva.identificador_cuenta,
                "alias": nueva.alias,
                "cbu": nueva.cbu,
                "sincronizado_en": nueva.sincronizado_en.isoformat(),
            }
        }


@router.delete("/api/cuentas/{cuenta_id}")
def api_eliminar_cuenta(request: Request, cuenta_id: int):
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cuenta = db.get(Cuenta, cuenta_id)
        if not cuenta:
            return JSONResponse({"error": "Cuenta no encontrada"}, status_code=404)
        db.delete(cuenta)
        db.commit()
    return {"ok": True}


def _es_uuid_epagos(identificador: str) -> bool:
    """True si el identificador parece un UUID asignado por ePagos (contiene guiones)."""
    return "-" in identificador and len(identificador) > 30


@router.get("/api/cuentas/pendientes_epagos")
def api_cuentas_pendientes(request: Request):
    """Cuenta cuántos CBU no tienen UUID de ePagos todavía."""
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        cuentas = db.query(Cuenta).all()
        pendientes = [c for c in cuentas if not _es_uuid_epagos(c.identificador_cuenta or "")]
        return {"total": len(pendientes), "pendientes": len(pendientes)}


@router.post("/api/cuentas/re_registrar_pendientes")
def api_re_registrar_pendientes(request: Request):
    """Re-intenta registrar en ePagos todos los CBU que no tienen UUID."""
    err = _api_require_auth(request)
    if err:
        return err

    ep = _epagos()
    ok = fallidos = saltados = 0
    errores = []

    with get_session() as db:
        cuentas = (
            db.query(Cuenta)
            .join(Cliente)
            .filter(Cuenta.cbu.isnot(None))
            .all()
        )
        pendientes = [c for c in cuentas if not _es_uuid_epagos(c.identificador_cuenta or "")]

    total = len(pendientes)
    print(f"[re_registrar] Iniciando re-registro de {total} CBU pendientes", flush=True)

    for idx, cuenta in enumerate(pendientes, 1):
        if not cuenta.cbu:
            saltados += 1
            continue
        # Cargar cliente en sesión corta
        with get_session() as db:
            cliente = db.get(Cliente, cuenta.cliente_id)
            if not cliente:
                saltados += 1
                continue
            id_cliente_ep = cliente.identificador_cliente
            cuit = cliente.cuit
            nombre_log = f"{cliente.apellido}"

        # Llamar ePagos FUERA de sesión DB
        try:
            res = ep.registrar_cuenta_cliente(
                identificador_cliente=id_cliente_ep,
                cbu=cuenta.cbu,
                cuit=cuit,
            )
            nuevo_id = res.get("identificador_cuenta")
            if nuevo_id and _es_uuid_epagos(nuevo_id):
                with get_session() as db:
                    c = db.get(Cuenta, cuenta.id)
                    if c:
                        c.identificador_cuenta = nuevo_id
                        db.commit()
                ok += 1
                print(f"[re_registrar] {idx}/{total} OK {nombre_log} ({id_cliente_ep}) → {nuevo_id}", flush=True)
            else:
                fallidos += 1
                msg = f"sin UUID en respuesta"
                errores.append(f"Cuenta {cuenta.id} (CBU {cuenta.cbu}): {msg}")
                print(f"[re_registrar] {idx}/{total} FALLO {nombre_log} ({id_cliente_ep}): {msg}", flush=True)
        except (EpagosError, Exception) as exc:
            fallidos += 1
            errores.append(f"Cuenta {cuenta.id} (CBU {cuenta.cbu}): {exc}")
            print(f"[re_registrar] {idx}/{total} ERROR {nombre_log} ({id_cliente_ep}): {exc}", flush=True)

    print(f"[re_registrar] FIN — total={total} ok={ok} fallidos={fallidos} saltados={saltados}", flush=True)

    return {
        "total_pendientes": len(pendientes),
        "ok": ok,
        "fallidos": fallidos,
        "saltados": saltados,
        "errores": errores[:20],  # Máx 20 errores para no sobrecargar la respuesta
    }


@router.get("/api/cobros")
def api_listar_cobros(
    request: Request,
    estado:    Optional[str] = None,
    busqueda:  Optional[str] = None,
    page:      int = 1,
    per_page:  int = 100,
):
    err = _api_require_auth(request)
    if err:
        return err
    page     = max(1, page)
    per_page = max(1, min(500, per_page))
    with get_session() as db:
        q = db.query(Cobro).join(Cliente)
        if estado:
            q = q.filter(Cobro.estado == estado)
        if busqueda:
            b = f"%{busqueda}%"
            q = q.filter(or_(
                Cliente.nombre.ilike(b),
                Cliente.apellido.ilike(b),
                Cobro.numero_operacion.ilike(b),
                Cobro.id_transaccion.ilike(b),
            ))
        total  = q.count()
        pages  = max(1, (total + per_page - 1) // per_page)
        page   = min(page, pages)
        cobros = q.order_by(Cobro.creado_en.desc()).offset((page - 1) * per_page).limit(per_page).all()
        return {
            "cobros": [
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
            ],
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "pages":    pages,
        }


@router.post("/api/cobros")
async def api_crear_cobro(request: Request):
    err = _api_require_auth(request)
    if err:
        return err
    data = await request.json()
    cliente_id  = data.get("cliente_id")
    cuenta_id   = data.get("cuenta_id")
    importe     = data.get("importe")
    descripcion = data.get("descripcion", "fundcolab")
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
                resultado = ep.solicitud_pago_recurrente_suscripcion(
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
                cobro.id_transaccion = str(resultado.get("id_transaccion") or "")
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
        except Exception as exc:
            cobro.estado = "error"
            cobro.error = f"Error al conectar con ePagos: {exc}"

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
    """Genera cobros en lote (SSE). Body: {descripcion, tipo, fecha_cobro, cobros:[{cliente_id,cuenta_id,importe}]}"""
    err = _api_require_auth(request)
    if err:
        return err

    data        = await request.json()
    descripcion = data.get("descripcion", "fundcolab")
    tipo        = data.get("tipo", "inmediato")
    fecha_cobro = data.get("fecha_cobro")
    items       = data.get("cobros", [])

    if not items:
        return JSONResponse({"error": "No hay cobros para procesar"}, status_code=422)

    total_items = len(items)
    print(f"[cobros_masivo] Iniciando lote de {total_items} cobros", flush=True)

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    def generate():
        exitosos = 0
        fallidos = 0
        ep = _epagos()

        for idx, item in enumerate(items, 1):
            cliente_id = item.get("cliente_id")
            cuenta_id  = item.get("cuenta_id")
            importe    = item.get("importe")
            result_row = {
                "cliente_id": cliente_id, "cliente_nombre": "—",
                "estado": "error", "numero_operacion": None,
                "importe": importe, "error": None,
            }

            if not all([cliente_id, cuenta_id, importe]):
                result_row["error"] = "Faltan datos"
                fallidos += 1
                yield _sse({"type": "progress", "idx": idx, "total": total_items,
                            "exitosos": exitosos, "fallidos": fallidos, "result": result_row})
                continue

            # Cargar cliente/cuenta y registrar CBU si hace falta (sesión corta)
            id_cliente = id_cuenta = nombre = apellido = email = dni = cuit = cbu = None
            try:
                with get_session() as db:
                    cliente = db.get(Cliente, int(cliente_id))
                    cuenta  = db.get(Cuenta,  int(cuenta_id))
                    if not cliente or not cuenta:
                        result_row["error"] = "Cliente o cuenta no encontrados"
                        fallidos += 1
                        yield _sse({"type": "progress", "idx": idx, "total": total_items,
                                    "exitosos": exitosos, "fallidos": fallidos, "result": result_row})
                        continue

                    id_cliente = cliente.identificador_cliente
                    nombre     = cliente.nombre
                    apellido   = cliente.apellido
                    email      = cliente.email
                    dni        = cliente.dni
                    cuit       = cliente.cuit
                    id_cuenta  = cuenta.identificador_cuenta or ""
                    cbu        = cuenta.cbu

                    _id = id_cuenta
                    _necesita_registro = cbu and (
                        _id == cbu
                        or _id.startswith("CBU-")
                        or (len(_id) == 22 and _id.isdigit())
                    )
                    if _necesita_registro:
                        try:
                            res_reg = ep.registrar_cuenta_cliente(
                                identificador_cliente=id_cliente, cbu=cbu, cuit=cuit)
                            nuevo_id = res_reg.get("identificador_cuenta")
                            if nuevo_id:
                                cuenta.identificador_cuenta = nuevo_id
                                id_cuenta = nuevo_id
                        except Exception:
                            pass
                    db.commit()
            except Exception as exc_db:
                result_row["error"] = f"Error DB: {exc_db}"
                fallidos += 1
                yield _sse({"type": "progress", "idx": idx, "total": total_items,
                            "exitosos": exitosos, "fallidos": fallidos, "result": result_row})
                continue

            result_row["cliente_nombre"] = f"{apellido}, {nombre}"
            numero_op = f"OP-{uuid.uuid4().hex[:12].upper()}"
            result_row["numero_operacion"] = numero_op

            # Crear cobro en DB estado "enviado" (sesión corta)
            cobro_id = None
            try:
                with get_session() as db:
                    cobro = Cobro(
                        cliente_id=int(cliente_id), cuenta_id=int(cuenta_id),
                        numero_operacion=numero_op, importe=float(importe),
                        descripcion=descripcion, tipo=tipo,
                        fecha_cobro=date.fromisoformat(fecha_cobro) if fecha_cobro else None,
                        estado="enviado",
                    )
                    db.add(cobro)
                    db.commit()
                    cobro_id = cobro.id
            except Exception as exc_db:
                result_row["error"] = f"Error DB cobro: {exc_db}"
                fallidos += 1
                yield _sse({"type": "progress", "idx": idx, "total": total_items,
                            "exitosos": exitosos, "fallidos": fallidos, "result": result_row})
                continue

            # Llamar ePagos fuera de sesión DB
            id_transaccion = ""
            estado_cobro   = "error"
            error_msg      = None
            try:
                if tipo == "programado" and fecha_cobro:
                    res_sus = ep.solicitud_pago_recurrente_suscripcion(
                        identificador_cliente=id_cliente, identificador_cuenta=id_cuenta,
                        importe=float(importe), numero_operacion=numero_op,
                        fecha_cobro=date.fromisoformat(fecha_cobro),
                        nombre_pagador=nombre, apellido_pagador=apellido,
                        email_pagador=email, dni_pagador=dni,
                        cuit_pagador=cuit, descripcion=descripcion)
                    id_transaccion = str(res_sus.get("id_transaccion") or "")
                    estado_cobro = "programado"
                else:
                    res = ep.solicitud_pago_recurrente(
                        identificador_cliente=id_cliente, identificador_cuenta=id_cuenta,
                        importe=float(importe), numero_operacion=numero_op,
                        nombre_pagador=nombre, apellido_pagador=apellido,
                        email_pagador=email, dni_pagador=dni,
                        cuit_pagador=cuit, descripcion=descripcion)
                    id_transaccion = str(res.get("id_transaccion", ""))
                    estado_cobro = "pendiente"
                exitosos += 1
                result_row["estado"] = estado_cobro
                print(f"[cobros_masivo] {idx}/{total_items} OK {apellido} ({id_cliente}) ${importe} → {numero_op}", flush=True)
            except (EpagosError, Exception) as e:
                estado_cobro = "error"
                error_msg    = str(e)
                result_row["estado"] = "error"
                result_row["error"]  = error_msg
                fallidos += 1
                print(f"[cobros_masivo] {idx}/{total_items} FALLO {apellido} ({id_cliente}): {e}", flush=True)

            # Guardar estado final del cobro (sesión corta)
            if cobro_id:
                try:
                    with get_session() as db:
                        c = db.get(Cobro, cobro_id)
                        if c:
                            c.estado = estado_cobro
                            c.id_transaccion = id_transaccion
                            if error_msg:
                                c.error = error_msg
                            db.commit()
                except Exception as exc_db:
                    print(f"[cobros_masivo] {idx} error guardando estado: {exc_db}", flush=True)

            yield _sse({"type": "progress", "idx": idx, "total": total_items,
                        "exitosos": exitosos, "fallidos": fallidos, "result": result_row})

        print(f"[cobros_masivo] FIN — total={total_items} exitosos={exitosos} fallidos={fallidos}", flush=True)
        yield _sse({"type": "done", "total": total_items, "exitosos": exitosos, "fallidos": fallidos})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/cobros/pendientes_reintento")
def api_cobros_pendientes_reintento(request: Request):
    """Cuenta cuántos cobros están en estado error y se pueden reintentar."""
    err = _api_require_auth(request)
    if err:
        return err
    with get_session() as db:
        total = db.query(func.count(Cobro.id)).filter(Cobro.estado == "error").scalar() or 0
        return {"pendientes": total}


@router.post("/api/cobros/reintentar_fallidos")
def api_reintentar_cobros_fallidos(request: Request):
    """Reintenta todos los cobros que quedaron en estado 'error'."""
    err = _api_require_auth(request)
    if err:
        return err

    with get_session() as db:
        cobros_err = db.query(Cobro).filter(Cobro.estado == "error").all()
        ids = [(c.id, c.cliente_id, c.cuenta_id, c.importe, c.descripcion, c.tipo,
                c.fecha_cobro, c.numero_operacion) for c in cobros_err]

    total = len(ids)
    print(f"[reintentar_cobros] Iniciando reintento de {total} cobros fallidos", flush=True)

    ep = _epagos()
    exitosos = fallidos = 0

    for idx, (cobro_id, cliente_id, cuenta_id, importe, descripcion, tipo,
              fecha_cobro_db, numero_op) in enumerate(ids, 1):
        # Cargar datos cliente/cuenta (sesión corta)
        with get_session() as db:
            cliente = db.get(Cliente, cliente_id)
            cuenta  = db.get(Cuenta, cuenta_id) if cuenta_id else None
            if not cliente or not cuenta:
                fallidos += 1
                continue
            datos_cliente = {
                "identificador_cliente": cliente.identificador_cliente,
                "identificador_cuenta":  cuenta.identificador_cuenta,
                "nombre":   cliente.nombre,
                "apellido": cliente.apellido,
                "email":    cliente.email,
                "dni":      cliente.dni,
                "cuit":     cliente.cuit,
            }

        # Llamar ePagos FUERA de sesión DB
        nuevo_estado = "error"
        nuevo_error  = None
        id_trans     = None
        try:
            if tipo == "programado" and fecha_cobro_db:
                res = ep.solicitud_pago_recurrente_suscripcion(
                    identificador_cliente=datos_cliente["identificador_cliente"],
                    identificador_cuenta=datos_cliente["identificador_cuenta"],
                    importe=float(importe), numero_operacion=numero_op,
                    fecha_cobro=fecha_cobro_db,
                    nombre_pagador=datos_cliente["nombre"],
                    apellido_pagador=datos_cliente["apellido"],
                    email_pagador=datos_cliente["email"],
                    dni_pagador=datos_cliente["dni"],
                    cuit_pagador=datos_cliente["cuit"],
                    descripcion=descripcion or "fundcolab",
                )
                nuevo_estado = "programado"
            else:
                res = ep.solicitud_pago_recurrente(
                    identificador_cliente=datos_cliente["identificador_cliente"],
                    identificador_cuenta=datos_cliente["identificador_cuenta"],
                    importe=float(importe), numero_operacion=numero_op,
                    nombre_pagador=datos_cliente["nombre"],
                    apellido_pagador=datos_cliente["apellido"],
                    email_pagador=datos_cliente["email"],
                    dni_pagador=datos_cliente["dni"],
                    cuit_pagador=datos_cliente["cuit"],
                    descripcion=descripcion or "fundcolab",
                )
                nuevo_estado = "pendiente"
            id_trans = str(res.get("id_transaccion") or "")
            exitosos += 1
            print(f"[reintentar_cobros] {idx}/{total} OK {datos_cliente['apellido']} → {numero_op}", flush=True)
        except (EpagosError, Exception) as exc:
            nuevo_error = str(exc)
            fallidos += 1
            print(f"[reintentar_cobros] {idx}/{total} FALLO {datos_cliente['apellido']}: {exc}", flush=True)

        # Guardar resultado (sesión corta)
        try:
            with get_session() as db:
                c = db.get(Cobro, cobro_id)
                if c:
                    c.estado = nuevo_estado
                    if id_trans:
                        c.id_transaccion = id_trans
                    if nuevo_error:
                        c.error = nuevo_error
                    else:
                        c.error = None
                    db.commit()
        except Exception as exc_db:
            print(f"[reintentar_cobros] {idx}/{total} error guardando DB: {exc_db}", flush=True)

    print(f"[reintentar_cobros] FIN — total={total} exitosos={exitosos} fallidos={fallidos}", flush=True)
    return {"total": total, "exitosos": exitosos, "fallidos": fallidos}


# ---------------------------------------------------------------------------
# Usuarios (solo admin)
# ---------------------------------------------------------------------------

@router.get("/dashboard/usuarios", response_class=HTMLResponse)
def usuarios_page(request: Request):
    usuario = _get_usuario(request)
    if not usuario:
        return _redirect_login()
    if usuario.rol != "admin":
        return RedirectResponse("/dashboard", status_code=302)
    with get_session() as db:
        usuarios = db.query(Usuario).order_by(Usuario.creado_en).all()
        return templates.TemplateResponse("dashboard/usuarios.html", {
            "request": request,
            "usuarios": [{"id": u.id, "username": u.username, "rol": u.rol,
                           "activo": u.activo, "creado_en": u.creado_en.strftime("%d/%m/%Y")}
                          for u in usuarios],
            "usuario_actual": usuario.username,
        })


@router.post("/api/usuarios")
async def api_crear_usuario(request: Request):
    usuario = _get_usuario(request)
    if not usuario or usuario.rol != "admin":
        return JSONResponse({"error": "No autorizado"}, status_code=403)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = (body.get("password") or "").strip()
    rol      = body.get("rol", "operador")
    if not username or not password:
        return JSONResponse({"error": "Usuario y contraseña son obligatorios"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres"}, status_code=400)
    if rol not in ("admin", "operador"):
        rol = "operador"
    with get_session() as db:
        if db.query(Usuario).filter(Usuario.username == username).first():
            return JSONResponse({"error": "El usuario ya existe"}, status_code=409)
        nuevo = Usuario(username=username, password_hash=_hash_password(password), rol=rol, activo=True)
        db.add(nuevo)
        db.commit()
        db.refresh(nuevo)
        return {"ok": True, "id": nuevo.id, "username": nuevo.username, "rol": nuevo.rol,
                "activo": nuevo.activo, "creado_en": nuevo.creado_en.strftime("%d/%m/%Y")}


@router.patch("/api/usuarios/{usuario_id}")
async def api_editar_usuario(request: Request, usuario_id: int):
    usuario = _get_usuario(request)
    if not usuario or usuario.rol != "admin":
        return JSONResponse({"error": "No autorizado"}, status_code=403)
    body = await request.json()
    with get_session() as db:
        u = db.get(Usuario, usuario_id)
        if not u:
            return JSONResponse({"error": "Usuario no encontrado"}, status_code=404)
        if u.username == usuario.username:
            return JSONResponse({"error": "No podés modificar tu propio usuario"}, status_code=400)
        if "activo" in body:
            u.activo = bool(body["activo"])
        if "rol" in body and body["rol"] in ("admin", "operador"):
            u.rol = body["rol"]
        if "password" in body and body["password"]:
            if len(body["password"]) < 6:
                return JSONResponse({"error": "Contraseña muy corta (mín. 6 caracteres)"}, status_code=400)
            u.password_hash = _hash_password(body["password"])
        db.commit()
        return {"ok": True}


@router.delete("/api/usuarios/{usuario_id}")
def api_eliminar_usuario(request: Request, usuario_id: int):
    usuario = _get_usuario(request)
    if not usuario or usuario.rol != "admin":
        return JSONResponse({"error": "No autorizado"}, status_code=403)
    with get_session() as db:
        u = db.get(Usuario, usuario_id)
        if not u:
            return JSONResponse({"error": "Usuario no encontrado"}, status_code=404)
        if u.username == usuario.username:
            return JSONResponse({"error": "No podés eliminar tu propio usuario"}, status_code=400)
        db.delete(u)
        db.commit()
        return {"ok": True}


# ---------------------------------------------------------------------------
# Importación desde Excel / CSV
# ---------------------------------------------------------------------------

@router.get("/dashboard/importar", response_class=HTMLResponse)
def importar_page(request: Request):
    if not _get_current_user(request):
        return _redirect_login()
    return templates.TemplateResponse("dashboard/importar.html", {"request": request})


@router.get("/api/importar_clientes/template")
def descargar_template(request: Request):
    """Devuelve un CSV de ejemplo para usar como plantilla de importación."""
    err = _api_require_auth(request)
    if err:
        return err
    contenido = (
        "TITULAR,CUIT/CUIL,CBU,BANCO\n"
        "GARCIA JUAN,20123456789,3220001101000040970011,Banco Nación\n"
        "LOPEZ MARIA,27234567890,0720461088000012345678,Banco Galicia\n"
    )
    return StreamingResponse(
        io.BytesIO(contenido.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=plantilla_clientes.csv"},
    )


def _dni_desde_cuit(cuit_digits: str) -> int:
    """Extrae el DNI de un CUIT/CUIL de persona física (prefijos 20/23/24/27)."""
    if len(cuit_digits) == 11 and cuit_digits[:2] in ("20", "23", "24", "27"):
        return int(cuit_digits[2:10])
    return 0


def _normalizar_cuit(raw: str) -> str:
    """Quita guiones, puntos y espacios y retorna solo los dígitos del CUIT."""
    return "".join(c for c in raw if c.isdigit())


@router.post("/api/importar_clientes")
async def api_importar_clientes(
    request: Request,
    file: UploadFile = File(...),
    registrar_cbu: str = Form("1"),
):
    err = _api_require_auth(request)
    if err:
        return err

    contents = await file.read()
    filename = (file.filename or "").lower()

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(io.BytesIO(contents), dtype=str, keep_default_na=False)
    except Exception as exc:
        return JSONResponse({"error": f"No se pudo leer el archivo: {exc}"}, status_code=400)

    # Normalizar nombres de columnas
    df.columns = [str(c).strip().lower() for c in df.columns]
    cols = set(df.columns)

    # Aliases para columnas: mapear variantes al nombre canónico
    _alias = {
        "titular":   "nombre",
        "cuit/cuil": "cuit/cuil",  # ya canónico
        "cuit":      "cuit/cuil",
        "cuil":      "cuit/cuil",
        "cvu/cbu":   "cvu/cbu",    # ya canónico
        "cbu":       "cvu/cbu",
        "cvu":       "cvu/cbu",
        "banco":     "banco",
        "nombre":    "nombre",
        "apellido":  "apellido",
        "email":     "email",
        "dni":       "dni",
    }
    rename_map = {col: _alias[col] for col in df.columns if col in _alias and _alias[col] != col}
    if rename_map:
        df = df.rename(columns=rename_map)
    cols = set(df.columns)

    # Detectar formato: nuevo (NOMBRE o TITULAR / CUIT o CUIT/CUIL / CBU o CVU/CBU / BANCO)
    #                   o viejo (nombre / apellido / email / dni / cuit / ...)
    formato_nuevo = "cuit/cuil" in cols or "cvu/cbu" in cols

    if formato_nuevo:
        required_cols = {"nombre", "cuit/cuil", "cvu/cbu"}
    else:
        required_cols = {"nombre", "apellido", "email", "dni", "cuit"}

    missing = required_cols - cols
    if missing:
        encontradas = ", ".join(df.columns.tolist())
        return JSONResponse(
            {"error": f"Columnas faltantes: {', '.join(sorted(missing))}. Encontradas: {encontradas}"},
            status_code=400,
        )

    usar_epagos = registrar_cbu == "1"
    ep = _epagos() if usar_epagos else None

    resultados = []

    for i, row in df.iterrows():
        fila = int(i) + 2  # +1 encabezado +1 base-1

        if formato_nuevo:
            nombre_completo = str(row.get("nombre", "")).strip()
            cuit_raw        = str(row.get("cuit/cuil", "")).strip()
            cbu_raw         = str(row.get("cvu/cbu", "")).strip().replace(" ", "")
            # Manejar representación float de Excel (ej: "1234.0")
            if "." in cbu_raw and not cbu_raw.startswith("0"):
                try:
                    cbu_raw = str(int(float(cbu_raw)))
                except Exception:
                    pass
            cbu   = cbu_raw
            alias = str(row.get("banco", "")).strip()

            if not nombre_completo and not cuit_raw:
                continue

            if not nombre_completo or not cuit_raw:
                resultados.append({
                    "fila": fila, "nombre": nombre_completo,
                    "identificador": "", "estado": "error",
                    "accion_cliente": "", "cbu_estado": "",
                    "error": "NOMBRE y CUIT/CUIL son obligatorios",
                })
                continue

            cuit_digits = _normalizar_cuit(cuit_raw)
            if len(cuit_digits) not in (10, 11):
                resultados.append({
                    "fila": fila, "nombre": nombre_completo,
                    "identificador": "", "estado": "error",
                    "accion_cliente": "", "cbu_estado": "",
                    "error": f"CUIT/CUIL inválido: '{cuit_raw}'",
                })
                continue

            if len(cuit_digits) == 10:
                cuit_digits = cuit_digits.zfill(11)

            cuit       = int(cuit_digits)
            dni        = _dni_desde_cuit(cuit_digits)
            nombre     = ""
            apellido   = nombre_completo
            email      = ""
            id_cliente = f"CLI-{cuit_digits}"

        else:
            nombre     = str(row.get("nombre",   "")).strip()
            apellido   = str(row.get("apellido", "")).strip()
            email      = str(row.get("email",    "")).strip()
            dni_raw    = str(row.get("dni",      "")).strip().rstrip(".0")
            cuit_raw   = str(row.get("cuit",     "")).strip().rstrip(".0")
            id_cliente = str(row.get("identificador_cliente", "")).strip()
            cbu        = str(row.get("cbu",   "")).strip().replace(" ", "").rstrip(".0")
            alias      = str(row.get("alias", "")).strip()

            if not any([nombre, apellido, email, dni_raw, cuit_raw]):
                continue

            if not all([nombre, apellido, email, dni_raw, cuit_raw]):
                resultados.append({
                    "fila": fila, "nombre": f"{apellido}, {nombre}",
                    "identificador": id_cliente, "estado": "error",
                    "accion_cliente": "", "cbu_estado": "",
                    "error": "Campos obligatorios vacíos (nombre, apellido, email, dni, cuit)",
                })
                continue

            try:
                dni  = int(float(dni_raw))
                cuit = int(float(cuit_raw))
            except ValueError:
                resultados.append({
                    "fila": fila, "nombre": f"{apellido}, {nombre}",
                    "identificador": id_cliente, "estado": "error",
                    "accion_cliente": "", "cbu_estado": "",
                    "error": f"DNI '{dni_raw}' o CUIT '{cuit_raw}' no son números válidos",
                })
                continue

            if len(id_cliente) < 6:
                id_cliente = f"CLI-{dni}"

        # ---- Crear o encontrar cliente (sesión corta) ----
        accion_cliente = ""
        cliente_id_db  = None
        id_cliente_ep  = id_cliente
        try:
            with get_session() as db:
                existente = db.query(Cliente).filter(
                    Cliente.identificador_cliente == id_cliente
                ).first()
                if existente:
                    cliente_id_db  = existente.id
                    id_cliente_ep  = existente.identificador_cliente
                    accion_cliente = "ya existía"
                else:
                    por_cuit = db.query(Cliente).filter(Cliente.cuit == cuit).first()
                    if por_cuit:
                        cliente_id_db  = por_cuit.id
                        id_cliente_ep  = por_cuit.identificador_cliente
                        accion_cliente = "ya existía (mismo CUIT)"
                    else:
                        nuevo = Cliente(
                            identificador_cliente=id_cliente,
                            nombre=nombre, apellido=apellido,
                            email=email, dni=dni, cuit=cuit,
                        )
                        db.add(nuevo)
                        db.commit()
                        db.refresh(nuevo)
                        cliente_id_db  = nuevo.id
                        accion_cliente = "creado"
        except Exception as exc_db:
            resultados.append({
                "fila": fila, "nombre": apellido,
                "identificador": id_cliente, "estado": "error",
                "accion_cliente": "", "cbu_estado": "",
                "error": f"Error BD al guardar cliente: {exc_db}",
            })
            continue

        # ---- Registrar CBU (llamada ePagos FUERA de la sesión DB) ----
        cbu_estado = ""
        if cbu and len(cbu) == 22 and cbu.isdigit():
            # Verificar si ya existe (sesión corta de lectura)
            with get_session() as db:
                existe_cbu = db.query(Cuenta).filter(Cuenta.cbu == cbu).first()
                cbu_ya_existe = existe_cbu is not None

            if cbu_ya_existe:
                cbu_estado = "CBU ya registrado"
            else:
                # Llamar ePagos FUERA de cualquier sesión DB
                id_cuenta = f"CBU-{cbu}"
                if ep:
                    try:
                        res_cbu   = ep.registrar_cuenta_cliente(
                            identificador_cliente=id_cliente_ep,
                            cbu=cbu,
                            cuit=cuit,
                        )
                        id_cuenta = res_cbu.get("identificador_cuenta") or id_cuenta
                        cbu_estado = "CBU registrado"
                    except (EpagosError, Exception) as exc_ep:
                        cbu_estado = f"CBU guardado local (ePagos: {exc_ep})"
                else:
                    cbu_estado = "CBU guardado local"

                # Guardar CBU en DB (sesión corta)
                try:
                    with get_session() as db:
                        db.add(Cuenta(
                            cliente_id=cliente_id_db,
                            identificador_cuenta=id_cuenta,
                            alias=alias or f"CBU {cbu[-4:]}",
                            cbu=cbu,
                        ))
                        db.commit()
                except Exception as exc_db2:
                    cbu_estado = f"Error al guardar CBU: {exc_db2}"
        elif cbu and cbu not in ("", "nan", "none"):
            cbu_estado = f"CBU/CVU inválido ({len(cbu)} dígitos)"

        resultados.append({
            "fila": fila,
            "nombre": apellido,
            "identificador": id_cliente,
            "estado": "ok",
            "accion_cliente": accion_cliente,
            "cbu_estado": cbu_estado,
            "error": None,
        })

    total    = len(resultados)
    ok_count = sum(1 for r in resultados if r["estado"] == "ok")
    err_count = total - ok_count

    return {
        "total": total,
        "ok": ok_count,
        "errores": err_count,
        "resultados": resultados,
    }
