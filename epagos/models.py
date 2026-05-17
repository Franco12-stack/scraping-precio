from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PagoEvento:
    """Payload que ePagos envía al webhook del organismo."""
    id_operacion: str
    estado: str                  # "acreditado", "devuelto"
    importe: float
    moneda: str
    identificador_cliente: str
    tipo_operacion: str
    fecha: datetime
    medio_pago: Optional[str] = None
    datos_extra: dict = field(default_factory=dict)
