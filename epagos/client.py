import os
from datetime import date, datetime
from typing import Any, Optional

from zeep import Client, Settings
from zeep.transports import Transport
from requests import Session

WSDL_PRODUCCION = "https://api.epagos.com.ar/wsdl/index.php?wsdl"
WSDL_SANDBOX    = "https://sandbox.epagos.com.ar/wsdl/index.php?wsdl"

API_VERSION = "3.0"


class EpagosError(Exception):
    def __init__(self, id_resp: str, mensaje: str):
        super().__init__(f"[{id_resp}] {mensaje}")
        self.id_resp = id_resp
        self.mensaje = mensaje


class EpagosClient:
    """
    Cliente SOAP para la API ePagos v3.
    Flujo: obtener_token() → usar token en llamadas posteriores.
    """

    def __init__(
        self,
        id_organismo: Optional[str] = None,
        id_usuario: Optional[str] = None,
        password: Optional[str] = None,
        hash_auth: Optional[str] = None,
        convenio: Optional[int] = None,
        entorno: Optional[str] = None,
    ):
        self.id_organismo = id_organismo or os.environ["EPAGOS_ID_ORGANISMO"]
        self.id_usuario   = id_usuario   or os.environ["EPAGOS_ID_USUARIO"]
        self.password     = password     or os.environ["EPAGOS_PASSWORD"]
        self.hash_auth    = hash_auth    or os.environ["EPAGOS_HASH"]
        _conv = os.getenv("EPAGOS_CONVENIO", "")
        self.convenio = convenio or (int(_conv) if _conv.strip().isdigit() else None)
        self.entorno      = (entorno or os.getenv("EPAGOS_ENTORNO", "sandbox")).lower()

        wsdl = WSDL_SANDBOX if self.entorno == "sandbox" else WSDL_PRODUCCION
        settings = Settings(strict=False, xml_huge_tree=True)
        self._soap = Client(wsdl, settings=settings, transport=Transport(session=Session()))
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Token
    # ------------------------------------------------------------------

    def obtener_token(self) -> str:
        credenciales = {
            "id_usuario":   self.id_usuario,
            "id_organismo": self.id_organismo,
            "password":     self.password,
            "hash":         self.hash_auth,
        }
        resp = self._soap.service.obtener_token(API_VERSION, credenciales)
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        self._token = str(resp.token)
        return self._token

    def _token_valido(self) -> str:
        if not self._token:
            self.obtener_token()
        return self._token

    def _credenciales_pago(self) -> dict:
        return {
            "id_organismo": self.id_organismo,
            "token":        self._token_valido(),
        }

    # ------------------------------------------------------------------
    # Consulta de pagos
    # ------------------------------------------------------------------

    def obtener_pagos(
        self,
        fecha_desde: date,
        fecha_hasta: date,
        estado: str = "A",
        pagina: int = 1,
    ) -> list[dict]:
        criterios = {
            "Estado":                        estado,
            "FechaNovedadAcreditacionDesde": fecha_desde.strftime("%Y-%m-%d"),
            "FechaNovedadAcreditacionHasta": fecha_hasta.strftime("%Y-%m-%d"),
            "pagina":                        pagina,
        }
        resp = self._soap.service.obtener_pagos(
            API_VERSION, self._credenciales_pago(), criterios
        )
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        pagos = resp.pago or []
        return [self._zeep_a_dict(p) for p in pagos]

    def obtener_rendiciones(
        self,
        fecha_desde: date,
        fecha_hasta: date,
    ) -> list[dict]:
        criterios = {
            "Fecha_desde": fecha_desde.strftime("%Y-%m-%d"),
            "Fecha_hasta": fecha_hasta.strftime("%Y-%m-%d"),
        }
        resp = self._soap.service.obtener_rendiciones(
            API_VERSION, self._credenciales_pago(), criterios
        )
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        rendiciones = resp.rendicion or []
        return [self._zeep_a_dict(r) for r in rendiciones]

    # ------------------------------------------------------------------
    # Recurrencia — gestión de cuentas bancarias (CBU/CVU)
    # ------------------------------------------------------------------

    def obtener_cuentas_cliente(
        self,
        identificador_cliente: str,
        tipo_operacion: Optional[str] = None,
    ) -> list[dict]:
        datos_cliente = {
            "identificador_cliente": identificador_cliente,
        }
        if tipo_operacion:
            datos_cliente["tipo_operacion"] = tipo_operacion

        resp = self._soap.service.obtener_cuentas_cliente(
            API_VERSION, self._credenciales_pago(), [datos_cliente]
        )
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        cuentas = resp.cuentas or []
        return [self._zeep_a_dict(c) for c in cuentas]

    # ------------------------------------------------------------------
    # Recurrencia — cobros por débito directo
    # ------------------------------------------------------------------

    def solicitud_pago_recurrente(
        self,
        tipo_operacion: str,
        identificador_cliente: str,
        identificador_cuenta: str,
        importe: float,
        numero_operacion: str,
        descripcion: str = "",
        medio: str = "CUENTA",   # "CUENTA" = débito directo / "TARJETA" = tarjeta guardada
    ) -> dict:
        """
        Genera una orden de cobro por débito directo.
        La acreditación demora hasta 72 hs hábiles.
        """
        operacion = {
            "monto":            importe,
            "numero_operacion": numero_operacion,
            "id_moneda":        1,   # 1 = ARS
        }
        cliente = {
            "identificador_cliente": identificador_cliente,
            "identificador_cuenta":  identificador_cuenta,
        }
        resp = self._soap.service.solicitud_pago_recurrente(
            API_VERSION,
            tipo_operacion,
            self._credenciales_pago(),
            operacion,
            self.convenio,
            medio,
            cliente,
        )
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        return {
            "id_transaccion":  resp.id_transaccion,
            "numero_operacion": resp.numero_operacion,
            "token":           resp.token,
        }

    def solicitud_pago_recurrente_suscripcion(
        self,
        tipo_operacion: str,
        identificador_cliente: str,
        identificador_cuenta: str,
        importe: float,
        numero_operacion: str,
        fecha_cobro: date,
        descripcion: str = "",
        modalidad: str = "U",   # "U" = única, "P" = periódica
        medio: str = "CUENTA",
    ) -> dict:
        """
        Programa un cobro para una fecha futura (se ejecuta en background al llegar la fecha).
        """
        operacion = {
            "monto":            importe,
            "numero_operacion": numero_operacion,
            "id_moneda":        1,
        }
        suscripcion = [{
            "fecha_cobro": fecha_cobro.strftime("%Y-%m-%d"),
        }]
        cliente = [{
            "identificador_cliente": identificador_cliente,
            "identificador_cuenta":  identificador_cuenta,
        }]
        resp = self._soap.service.solicitud_pago_recurrente_suscripcion(
            API_VERSION,
            tipo_operacion,
            self._credenciales_pago(),
            operacion,
            suscripcion,
            modalidad,
            descripcion,
            self.convenio,
            medio,
            cliente,
        )
        self._validar_respuesta(resp.id_resp, resp.respuesta)
        return {"id_resp": resp.id_resp, "respuesta": resp.respuesta}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Códigos de éxito conocidos: 01001 (token), 4001 (pagos), 5001 (rendiciones), etc.
    # Los códigos de error tienen dígito final par (01002, 01004...) o empiezan con "02".
    _ERROR_PREFIXES = ("02", "03")

    @classmethod
    def _validar_respuesta(cls, id_resp: Any, respuesta: Any) -> None:
        id_str = str(id_resp).strip()
        msg = str(respuesta).lower()
        if any(id_str.startswith(p) for p in cls._ERROR_PREFIXES) or "error" in msg:
            raise EpagosError(id_str, str(respuesta))

    @staticmethod
    def _zeep_a_dict(obj: Any) -> dict:
        if hasattr(obj, "__values__"):
            return {k: EpagosClient._zeep_a_dict(v) for k, v in obj.__values__.items()}
        if isinstance(obj, list):
            return [EpagosClient._zeep_a_dict(i) for i in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj
