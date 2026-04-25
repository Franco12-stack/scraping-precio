from .base import BaseScraper, Resultado
import config


class MercadoLibreScraper(BaseScraper):
    """Usa la API pública de MercadoLibre (no necesita scraping HTML)."""

    API_URL = "https://api.mercadolibre.com/sites/{site_id}/search"

    def __init__(self, site_id: str = config.ML_SITE_ID, delay: float = 0.5):
        super().__init__(delay)
        self.site_id = site_id

    @property
    def site_name(self) -> str:
        return "MercadoLibre"

    def buscar(self, query: str, max_resultados: int = 5) -> list[Resultado]:
        url = self.API_URL.format(site_id=self.site_id)
        try:
            resp = self.session.get(
                url,
                params={"q": query, "limit": max_resultados},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [MercadoLibre] Error: {e}")
            return []

        resultados = []
        for item in data.get("results", [])[:max_resultados]:
            precio = item.get("price")
            if precio is None:
                continue
            resultados.append(
                Resultado(
                    sitio=self.site_name,
                    titulo=item.get("title", ""),
                    precio=float(precio),
                    moneda=item.get("currency_id", config.ML_CURRENCY),
                    url=item.get("permalink", ""),
                    imagen=item.get("thumbnail", ""),
                )
            )
        return resultados
