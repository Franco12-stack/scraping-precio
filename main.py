#!/usr/bin/env python3
"""
Comparador de precios: MercadoLibre + CompraGamer + HardGamer + Mexx
Uso:
    python main.py
    python main.py "rtx 4070" "monitor 27 pulgadas"
    python main.py --archivo productos.txt
    python main.py --archivo productos.json --max 3
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich import box

import config
from scrapers import (
    MercadoLibreScraper,
    CompraGamerScraper,
    HardGamerScraper,
    MexxScraper,
)
from scrapers.base import Resultado
from report import guardar_csv, guardar_html

console = Console()

SCRAPERS = [
    MercadoLibreScraper(),
    CompraGamerScraper(),
    HardGamerScraper(),
    MexxScraper(),
]


def leer_productos(args) -> list[str]:
    if args.archivo:
        return _leer_archivo(args.archivo)
    if args.productos:
        return args.productos
    # Interactivo
    console.print("\n[bold cyan]Ingresá los productos a buscar[/] (uno por línea, línea vacía para terminar):\n")
    productos = []
    while True:
        try:
            linea = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not linea:
            break
        productos.append(linea)
    return productos


def _leer_archivo(ruta: str) -> list[str]:
    if not os.path.exists(ruta):
        console.print(f"[red]Archivo no encontrado:[/] {ruta}")
        sys.exit(1)

    with open(ruta, encoding="utf-8") as f:
        contenido = f.read().strip()

    if ruta.endswith(".json"):
        data = json.loads(contenido)
        if isinstance(data, list):
            return [str(p) for p in data]
        if isinstance(data, dict):
            for key in ("productos", "products", "items"):
                if key in data:
                    return [str(p) for p in data[key]]
        console.print("[red]JSON inválido:[/] debe ser una lista o un dict con clave 'productos'.")
        sys.exit(1)
    else:
        return [l for l in contenido.splitlines() if l.strip()]


def buscar_en_sitio(scraper, query: str, max_resultados: int) -> list[Resultado]:
    try:
        return scraper.buscar(query, max_resultados)
    except Exception as e:
        console.print(f"  [red][{scraper.site_name}] Error inesperado:[/] {e}")
        return []


def buscar_producto(producto: str, max_resultados: int) -> list[Resultado]:
    todos = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futuros = {
            executor.submit(buscar_en_sitio, sc, producto, max_resultados): sc.site_name
            for sc in SCRAPERS
        }
        for futuro in as_completed(futuros):
            sitio = futuros[futuro]
            try:
                resultados = futuro.result()
                todos.extend(resultados)
                icono = "✓" if resultados else "·"
                console.print(f"    {icono} {sitio}: [dim]{len(resultados)} resultado(s)[/]")
            except Exception as e:
                console.print(f"    ✗ {sitio}: [red]{e}[/]")
    return todos


def mostrar_tabla(producto: str, resultados: list[Resultado]) -> None:
    tabla = Table(
        title=f"[bold]{producto}[/]",
        box=box.ROUNDED,
        header_style="bold white on dark_blue",
        show_lines=True,
    )
    tabla.add_column("Sitio", min_width=12)
    tabla.add_column("Producto", max_width=60, overflow="fold")
    tabla.add_column("Precio", justify="right", min_width=18)
    tabla.add_column("Enlace", max_width=40, overflow="fold")

    if not resultados:
        tabla.add_row("[dim]—[/]", "[dim]Sin resultados[/]", "[dim]—[/]", "[dim]—[/]")
        console.print(tabla)
        return

    ordenados = sorted(resultados, key=lambda r: r.precio)
    precio_min = ordenados[0].precio

    for r in ordenados:
        es_mejor = r.precio == precio_min
        precio_str = f"$ {r.precio:,.2f} {r.moneda}"
        if es_mejor:
            precio_str = f"[bold yellow]{precio_str}[/] 🏆"
        tabla.add_row(r.sitio, r.titulo, precio_str, r.url)

    console.print(tabla)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comparador de precios: ML, CompraGamer, HardGamer, Mexx"
    )
    parser.add_argument(
        "productos",
        nargs="*",
        metavar="PRODUCTO",
        help="Productos a buscar (entre comillas si tienen espacios)",
    )
    parser.add_argument(
        "--archivo",
        "-a",
        metavar="RUTA",
        help="Archivo .txt o .json con lista de productos",
    )
    parser.add_argument(
        "--max",
        "-m",
        type=int,
        default=config.MAX_RESULTS,
        dest="max_resultados",
        help=f"Resultados por sitio (default: {config.MAX_RESULTS})",
    )
    parser.add_argument(
        "--sin-html",
        action="store_true",
        help="No generar reporte HTML",
    )
    parser.add_argument(
        "--sin-csv",
        action="store_true",
        help="No generar reporte CSV",
    )
    args = parser.parse_args()

    console.print(
        Panel.fit(
            "[bold cyan]Comparador de Precios[/]\n"
            "[dim]MercadoLibre · CompraGamer · HardGamer · Mexx[/]",
            border_style="cyan",
        )
    )

    productos = leer_productos(args)
    if not productos:
        console.print("[yellow]No se indicaron productos.[/]")
        sys.exit(0)

    console.print(f"\n[bold]Buscando {len(productos)} producto(s), {args.max_resultados} resultado(s) por sitio...[/]\n")

    resultados_totales: dict[str, list[Resultado]] = {}
    inicio = time.time()

    for i, producto in enumerate(productos, 1):
        console.print(f"[bold cyan]({i}/{len(productos)})[/] [bold]{producto}[/]")
        resultados = buscar_producto(producto, args.max_resultados)
        resultados_totales[producto] = resultados
        mostrar_tabla(producto, resultados)
        console.print()

    elapsed = time.time() - inicio
    console.print(f"[dim]Búsqueda completada en {elapsed:.1f}s[/]\n")

    # Exportar
    if not args.sin_csv:
        ruta_csv = guardar_csv(resultados_totales, config.OUTPUT_DIR)
        console.print(f"[green]CSV guardado:[/] {ruta_csv}")

    if not args.sin_html:
        ruta_html = guardar_html(resultados_totales, config.OUTPUT_DIR)
        console.print(f"[green]HTML guardado:[/] {ruta_html}")

    # Resumen: mejor precio por producto
    console.print("\n[bold underline]Resumen — Mejor precio por producto:[/]\n")
    for producto, resultados in resultados_totales.items():
        if resultados:
            mejor = min(resultados, key=lambda r: r.precio)
            console.print(
                f"  [cyan]{producto}[/]: "
                f"[bold yellow]$ {mejor.precio:,.2f} {mejor.moneda}[/] "
                f"en [green]{mejor.sitio}[/]"
            )
        else:
            console.print(f"  [cyan]{producto}[/]: [dim]sin resultados[/]")


if __name__ == "__main__":
    main()
