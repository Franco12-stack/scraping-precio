import os
from datetime import date, datetime
from typing import Any, Optional

from zeep import Client, Settings
from zeep.transports import Transport
from requests import Session

WSDL_PRODUCCION = "https://api.epagos.com.ar/wsdl/index.php?wsdl"
WSDL_SANDBOX    = "https://sandbox.epagos.com.ar/wsdl/index.php?wsdl"

# v2.1 endpoints — tienen registrar_cuentas_cliente que v3.0 no expone
WSDL_PRODUCCION_V2 = "https://api.epagos.com/wsdl/2.1/index.php?wsdl"
WSDL_SANDBOX_V2    = "https://sandbox.epagos.com/wsdl/2.1/index.php?wsdl"

API_VERSION    = "3.0"
API_VERSION_V2 = "2.0"

# Valores válidos del enum MedioRecurrente
MEDIO_TARJETA        = "op_pago_recurrente_medio_tarjeta"
MEDIO_DEBIN          = "op_pago_recurrente_medio_debin"
MEDIO_DEBITO_DIRECTO = "op_pago_recurrente_medio_debito_directo"

# Único valor válido de TipoOperacionRec
TIPO_RECURRENTE = "op_pago_recurrente"

# Códigos que devuelve respuesta_entidad_cobro cuando el débito es rechazado
RECHAZO_ENTIDAD = {
    "cc_rejected_blacklist":         "Tarjeta en lista negra",
    "cc_rejected_card_disabled":     "Tarjeta deshabilitada",
    "cc_rejected_bad_filled_date":   "Fecha de vencimiento incorrecta",
    "cc_rejected_bad_filled_other":  "Datos incorrectos",
    "cc_rejected_bad_filled_security_code": "Código de seguridad incorrecto",
    "cc_rejected_call_for_authorize":"Requiere autorización telefónica",
    "cc_rejected_card_error":        "Error de tarjeta",
    "cc_rejected_duplicated_payment":"Pago duplicado",
    "cc_rejected_high_risk":         "Rechazado por riesgo",
    "cc_rejected_insufficient_amount":"Saldo insuficiente",
    "cc_rejected_invalid_installments":"Cuotas inválidas",
    "cc_rejected_max_attempts":      "Máximo de intentos alcanzado",
    "cc_rejected_other_reason":      "Rechazado por otra razón",
}


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
        self._token:    Optional[str] = None
        self._soap_v2:  Optional[Any] = None   # lazy — solo para registrar_cuentas_cliente
        self._token_v2: Optional[str] = None   # token independiente para v2.1

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

    def _v2(self):
        """Cliente SOAP v2.1 (carga perezosa). Necesario para registrar_cuentas_cliente."""
        if self._soap_v2 is None:
            wsdl = WSDL_SANDBOX_V2 if self.entorno == "sandbox" else WSDL_PRODUCCION_V2
            settings = Settings(strict=False, xml_huge_tree=True)
            self._soap_v2 = Client(wsdl, settings=settings, transport=Transport(session=Session()))
        return self._soap_v2

    def _tipo_v2(self, nombre: str):
        """Equivalente a _tipo() pero sobre el cliente v2.1."""
        key = f"v2:{nombre}"
        if key not in self._t:
            self._t[key] = self._v2().get_type(f"ns0:{nombre}")
        return self._t[key]

    def _token_valido_v2(self) -> str:
        """Obtiene (o reutiliza) un token autenticado contra el endpoint v2.1."""
        if not self._token_v2:
            creds = {
                "id_usuario":   self.id_usuario,
                "id_organismo": self.id_organismo,
                "password":     self.password,
                "hash":         self.hash_auth,
            }
            resp = self._v2().service.obtener_token(API_VERSION_V2, creds)
            self._validar(resp.id_resp, resp.respuesta)
            self._token_v2 = str(resp.token)
        return self._token_v2

    def registrar_cuenta_cliente(
        self,
        identificador_cliente: str,
        cbu: str,
        cuit: int = 0,
        tipo_operacion: int = 1,
    ) -> dict:
        """
        Registra un CBU directamente para un cliente (sin adhesión web).
        Usa la API v2.1 que expone registrar_cuentas_cliente.
        Retorna {"identificador_cuenta": "..."} asignado por ePagos.
        """
        CuentaAgregar = self._tipo_v2("CuentaClienteAgregar")
        ArrayCuentas  = self._tipo_v2("ArrayCuentasClienteAgregar")

        cuenta = CuentaAgregar(
            identificador_cliente = identificador_cliente,
            tipo_operacion        = tipo_operacion,
            cuit                  = cuit,
            cbu                   = cbu,
            fecha_adhesion        = date.today(),
        )

        resp = self._v2().service.registrar_cuentas_cliente(
            API_VERSION_V2,
            {"id_organismo": self.id_organismo, "token": self._token_valido_v2()},
            ArrayCuentas([cuenta]),
        )
        self._validar(resp.id_resp, resp.respuesta)
        # Extraer identificador_cuenta de ArrayCuentasClienteGeneradas
        for item in (resp.cuentas or []):
            id_cuenta = getattr(item, "identificador_cuenta", None) or (
                item.get("identificador_cuenta") if isinstance(item, dict) else None
            )
            if id_cuenta:
                return {"identificador_cuenta": str(id_cuenta)}
        return {"identificador_cuenta": ""}

    def obtener_tarjetas_cliente(self, identificador_cliente: str) -> list[dict]:
        """Devuelve las tarjetas de crédito/débito registradas para un cliente."""
        resp = self._soap.service.obtener_tarjetas_cliente(
            API_VERSION,
            self._creds_pago(),
            [{"identificador_cliente": identificador_cliente, "identificador_tarjeta": ""}],
        )
        self._validar(resp.id_resp, resp.respuesta)
        tarjetas = []
        for t in (resp.tarjetas or []):
            for tarjeta in (t.tarjetas or []):
                tarjetas.append(self._to_dict(tarjeta))
        return tarjetas

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
        if len(identificador_cliente) < 6:
            raise ValueError("identificador_cliente debe tener al menos 6 caracteres")
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
        if len(identificador_cliente) < 6:
            raise ValueError("identificador_cliente debe tener al menos 6 caracteres")
        conv = convenio or self.convenio
        if not conv:
            raise ValueError("CONVENIO es obligatorio. Completá EPAGOS_CONVENIO en .env")

        operacion   = self._build_operacion(numero_operacion, importe, descripcion,
                                            nombre_pagador, apellido_pagador,
                                            email_pagador, dni_pagador, cuit_pagador)
        # DatosSuscripcion fields: fecha (date), monto (float)
        ArraySusc   = [{"fecha": fecha_cobro, "monto": float(importe)}]
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
    # Link de adhesión (primera vez que el cliente registra su CBU)
    # ------------------------------------------------------------------

    def generar_link_adhesion(
        self,
        identificador_cliente: str,
        email_pagador:         str,
        importe:               float,
        descripcion:           str = "Adhesión débito directo",
        url_ok:                str = "",
        url_error:             str = "",
    ) -> str:
        """
        Genera un link de pago restringido a débito directo (fp=42).
        El cliente ingresa su CBU y queda adherido en ePagos.
        Firma real: solicitud_pago(version, tipo_op, credenciales, operacion, fp[], convenio)
        """
        import uuid
        nop = str(uuid.uuid4()).replace("-", "")[:20].upper()

        DetallePago  = self._tipo("DetallePago")
        IdentPag     = self._tipo("IdentificacionPagador")
        DomPag       = self._tipo("DomicilioPagador")
        TelPag       = self._tipo("TelefonoPagador")
        DatosPag     = self._tipo("DatosPagadorPago")
        DatosOp      = self._tipo("DatosOperacionPago")
        FormaPago    = self._tipo("DatosFormaPago")
        FpArray      = self._tipo("DatosFormaPagoArray")

        pagador = DatosPag(
            nombre_pagador         = "Cliente",
            apellido_pagador       = "Adhesion",
            fechanac_pagador       = date(1900, 1, 1),
            email_pagador          = email_pagador,
            identificacion_pagador = IdentPag(tipo_doc_pagador=96, numero_doc_pagador=0, cuit_doc_pagador=0),
            domicilio_pagador      = DomPag(calle_dom_pagador="", numero_dom_pagador="",
                                            adicional_dom_pagador="", cp_dom_pagador="",
                                            ciudad_dom_pagador="", provincia_dom_pagador=1, pais_dom_pagador=54),
            telefono_pagador       = TelPag(codigo_telef_pagador=0, numero_telef_pagador=0),
            cbu_pagador            = "",
        )

        operacion = DatosOp(
            numero_operacion        = nop,
            identificador_externo_2 = identificador_cliente,
            identificador_externo_3 = "",
            id_moneda_operacion     = 1,
            monto_operacion         = float(importe),
            opc_pdf                 = False,
            opc_fecha_vencimiento   = date(2099, 12, 31),
            opc_devolver_qr         = False,
            opc_devolver_codbarras  = False,
            detalle_operacion       = [DetallePago(
                id_item     = 1,
                desc_item   = descripcion or "Adhesión débito directo",
                monto_item  = float(importe),
                cantidad_item = 1,
            )],
            pagador = pagador,
        )

        # fp=42 → débito directo CBU; FpArray([...]) es la sintaxis correcta para ArrayValue
        fp = FpArray([FormaPago(id_fp=42, monto_fp=float(importe))])

        resp = self._soap.service.solicitud_pago(
            API_VERSION,        # version
            "1",                # tipo_operacion
            self._creds_pago(), # credenciales (id_organismo + token)
            operacion,          # DatosOperacionPago
            fp,                 # DatosFormaPagoArray
            self.convenio,      # convenio
        )
        self._validar(resp.id_resp, resp.respuesta)

        base = "https://sandbox.epagos.com.ar" if self.entorno == "sandbox" else "https://www.epagos.com.ar"
        return f"{base}/pago/?id_pago={resp.id_transaccion}"

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
