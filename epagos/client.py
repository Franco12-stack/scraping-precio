import os
from datetime import date, datetime
from typing import Any, Optional

from zeep import Client, Settings
from zeep.transports import Transport
from requests import Session

WSDL_PRODUCCION = "https://api.epagos.com.ar/wsdl/index.php?wsdl"
WSDL_SANDBOX    = "https://sandbox.epagos.com.ar/wsdl/index.php?wsdl"

API_VERSION = "3.0"

# Valores válidos del enum MedioRecurrente
MEDIO_TARJETA        = "op_pago_recurrente_medio_tarjeta"
MEDIO_DEBIN          = "op_pago_recurrente_medio_debin"
MEDIO_DEBITO_DIRECTO = "op_pago_recurrente_medio_debito_directo"

# Único valor válido de TipoOperacionRec
TIPO_RECURRENTE = "op_pago_recurrente"


class EpagosError(Exception):
    def __init__(self, id_resp: str, mensaje: str):
        super().__init__(f"[{id_resp}] {mensaje}")
        self.id_resp = id_resp
        self.mensaje = mensaje


class EpagosClient:
    """
    Cliente SOAP para la API ePagos v3.

    Flujo de cobro recurrente (débito directo):
      1. El cliente hace un primer pago en el botón de ePagos con fp_permitidas=42
         → ePagos registra el CBU y genera un identificador_cuenta
      2. Llamar a obtener_cuentas_cliente() para obtener el identificador_cuenta
      3. Llamar a solicitud_pago_recurrente() para generar la orden de cobro
         (acreditación en 72 hs hábiles)
    """

    def __init__(
        self,
        id_organismo: Optional[str] = None,
        id_usuario:   Optional[str] = None,
        password:     Optional[str] = None,
        hash_auth:    Optional[str] = None,
        convenio:     Optional[int] = None,
        entorno:      Optional[str] = None,
    ):
        self.id_organismo = id_organismo or os.environ["EPAGOS_ID_ORGANISMO"]
        self.id_usuario   = id_usuario   or os.environ["EPAGOS_ID_USUARIO"]
        self.password     = password     or os.environ["EPAGOS_PASSWORD"]
        self.hash_auth    = hash_auth    or os.environ["EPAGOS_HASH"]
        self.entorno      = (entorno or os.getenv("EPAGOS_ENTORNO", "produccion")).lower()

        _conv = os.getenv("EPAGOS_CONVENIO", "")
        self.convenio = convenio or (int(_conv) if _conv.strip().isdigit() else None)

        wsdl = WSDL_SANDBOX if self.entorno == "sandbox" else WSDL_PRODUCCION
        settings = Settings(strict=False, xml_huge_tree=True)
        self._soap = Client(wsdl, settings=settings, transport=Transport(session=Session()))
        self._token: Optional[str] = None

        # Tipos WSDL cacheados
        self._t = {}

    def _tipo(self, nombre: str):
        if nombre not in self._t:
            self._t[nombre] = self._soap.get_type(f"ns0:{nombre}")
        return self._t[nombre]

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
        self._validar(resp.id_resp, resp.respuesta)
        self._token = str(resp.token)
        return self._token

    def _token_valido(self) -> str:
        if not self._token:
            self.obtener_token()
        return self._token

    def _creds_pago(self) -> dict:
        return {"id_organismo": self.id_organismo, "token": self._token_valido()}

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
        resp = self._soap.service.obtener_pagos(API_VERSION, self._creds_pago(), criterios)
        self._validar(resp.id_resp, resp.respuesta)
        return [self._to_dict(p) for p in (resp.pago or [])]

    def obtener_rendiciones(self, fecha_desde: date, fecha_hasta: date) -> list[dict]:
        criterios = {
            "Fecha_desde": fecha_desde.strftime("%Y-%m-%d"),
            "Fecha_hasta": fecha_hasta.strftime("%Y-%m-%d"),
        }
        resp = self._soap.service.obtener_rendiciones(API_VERSION, self._creds_pago(), criterios)
        self._validar(resp.id_resp, resp.respuesta)
        return [self._to_dict(r) for r in (resp.rendicion or [])]

    # ------------------------------------------------------------------
    # Recurrencia — cuentas bancarias registradas
    # ------------------------------------------------------------------

    def obtener_cuentas_cliente(self, identificador_cliente: str) -> list[dict]:
        """Devuelve las cuentas CBU/CVU registradas para un cliente."""
        resp = self._soap.service.obtener_cuentas_cliente(
            API_VERSION,
            self._creds_pago(),
            [{"identificador_cliente": identificador_cliente}],
        )
        self._validar(resp.id_resp, resp.respuesta)
        cuentas = []
        for c in (resp.cuentas or []):
            for cuenta in (c.cuentas or []):
                cuentas.append(self._to_dict(cuenta))
        return cuentas

    # ------------------------------------------------------------------
    # Recurrencia — cobros por débito directo
    # ------------------------------------------------------------------

    def _build_operacion(
        self,
        numero_operacion: str,
        importe: float,
        descripcion: str,
        nombre_pagador:   str,
        apellido_pagador: str,
        email_pagador:    str,
        dni_pagador:      int,
        cuit_pagador:     int,
        fecha_vencimiento: Optional[date] = None,
        identificador_externo_2: str = "",
        identificador_externo_3: str = "",
    ):
        DetallePago      = self._tipo("DetallePago")
        IdentPagador     = self._tipo("IdentificacionPagador")
        DomicilioPagador = self._tipo("DomicilioPagador")
        TelefonoPagador  = self._tipo("TelefonoPagador")
        DatosPagador     = self._tipo("DatosPagadorPago")
        DatosOperacion   = self._tipo("DatosOperacionPago")

        pagador = DatosPagador(
            nombre_pagador         = nombre_pagador,
            apellido_pagador       = apellido_pagador,
            fechanac_pagador       = date(1900, 1, 1),
            email_pagador          = email_pagador,
            identificacion_pagador = IdentPagador(
                tipo_doc_pagador   = 96,         # 96 = DNI
                numero_doc_pagador = dni_pagador,
                cuit_doc_pagador   = cuit_pagador,
            ),
            domicilio_pagador = DomicilioPagador(
                calle_dom_pagador     = "",
                numero_dom_pagador    = "",
                adicional_dom_pagador = "",
                cp_dom_pagador        = "",
                ciudad_dom_pagador    = "",
                provincia_dom_pagador = 1,
                pais_dom_pagador      = 54,
            ),
            telefono_pagador = TelefonoPagador(
                codigo_telef_pagador = 0,
                numero_telef_pagador = 0,
            ),
            cbu_pagador = "",
        )

        return DatosOperacion(
            numero_operacion         = numero_operacion,
            identificador_externo_2  = identificador_externo_2,
            identificador_externo_3  = identificador_externo_3,
            id_moneda_operacion      = 1,          # 1 = ARS
            monto_operacion          = float(importe),
            opc_pdf                  = False,
            opc_fecha_vencimiento    = fecha_vencimiento or date(2099, 12, 31),
            opc_devolver_qr          = False,
            opc_devolver_codbarras   = False,
            detalle_operacion        = [DetallePago(
                id_item     = 1,
                desc_item   = descripcion or "Cobro recurrente",
                monto_item  = float(importe),
                cantidad_item = 1,
            )],
            pagador = pagador,
        )

    def solicitud_pago_recurrente(
        self,
        identificador_cliente:  str,
        identificador_cuenta:   str,
        importe:                float,
        numero_operacion:       str,
        nombre_pagador:         str,
        apellido_pagador:       str,
        email_pagador:          str,
        dni_pagador:            int,
        cuit_pagador:           int,
        descripcion:            str = "Cobro recurrente",
        convenio:               Optional[int] = None,
        medio:                  str = MEDIO_DEBITO_DIRECTO,
    ) -> dict:
        """
        Genera una orden de cobro por débito directo.
        La acreditación demora hasta 72 hs hábiles.
        Requiere que el cliente ya tenga una cuenta CBU/CVU registrada en ePagos.
        """
        conv = convenio or self.convenio
        if not conv:
            raise ValueError("CONVENIO es obligatorio. Completá EPAGOS_CONVENIO en .env")

        operacion   = self._build_operacion(numero_operacion, importe, descripcion,
                                            nombre_pagador, apellido_pagador,
                                            email_pagador, dni_pagador, cuit_pagador)
        SuscCliente = self._tipo("SuscripcionCliente")
        cliente_sus = SuscCliente(
            identificador_cliente = identificador_cliente,
            identificador_tarjeta = "",
            identificador_cuenta  = identificador_cuenta,
        )

        resp = self._soap.service.solicitud_pago_recurrente(
            API_VERSION, TIPO_RECURRENTE, self._creds_pago(),
            operacion, conv, medio, cliente_sus,
        )
        self._validar(resp.id_resp, resp.respuesta)
        return {
            "id_transaccion":  resp.id_transaccion,
            "numero_operacion": resp.numero_operacion,
        }

    def solicitud_pago_recurrente_suscripcion(
        self,
        identificador_cliente: str,
        identificador_cuenta:  str,
        importe:               float,
        numero_operacion:      str,
        fecha_cobro:           date,
        nombre_pagador:        str,
        apellido_pagador:      str,
        email_pagador:         str,
        dni_pagador:           int,
        cuit_pagador:          int,
        descripcion:           str = "Cobro recurrente",
        modalidad:             str = "U",   # "U" = única, "P" = periódica
        convenio:              Optional[int] = None,
        medio:                 str = MEDIO_DEBITO_DIRECTO,
    ) -> dict:
        """
        Programa un cobro para una fecha futura (se ejecuta en background al vencer).
        """
        conv = convenio or self.convenio
        if not conv:
            raise ValueError("CONVENIO es obligatorio. Completá EPAGOS_CONVENIO en .env")

        operacion   = self._build_operacion(numero_operacion, importe, descripcion,
                                            nombre_pagador, apellido_pagador,
                                            email_pagador, dni_pagador, cuit_pagador)
        ArraySusc   = [{"fecha_cobro": fecha_cobro.strftime("%Y-%m-%d")}]
        SuscCliente = self._tipo("SuscripcionCliente")
        cliente_sus = SuscCliente(
            identificador_cliente = identificador_cliente,
            identificador_tarjeta = "",
            identificador_cuenta  = identificador_cuenta,
        )

        resp = self._soap.service.solicitud_pago_recurrente_suscripcion(
            API_VERSION, TIPO_RECURRENTE, self._creds_pago(),
            operacion, ArraySusc, modalidad, descripcion,
            conv, medio, [cliente_sus],
        )
        self._validar(resp.id_resp, resp.respuesta)
        return {"id_resp": resp.id_resp, "respuesta": resp.respuesta}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _ERROR_PREFIXES = ("02", "03")

    @classmethod
    def _validar(cls, id_resp: Any, respuesta: Any) -> None:
        id_str = str(id_resp).strip()
        msg    = str(respuesta).lower()
        if any(id_str.startswith(p) for p in cls._ERROR_PREFIXES) or "error" in msg:
            raise EpagosError(id_str, str(respuesta))

    @staticmethod
    def _to_dict(obj: Any) -> dict | Any:
        if hasattr(obj, "__values__"):
            return {k: EpagosClient._to_dict(v) for k, v in obj.__values__.items()}
        if isinstance(obj, list):
            return [EpagosClient._to_dict(i) for i in obj]
        if isinstance(obj, datetime):
            return obj.isoformat()
        return obj
