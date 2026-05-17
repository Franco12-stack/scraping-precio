from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ResultadoPago:
    id_operacion: str
    estado: str                  # "acreditado", "pendiente", "rechazado"
    importe: float
    moneda: str
    identificador_cliente: str
    tipo_operacion: str
    fecha: datetime
    medio_pago: Optional[str] = None
    descripcion: Optional[str] = None


@dataclass
class CuentaCliente:
    identificador_cuenta: str
    identificador_cliente: str
    cbu: str
    tipo_operacion: str
    activa: bool = True


@dataclass
class Rendicion:
    id_operacion: str
    fecha_acreditacion: datetime
    importe: float
    moneda: str
    identificador_cliente: str
    tipo_operacion: str
    medio_pago: str
    comision: float = 0.0


@dataclass
class PagoEvento:
    """Payload que ePagos envía al webhook del organismo."""
    id_operacion: str
    estado: str
    importe: float
    moneda: str
    identificador_cliente: str
    tipo_operacion: str
    fecha: datetime
    medio_pago: Optional[str] = None
    datos_extra: dict = field(default_factory=dict)
