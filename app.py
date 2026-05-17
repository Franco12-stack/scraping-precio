"""
Servidor FastAPI para recibir webhooks de ePagos y exponer endpoints de cobro.

Iniciar:
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

import logging
import os
from datetime import date, datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field

from epagos import EpagosClient, PagoEvento

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="ePagos Integration", version="1.0.0")


def get_client() -> EpagosClient:
    return EpagosClient()


# --------------------------------------------------------------------------
# Webhook — ePagos llama a este endpoint cuando acredita/devuelve un pago
# --------------------------------------------------------------------------

class WebhookPayload(BaseModel):
    id_operacion: str
    estado: str
    importe: float
    moneda: str = "ARS"
    identificador_cliente: str = ""
    tipo_operacion: str = ""
    fecha: datetime
    medio_pago: str = ""

    model_config = {"extra": "allow"}


@app.post("/webhook", status_code=status.HTTP_200_OK)
async def recibir_webhook(request: Request, payload: WebhookPayload):
    log.info(
        "Webhook recibido: op=%s estado=%s importe=%s cliente=%s",
        payload.id_operacion,
        payload.estado,
        payload.importe,
        payload.identificador_cliente,
    )

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
    """Lógica de negocio al recibir un evento de ePagos."""
    if evento.estado == "acreditado":
        log.info("Pago acreditado: op=%s cliente=%s", evento.id_operacion, evento.identificador_cliente)
        # TODO: marcar la deuda como paga en tu sistema
    elif evento.estado == "devuelto":
        log.info("Devolución registrada: op=%s", evento.id_operacion)
        # TODO: revertir el pago en tu sistema
    else:
        log.warning("Estado desconocido: %s — op=%s", evento.estado, evento.id_operacion)


# --------------------------------------------------------------------------
# Consulta de pagos
# --------------------------------------------------------------------------

@app.get("/pagos")
def listar_pagos(
    fecha_desde: date,
    fecha_hasta: date,
    tipo_operacion: str | None = None,
):
    client = get_client()
    pagos = client.obtener_pagos(fecha_desde, fecha_hasta, tipo_operacion)
    return {"total": len(pagos), "pagos": [vars(p) for p in pagos]}


@app.get("/rendiciones/{fecha}")
def listar_rendicion(fecha: date):
    client = get_client()
    rendiciones = client.obtener_rendiciones(fecha)
    return {"total": len(rendiciones), "rendiciones": [vars(r) for r in rendiciones]}


# --------------------------------------------------------------------------
# Recurrencia — cuentas
# --------------------------------------------------------------------------

class RegistrarCuentaBody(BaseModel):
    cbu: str
    identificador_cliente: str
    tipo_operacion: str


@app.post("/cuentas")
def registrar_cuenta(body: RegistrarCuentaBody):
    client = get_client()
    cuenta = client.registrar_cuenta_cliente(
        cbu=body.cbu,
        identificador_cliente=body.identificador_cliente,
        tipo_operacion=body.tipo_operacion,
    )
    return vars(cuenta)


@app.get("/cuentas/{identificador_cliente}")
def obtener_cuentas(identificador_cliente: str):
    client = get_client()
    cuentas = client.obtener_cuentas_cliente(identificador_cliente)
    return {"total": len(cuentas), "cuentas": [vars(c) for c in cuentas]}


# --------------------------------------------------------------------------
# Recurrencia — cobros
# --------------------------------------------------------------------------

class PagoRecurrenteBody(BaseModel):
    identificador_cliente: str
    identificador_cuenta: str
    importe: float = Field(gt=0)
    tipo_operacion: str
    descripcion: str | None = None
    referencia_externa: str | None = None


@app.post("/cobros/recurrente")
def cobro_recurrente(body: PagoRecurrenteBody):
    client = get_client()
    resultado = client.solicitud_pago_recurrente(
        identificador_cliente=body.identificador_cliente,
        identificador_cuenta=body.identificador_cuenta,
        importe=body.importe,
        tipo_operacion=body.tipo_operacion,
        descripcion=body.descripcion,
        referencia_externa=body.referencia_externa,
    )
    return vars(resultado)


class PagoSuscripcionBody(PagoRecurrenteBody):
    fecha_cobro: date


@app.post("/cobros/suscripcion")
def cobro_suscripcion(body: PagoSuscripcionBody):
    client = get_client()
    resultado = client.solicitud_pago_recurrente_suscripcion(
        identificador_cliente=body.identificador_cliente,
        identificador_cuenta=body.identificador_cuenta,
        importe=body.importe,
        tipo_operacion=body.tipo_operacion,
        fecha_cobro=body.fecha_cobro,
        descripcion=body.descripcion,
        referencia_externa=body.referencia_externa,
    )
    return vars(resultado)


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}
