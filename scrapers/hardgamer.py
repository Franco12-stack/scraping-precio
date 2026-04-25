import urllib.parse
from bs4 import BeautifulSoup
from .base import BaseScraper, Resultado, parsear_precio
import config


class HardGamerScraper(BaseScraper):
    """Scraper para hardgamer.com.ar (plataforma Magento)."""

    BASE_URL = "https://www.hardgamer.com.ar"

    @property
    def site_name(self) -> str:
        return "HardGamer"

    def buscar(self, query: str, max_resultados: int = 5) -> list[Resultado]:
        url = config.HARDGAMER_SEARCH_URL.format(
            query=urllib.parse.quote_plus(query)
        )
        resp = self._get(url, extra_headers={"Referer": self.BASE_URL + "/"})
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parsear(soup, max_resultados)

    def _parsear(self, soup: BeautifulSoup, max_resultados: int) -> list[Resultado]:
        # Magento 2 usa li.product-item; Magento 1 usa li.item
        contenedores = (
            soup.select("li.product-item")
            or soup.select("li.item.product")
            or soup.select("div.product-item")
            or soup.select("div[class*='product-item']")
        )

        resultados = []
        for item in contenedores[:max_resultados]:
            resultado = self._extraer_item(item)
            if resultado:
                resultados.append(resultado)
        return resultados

    def _extraer_item(self, item) -> Resultado | None:
        # Título (Magento 2)
        titulo_el = (
            item.select_one(".product-item-name a")
            or item.select_one(".product-name a")
            or item.select_one("h2.product-name a")
            or item.select_one("a.product-item-link")
        )
        # Precio (Magento 2 y 1)
        precio_el = (
            item.select_one("span.price")
            or item.select_one(".price-box .price")
            or item.select_one(".regular-price .price")
            or item.select_one("[data-price-type='finalPrice'] .price")
        )
        # Imagen
        img_el = item.select_one("img.product-image-photo") or item.select_one("img")

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
            imagen = (
                img_el.get("src")
                or img_el.get("data-src")
                or img_el.get("data-original", "")
            )

        return Resultado(
            sitio=self.site_name,
            titulo=titulo_el.get_text(strip=True),
            precio=precio,
            moneda="ARS",
            url=href,
            imagen=imagen,
        )
