import os
from datetime import date, datetime
from typing import Optional

from zeep import Client
from zeep.transports import Transport
from requests import Session
from requests.auth import HTTPBasicAuth

from .models import CuentaCliente, Rendicion, ResultadoPago

# Reemplazar con la URL real del WSDL provista por ePagos
WSDL_URL = os.getenv("EPAGOS_WSDL_URL", "https://www.epagos.com.ar/api/wsdl")


class EpagosClient:
    """Cliente SOAP para la API de ePagos."""

    def __init__(
        self,
        usuario: Optional[str] = None,
        password: Optional[str] = None,
        organismo: Optional[str] = None,
        wsdl_url: Optional[str] = None,
    ):
        self.usuario = usuario or os.environ["EPAGOS_USUARIO"]
        self.password = password or os.environ["EPAGOS_PASSWORD"]
        self.organismo = organismo or os.environ["EPAGOS_ORGANISMO"]
        self._wsdl = wsdl_url or WSDL_URL
        self._client = self._build_client()

    def _build_client(self) -> Client:
        session = Session()
        session.auth = HTTPBasicAuth(self.usuario, self.password)
        transport = Transport(session=session)
        return Client(self._wsdl, transport=transport)

    def _credenciales(self) -> dict:
        return {
            "usuario": self.usuario,
            "password": self.password,
            "organismo": self.organismo,
        }

    # ------------------------------------------------------------------
    # Consulta de pagos
    # ------------------------------------------------------------------

    def obtener_pagos(
        self,
        fecha_desde: date,
        fecha_hasta: date,
        tipo_operacion: Optional[str] = None,
    ) -> list[ResultadoPago]:
        """Devuelve operaciones realizadas en el rango de fechas indicado."""
        params = {
            **self._credenciales(),
            "fecha_desde": fecha_desde.isoformat(),
            "fecha_hasta": fecha_hasta.isoformat(),
        }
        if tipo_operacion:
            params["tipo_operacion"] = tipo_operacion

        respuesta = self._client.service.obtener_pagos(**params)
        return [self._parsear_pago(item) for item in (respuesta or [])]

    def obtener_rendiciones(self, fecha: date) -> list[Rendicion]:
        """Devuelve la rendición diaria de la fecha indicada."""
        params = {
            **self._credenciales(),
            "fecha": fecha.isoformat(),
        }
        respuesta = self._client.service.obtener_rendiciones(**params)
        return [self._parsear_rendicion(item) for item in (respuesta or [])]

    # ------------------------------------------------------------------
    # Recurrencia — gestión de cuentas
    # ------------------------------------------------------------------

    def registrar_cuenta_cliente(
        self,
        cbu: str,
        identificador_cliente: str,
        tipo_operacion: str,
    ) -> CuentaCliente:
        """Migra/registra una cuenta bancaria para cobro recurrente."""
        params = {
            **self._credenciales(),
            "cbu": cbu,
            "identificador_cliente": identificador_cliente,
            "tipo_operacion": tipo_operacion,
        }
        respuesta = self._client.service.registrar_cuentas_cliente(**params)
        return CuentaCliente(
            identificador_cuenta=respuesta.identificador_cuenta,
            identificador_cliente=identificador_cliente,
            cbu=cbu,
            tipo_operacion=tipo_operacion,
        )

    def obtener_cuentas_cliente(
        self, identificador_cliente: str
    ) -> list[CuentaCliente]:
        """Devuelve las cuentas bancarias guardadas para un cliente."""
        params = {
            **self._credenciales(),
            "identificador_cliente": identificador_cliente,
        }
        respuesta = self._client.service.obtener_cuentas_cliente(**params)
        return [
            CuentaCliente(
                identificador_cuenta=c.identificador_cuenta,
                identificador_cliente=identificador_cliente,
                cbu=c.cbu,
                tipo_operacion=c.tipo_operacion,
                activa=getattr(c, "activa", True),
            )
            for c in (respuesta or [])
        ]

    # ------------------------------------------------------------------
    # Recurrencia — cobros
    # ------------------------------------------------------------------

    def solicitud_pago_recurrente(
        self,
        identificador_cliente: str,
        identificador_cuenta: str,
        importe: float,
        tipo_operacion: str,
        descripcion: Optional[str] = None,
        referencia_externa: Optional[str] = None,
    ) -> ResultadoPago:
        """
        Genera una orden de cobro por débito directo.
        La acreditación demora hasta 72 hs hábiles.
        """
        params = {
            **self._credenciales(),
            "identificador_cliente": identificador_cliente,
            "identificador_cuenta": identificador_cuenta,
            "importe": importe,
            "tipo_operacion": tipo_operacion,
        }
        if descripcion:
            params["descripcion"] = descripcion
        if referencia_externa:
            params["referencia_externa"] = referencia_externa

        respuesta = self._client.service.solicitud_pago_recurrente(**params)
        return self._parsear_pago(respuesta)

    def solicitud_pago_recurrente_suscripcion(
        self,
        identificador_cliente: str,
        identificador_cuenta: str,
        importe: float,
        tipo_operacion: str,
        fecha_cobro: date,
        descripcion: Optional[str] = None,
        referencia_externa: Optional[str] = None,
    ) -> ResultadoPago:
        """
        Programa un cobro planificado que se ejecuta al llegar la fecha indicada.
        """
        params = {
            **self._credenciales(),
            "identificador_cliente": identificador_cliente,
            "identificador_cuenta": identificador_cuenta,
            "importe": importe,
            "tipo_operacion": tipo_operacion,
            "fecha_cobro": fecha_cobro.isoformat(),
        }
        if descripcion:
            params["descripcion"] = descripcion
        if referencia_externa:
            params["referencia_externa"] = referencia_externa

        respuesta = self._client.service.solicitud_pago_recurrente_suscripcion(**params)
        return self._parsear_pago(respuesta)

    # ------------------------------------------------------------------
    # Helpers de parseo
    # ------------------------------------------------------------------

    @staticmethod
    def _parsear_pago(item) -> ResultadoPago:
        return ResultadoPago(
            id_operacion=str(item.id_operacion),
            estado=str(item.estado),
            importe=float(item.importe),
            moneda=str(getattr(item, "moneda", "ARS")),
            identificador_cliente=str(getattr(item, "identificador_cliente", "")),
            tipo_operacion=str(getattr(item, "tipo_operacion", "")),
            fecha=datetime.fromisoformat(str(item.fecha)),
            medio_pago=str(getattr(item, "medio_pago", "") or ""),
            descripcion=str(getattr(item, "descripcion", "") or ""),
        )

    @staticmethod
    def _parsear_rendicion(item) -> Rendicion:
        return Rendicion(
            id_operacion=str(item.id_operacion),
            fecha_acreditacion=datetime.fromisoformat(str(item.fecha_acreditacion)),
            importe=float(item.importe),
            moneda=str(getattr(item, "moneda", "ARS")),
            identificador_cliente=str(getattr(item, "identificador_cliente", "")),
            tipo_operacion=str(getattr(item, "tipo_operacion", "")),
            medio_pago=str(getattr(item, "medio_pago", "")),
            comision=float(getattr(item, "comision", 0.0)),
        )
