import urllib.parse
from bs4 import BeautifulSoup
from .base import BaseScraper, Resultado, parsear_precio
import config


class CompraGamerScraper(BaseScraper):
    """Scraper para compragamer.com."""

    @property
    def site_name(self) -> str:
        return "CompraGamer"

    def buscar(self, query: str, max_resultados: int = 5) -> list[Resultado]:
        url = config.COMPRAGAMER_SEARCH_URL.format(
            query=urllib.parse.quote_plus(query)
        )
        resp = self._get(url, extra_headers={"Referer": "https://www.compragamer.com/"})
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        return self._parsear(soup, max_resultados)

    def _parsear(self, soup: BeautifulSoup, max_resultados: int) -> list[Resultado]:
        # compragamer usa una grilla de productos con contenedores individuales
        # Intentamos varios selectores en orden de especificidad
        contenedores = (
            soup.select("div.contenedor_producto")
            or soup.select("div.item_producto")
            or soup.select("div.producto")
            or soup.select("article.producto")
            or soup.select("div[class*='producto']")
        )

        resultados = []
        for item in contenedores[:max_resultados]:
            resultado = self._extraer_item(item)
            if resultado:
                resultados.append(resultado)

        if not resultados:
            resultados = self._parsear_fallback(soup, max_resultados)

        return resultados

    def _extraer_item(self, item) -> Resultado | None:
        # Título
        titulo_el = (
            item.select_one("a.nombreProducto")
            or item.select_one("span.nombreProducto")
            or item.select_one(".nombre")
            or item.select_one("a[title]")
            or item.select_one("h2 a")
            or item.select_one("h3 a")
        )
        # Precio
        precio_el = (
            item.select_one("span.precio_oferta")
            or item.select_one("span.precioActual")
            or item.select_one("span.precio")
            or item.select_one("[class*='precio']")
        )
        # Link
        link_el = item.select_one("a[href]")
        # Imagen
        img_el = item.select_one("img[src]") or item.select_one("img[data-src]")

        if not titulo_el or not precio_el:
            return None

        precio = parsear_precio(precio_el.get_text())
        if precio is None:
            return None

        href = link_el["href"] if link_el else ""
        if href and not href.startswith("http"):
            href = "https://www.compragamer.com" + href

        imagen = ""
        if img_el:
            imagen = img_el.get("src") or img_el.get("data-src", "")

        return Resultado(
            sitio=self.site_name,
            titulo=titulo_el.get_text(strip=True) or titulo_el.get("title", ""),
            precio=precio,
            moneda="ARS",
            url=href,
            imagen=imagen,
        )

    def _parsear_fallback(self, soup: BeautifulSoup, max_resultados: int) -> list[Resultado]:
        """Intenta extraer productos de cualquier estructura de tabla o lista."""
        resultados = []
        # Busca cualquier par título+precio con links
        for a_tag in soup.find_all("a", href=True):
            if len(resultados) >= max_resultados:
                break
            texto = a_tag.get_text(strip=True)
            if len(texto) < 5:
                continue
            precio_el = a_tag.find_next(class_=lambda c: c and "precio" in c.lower())
            if not precio_el:
                continue
            precio = parsear_precio(precio_el.get_text())
            if precio and precio > 0:
                href = a_tag["href"]
                if not href.startswith("http"):
                    href = "https://www.compragamer.com" + href
                resultados.append(
                    Resultado(
                        sitio=self.site_name,
                        titulo=texto,
                        precio=precio,
                        moneda="ARS",
                        url=href,
                    )
                )
        return resultados
