import csv
import os
from datetime import datetime
from scrapers.base import Resultado


def guardar_csv(
    resultados_por_producto: dict[str, list[Resultado]], directorio: str
) -> str:
    os.makedirs(directorio, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(directorio, f"precios_{timestamp}.csv")

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Producto Buscado", "Sitio", "Título", "Precio", "Moneda", "URL"]
        )
        for producto, resultados in resultados_por_producto.items():
            for r in sorted(resultados, key=lambda x: x.precio):
                writer.writerow(
                    [producto, r.sitio, r.titulo, r.precio, r.moneda, r.url]
                )
    return ruta


def guardar_html(
    resultados_por_producto: dict[str, list[Resultado]], directorio: str
) -> str:
    os.makedirs(directorio, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(directorio, f"precios_{timestamp}.html")

    filas_html = ""
    for producto, resultados in resultados_por_producto.items():
        if not resultados:
            filas_html += f"""
            <tr>
              <td colspan="5" class="sin-resultados">
                <strong>{_esc(producto)}</strong> — sin resultados
              </td>
            </tr>"""
            continue

        ordenados = sorted(resultados, key=lambda x: x.precio)
        mejor = ordenados[0]

        for i, r in enumerate(ordenados):
            es_mejor = r is mejor
            clase_fila = "mejor-precio" if es_mejor else ("fila-par" if i % 2 == 0 else "")
            badge = '<span class="badge">🏆 Mejor precio</span>' if es_mejor else ""
            imagen_html = (
                f'<img src="{_esc(r.imagen)}" alt="" class="thumb">'
                if r.imagen
                else ""
            )
            precio_fmt = f"$ {r.precio:,.2f} {r.moneda}"
            prod_celda = f"<strong>{_esc(producto)}</strong>" if i == 0 else ""

            filas_html += f"""
            <tr class="{clase_fila}">
              <td>{prod_celda}</td>
              <td><span class="sitio sitio-{r.sitio.lower().replace(' ', '')}">{_esc(r.sitio)}</span></td>
              <td>{imagen_html} {_esc(r.titulo)}</td>
              <td class="precio">{precio_fmt} {badge}</td>
              <td><a href="{_esc(r.url)}" target="_blank">Ver →</a></td>
            </tr>"""

    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Comparador de Precios — {fecha}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #f4f6f9; color: #222; padding: 2rem; }}
    h1 {{ font-size: 1.6rem; color: #1a1a2e; margin-bottom: 0.3rem; }}
    .subtitulo {{ color: #666; margin-bottom: 2rem; font-size: 0.9rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
    thead {{ background: #1a1a2e; color: #fff; }}
    th {{ padding: 0.9rem 1rem; text-align: left; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; letter-spacing: .05em; }}
    td {{ padding: 0.75rem 1rem; vertical-align: middle; border-bottom: 1px solid #eee; font-size: 0.92rem; }}
    tr:last-child td {{ border-bottom: none; }}
    .mejor-precio {{ background: #fffbea; }}
    .fila-par {{ background: #fafafa; }}
    .precio {{ font-weight: 700; color: #1a1a2e; white-space: nowrap; }}
    .badge {{ background: #f59e0b; color: #fff; border-radius: 4px; padding: 2px 7px; font-size: 0.75rem; margin-left: 6px; white-space: nowrap; }}
    .sitio {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }}
    .sitio-mercadolibre {{ background: #ffe600; color: #333; }}
    .sitio-compragamer {{ background: #e8f5e9; color: #2e7d32; }}
    .sitio-hardgamer {{ background: #fce4ec; color: #c62828; }}
    .sitio-mexx {{ background: #e3f2fd; color: #1565c0; }}
    .thumb {{ width: 40px; height: 40px; object-fit: contain; vertical-align: middle; margin-right: 8px; border-radius: 4px; }}
    a {{ color: #1565c0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .sin-resultados {{ color: #999; font-style: italic; padding: 1rem; }}
    .footer {{ margin-top: 1.5rem; color: #aaa; font-size: 0.8rem; text-align: center; }}
  </style>
</head>
<body>
  <h1>Comparador de Precios</h1>
  <p class="subtitulo">Generado el {fecha} · Fuentes: MercadoLibre, CompraGamer, HardGamer, Mexx</p>
  <table>
    <thead>
      <tr>
        <th>Producto</th>
        <th>Sitio</th>
        <th>Título del producto</th>
        <th>Precio</th>
        <th>Enlace</th>
      </tr>
    </thead>
    <tbody>
      {filas_html}
    </tbody>
  </table>
  <p class="footer">Precios obtenidos por scraping automático — verificar disponibilidad en cada tienda.</p>
</body>
</html>"""

    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    return ruta


def _esc(texto: str) -> str:
    """Escapa caracteres HTML básicos."""
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
