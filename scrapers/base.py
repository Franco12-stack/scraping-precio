import re
import time
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class Resultado:
    sitio: str
    titulo: str
    precio: float
    moneda: str
    url: str
    imagen: str = ""


def parsear_precio(texto: str) -> Optional[float]:
    """Convierte strings de precio a float.
    Soporta formatos ARS (1.234.567 / 1.234,56) y USD (1,234.56).
    """
    limpio = re.sub(r"[^\d.,]", "", texto.strip())
    if not limpio:
        return None

    n_puntos = limpio.count(".")
    n_comas = limpio.count(",")

    if n_puntos > 1:
        # 1.234.567 o 1.234.567,89 → separadores de miles con punto (ARS)
        limpio = limpio.replace(".", "")
        if n_comas == 1:
            limpio = limpio.replace(",", ".")
    elif n_comas > 1:
        # 1,234,567 → separadores de miles con coma (USD)
        limpio = limpio.replace(",", "")
    elif n_puntos == 1 and n_comas == 0:
        # 1.234 → en ARS los precios con 3 dígitos post-punto son miles
        partes = limpio.split(".")
        if len(partes[1]) == 3:
            limpio = limpio.replace(".", "")
    elif n_puntos == 1 and n_comas == 1:
        if limpio.index(",") < limpio.index("."):
            # 1,234.56 → formato USD
            limpio = limpio.replace(",", "")
        else:
            # 1.234,56 → formato ARS/EU
            limpio = limpio.replace(".", "").replace(",", ".")
    elif n_comas == 1 and n_puntos == 0:
        partes = limpio.split(",")
        if len(partes) == 2 and len(partes[1]) <= 2:
            limpio = limpio.replace(",", ".")
        else:
            limpio = limpio.replace(",", "")

    try:
        return float(limpio)
    except ValueError:
        return None


class BaseScraper(ABC):
    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(
        self,
        url: str,
        params: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
        retries: int = 3,
    ) -> Optional[requests.Response]:
        headers = dict(self.session.headers)
        if extra_headers:
            headers.update(extra_headers)

        for intento in range(retries):
            try:
                time.sleep(self.delay + random.uniform(0.3, 1.2))
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=20
                )
                resp.raise_for_status()
                return resp
            except requests.HTTPError as e:
                codigo = e.response.status_code if e.response is not None else 0
                if codigo in (429, 503, 403):
                    espera = 10 * (intento + 1)
                    print(f"  [{self.site_name}] HTTP {codigo}, esperando {espera}s...")
                    time.sleep(espera)
                elif intento == retries - 1:
                    print(f"  [{self.site_name}] Error HTTP {codigo}: {url}")
            except requests.RequestException as e:
                if intento == retries - 1:
                    print(f"  [{self.site_name}] Error de red: {e}")
                else:
                    time.sleep(2**intento)
        return None

    @property
    @abstractmethod
    def site_name(self) -> str:
        pass

    @abstractmethod
    def buscar(self, query: str, max_resultados: int = 5) -> list[Resultado]:
        pass
