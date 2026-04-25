import json
import urllib.parse
from bs4 import BeautifulSoup
from .base import BaseScraper, Resultado, parsear_precio
import config


class MexxScraper(BaseScraper):
    """Scraper para mexx.com.ar."""

    BASE_URL = "https://mexx.com.ar"

    @property
    def site_name(self) -> str:
        return "Mexx"

    def buscar(self, query: str, max_resultados: int = 5) -> list[Resultado]:
        url = config.MEXX_SEARCH_URL.format(query=urllib.parse.quote_plus(query))
        resp = self._get(url, extra_headers={"Referer": self.BASE_URL + "/"})
        if not resp:
            return []

        # Intenta extraer datos de __NEXT_DATA__ (Next.js) primero
        resultados = self._parsear_nextdata(resp.text, max_resultados)
        if resultados:
            return resultados

        # Fallback: parseo HTML estático
        soup = BeautifulSoup(resp.text, "lxml")
        return self._parsear_html(soup, max_resultados)

    def _parsear_nextdata(self, html: str, max_resultados: int) -> list[Resultado]:
        """Extrae productos del JSON embebido de Next.js."""
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return []
        try:
            data = json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            return []

        # Navega por el árbol de Next.js buscando listas de productos
        productos = self._buscar_productos_en_json(data)
        resultados = []
        for prod in productos[:max_resultados]:
            resultado = self._extraer_de_json(prod)
            if resultado:
                resultados.append(resultado)
        return resultados

    def _buscar_productos_en_json(self, data: dict | list, profundidad: int = 0) -> list:
        """Búsqueda recursiva de arrays de productos dentro del JSON de Next.js."""
        if profundidad > 8:
            return []
        candidatos = []
        if isinstance(data, dict):
            for key, val in data.items():
                if key in ("products", "items", "results", "hits", "edges", "nodes"):
                    if isinstance(val, list) and val:
                        candidatos.extend(val)
                else:
                    candidatos.extend(
                        self._buscar_productos_en_json(val, profundidad + 1)
                    )
        elif isinstance(data, list):
            for item in data:
                candidatos.extend(
                    self._buscar_productos_en_json(item, profundidad + 1)
                )
        return candidatos

    def _extraer_de_json(self, prod: dict) -> Resultado | None:
        if not isinstance(prod, dict):
            return None
        titulo = (
            prod.get("name") or prod.get("title") or prod.get("displayName", "")
        )
        precio_raw = (
            prod.get("price")
            or prod.get("salePrice")
            or prod.get("priceMin")
            or (prod.get("pricing") or {}).get("price")
        )
        url = prod.get("url") or prod.get("link") or prod.get("slug", "")
        imagen = (
            prod.get("image")
            or prod.get("thumbnail")
            or (prod.get("images") or [""])[0]
        )

        if not titulo or precio_raw is None:
            return None
        precio = parsear_precio(str(precio_raw))
        if precio is None:
            return None

        if url and not url.startswith("http"):
            url = self.BASE_URL + ("/" if not url.startswith("/") else "") + url

        return Resultado(
            sitio=self.site_name,
            titulo=str(titulo),
            precio=precio,
            moneda="ARS",
            url=url,
            imagen=str(imagen) if imagen else "",
        )

    def _parsear_html(self, soup: BeautifulSoup, max_resultados: int) -> list[Resultado]:
        """Parseo HTML genérico para tiendas Shopify / WooCommerce / custom."""
        contenedores = (
            soup.select("div.product-card")
            or soup.select("article.product")
            or soup.select("div.product-item")
            or soup.select("li.product")
            or soup.select("[class*='ProductCard']")
            or soup.select("[class*='product-card']")
        )

        resultados = []
        for item in contenedores[:max_resultados]:
            resultado = self._extraer_item_html(item)
            if resultado:
                resultados.append(resultado)
        return resultados

    def _extraer_item_html(self, item) -> Resultado | None:
        titulo_el = (
            item.select_one("h2 a")
            or item.select_one("h3 a")
            or item.select_one(".product-title a")
            or item.select_one("[class*='title'] a")
            or item.select_one("a[href*='/productos/']")
            or item.select_one("a[href*='/product/']")
        )
        precio_el = (
            item.select_one("span.price")
            or item.select_one("[class*='price']")
            or item.select_one("[class*='Price']")
        )
        img_el = item.select_one("img")

        if not titulo_el or not precio_el:
            return None
        precio = parsear_precio(precio_el.get_text())
        if precio is None:
            return None

        href = titulo_el.get("href", "")
        if href and not href.startswith("http"):
            href = self.BASE_URL + href

        imagen = ""
        if img_el:
            imagen = img_el.get("src") or img_el.get("data-src", "")

        return Resultado(
            sitio=self.site_name,
            titulo=titulo_el.get_text(strip=True),
            precio=precio,
            moneda="ARS",
            url=href,
            imagen=imagen,
        )
