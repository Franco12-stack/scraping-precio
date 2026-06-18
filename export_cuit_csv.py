#!/usr/bin/env python3
"""
Exporta numero_operacion / id_transaccion / cuit de todos los cobros,
para mandarle a ePagos y que asocien el CUIT a las operaciones ya enviadas.
Correr desde el directorio del proyecto: python3 export_cuit_csv.py
Genera cobros_cuit.csv en el mismo directorio.
"""
import csv

from db import get_session, Cobro, Cliente

with get_session() as db:
    filas = (
        db.query(Cobro.numero_operacion, Cobro.id_transaccion, Cliente.cuit,
                  Cliente.nombre, Cliente.apellido)
        .join(Cliente, Cobro.cliente_id == Cliente.id)
        .order_by(Cobro.creado_en)
        .all()
    )

with open("cobros_cuit.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["numero_operacion", "id_transaccion", "cuit", "nombre", "apellido"])
    writer.writerows(filas)

print(f"OK → {len(filas)} filas escritas en cobros_cuit.csv")
