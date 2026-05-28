"""
Servidor FastAPI — integración ePagos cobros recurrentes.

Iniciar:
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
from datetime import date, datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from epagos import EpagosClient, EpagosError, PagoEvento
from db import init_db
from dashboard import router as dashboard_router, _seed_admin

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="ePagos - Cobros Recurrentes", version="1.0.0")

# Inicializar BD y montar dashboard
init_db()
_seed_admin()
app.include_router(dashboard_router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


def get_client() -> EpagosClient:
    return EpagosClient()


# --------------------------------------------------------------------------
# Webhook — ePagos notifica acreditaciones y devoluciones en tiempo real
# --------------------------------------------------------------------------

class WebhookPayload(BaseModel):
    id_operacion: str
    estado: str                  # "acreditado" | "devuelto" | "pendiente"
    importe: float
    moneda: str = "ARS"
    identificador_cliente: str = ""
    tipo_operacion: str = ""
    fecha: datetime
    medio_pago: str = ""

    model_config = {"extra": "allow"}


@app.post("/webhook", status_code=status.HTTP_200_OK)
async def recibir_webhook(request: Request, payload: WebhookPayload):
    log.info("Webhook op=%s estado=%s importe=%.2f cliente=%s",
             payload.id_operacion, payload.estado,
             payload.importe, payload.identificador_cliente)

    evento = PagoEvento(
        id_operacion=payload.id_operacion,
        estado=payload.estado,
        importe=payload.importe,
        moneda=payload.moneda,
        identificador_cliente=payload.identificador_cliente,
        tipo_operacion=payload.tipo_operacion,
        fecha=payload.fecha,
        medio_pago=payload.medio_pago,
        datos_extra=payload.model_extra or {},
    )
    await procesar_evento(evento)
    return {"recibido": True}


async def procesar_evento(evento: PagoEvento) -> None:
    if evento.estado == "acreditado":
        log.info("PAGO ACREDITADO op=%s cliente=%s importe=%.2f",
                 evento.id_operacion, evento.identificador_cliente, evento.importe)
        # TODO: marcar la deuda como paga en tu sistema
    elif evento.estado == "devuelto":
        log.info("DEVOLUCION op=%s", evento.id_operacion)
        # TODO: revertir el pago en tu sistema
    else:
        log.warning("Estado desconocido '%s' op=%s", evento.estado, evento.id_operacion)


# --------------------------------------------------------------------------
# Consulta de pagos y rendiciones
# --------------------------------------------------------------------------

@app.get("/pagos")
def listar_pagos(fecha_desde: date, fecha_hasta: date, estado: str = "A"):
    client = get_client()
    pagos = client.obtener_pagos(fecha_desde, fecha_hasta, estado)
    return {"total": len(pagos), "pagos": pagos}


@app.get("/rendiciones")
def listar_rendiciones(fecha_desde: date, fecha_hasta: date):
    client = get_client()
    rendiciones = client.obtener_rendiciones(fecha_desde, fecha_hasta)
    return {"total": len(rendiciones), "rendiciones": rendiciones}


# --------------------------------------------------------------------------
# Recurrencia — consultar cuentas registradas de un cliente
# --------------------------------------------------------------------------

@app.get("/cuentas/{identificador_cliente}")
def obtener_cuentas(identificador_cliente: str):
    """
    Devuelve las cuentas CBU/CVU registradas para el cliente.
    El cliente debe haber completado un pago con débito directo previamente
    para que su CBU quede guardado en ePagos.
    """
    client = get_client()
    cuentas = client.obtener_cuentas_cliente(identificador_cliente)
    return {"total": len(cuentas), "cuentas": cuentas}


@app.get("/tarjetas/{identificador_cliente}")
def obtener_tarjetas(identificador_cliente: str):
    """
    Devuelve las tarjetas registradas para el cliente.
    El cliente debe haber completado un pago con tarjeta previamente.
    """
    client = get_client()
    tarjetas = client.obtener_tarjetas_cliente(identificador_cliente)
    return {"total": len(tarjetas), "tarjetas": tarjetas}


# --------------------------------------------------------------------------
# Recurrencia — generar cobro
# --------------------------------------------------------------------------

class PagadorBase(BaseModel):
    nombre_pagador:   str
    apellido_pagador: str
    email_pagador:    str
    dni_pagador:      int
    cuit_pagador:     int


class PagoRecurrenteBody(PagadorBase):
    identificador_cliente: str   # ID único del cliente en tu sistema
    identificador_cuenta:  str   # ID de la cuenta CBU (obtenido de /cuentas/{cliente})
    importe:               float = Field(gt=0, description="Importe en ARS")
    numero_operacion:      str   = Field(description="ID único de esta operación en tu sistema")
    descripcion:           str   = "Cobro recurrente"
    convenio:              int | None = None


@app.post("/cobros/recurrente")
def cobro_recurrente(body: PagoRecurrenteBody):
    """
    Genera una orden de cobro por débito directo.
    Acreditación en hasta 72 hs hábiles.
    """
    client = get_client()
    resultado = client.solicitud_pago_recurrente(
        identificador_cliente = body.identificador_cliente,
        identificador_cuenta  = body.identificador_cuenta,
        importe               = body.importe,
        numero_operacion      = body.numero_operacion,
        nombre_pagador        = body.nombre_pagador,
        apellido_pagador      = body.apellido_pagador,
        email_pagador         = body.email_pagador,
        dni_pagador           = body.dni_pagador,
        cuit_pagador          = body.cuit_pagador,
        descripcion           = body.descripcion,
        convenio              = body.convenio,
    )
    return resultado


class PagoSuscripcionBody(PagoRecurrenteBody):
    fecha_cobro: date   = Field(description="Fecha en que se ejecutará el débito")
    modalidad:   str    = Field(default="U", description="U=única, P=periódica")


@app.post("/cobros/suscripcion")
def cobro_suscripcion(body: PagoSuscripcionBody):
    """
    Programa un cobro para una fecha futura.
    Se ejecuta automáticamente en background al llegar la fecha.
    """
    client = get_client()
    resultado = client.solicitud_pago_recurrente_suscripcion(
        identificador_cliente = body.identificador_cliente,
        identificador_cuenta  = body.identificador_cuenta,
        importe               = body.importe,
        numero_operacion      = body.numero_operacion,
        fecha_cobro           = body.fecha_cobro,
        nombre_pagador        = body.nombre_pagador,
        apellido_pagador      = body.apellido_pagador,
        email_pagador         = body.email_pagador,
        dni_pagador           = body.dni_pagador,
        cuit_pagador          = body.cuit_pagador,
        descripcion           = body.descripcion,
        modalidad             = body.modalidad,
        convenio              = body.convenio,
    )
    return resultado


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "entorno": os.getenv("EPAGOS_ENTORNO", "produccion")}
