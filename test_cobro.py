#!/usr/bin/env python3
"""
Test end-to-end: registrar_cuentas_cliente + solicitud_pago_recurrente
Correr desde el directorio del proyecto: python3 test_cobro.py
"""
import os, sys, uuid
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from epagos.client import EpagosClient, EpagosError

CBU_PRUEBA        = "3220002204000040970011"  # CBU DEBIN sandbox
CUIT_PRUEBA       = 20123456781
DNI_PRUEBA        = 12345678
NOMBRE_PRUEBA     = "Juan"
APELLIDO_PRUEBA   = "Prueba"
EMAIL_PRUEBA      = "prueba@test.com"
ID_CLIENTE_PRUEBA = f"TEST-{CUIT_PRUEBA}"
IMPORTE_PRUEBA    = 100.0


def separador(titulo):
    print(f"\n{'='*60}")
    print(f"  {titulo}")
    print('='*60)


def main():
    client = EpagosClient()
    print(f"Entorno: {client.entorno}")
    print(f"ID Organismo: {client.id_organismo}")

    # ── 1. registrar_cuentas_cliente ──────────────────────────────
    separador("1. registrar_cuentas_cliente")
    try:
        resultado = client.registrar_cuenta_cliente(
            identificador_cliente=ID_CLIENTE_PRUEBA,
            cbu=CBU_PRUEBA,
            cuit=CUIT_PRUEBA,
        )
        print(f"OK → {resultado}")
        id_cuenta = resultado.get("identificador_cuenta", CBU_PRUEBA)
    except EpagosError as e:
        print(f"EpagosError: {e}")
        print("\n✗ No se puede continuar sin un identificador_cuenta válido.")
        sys.exit(1)
    except Exception as e:
        print(f"Error inesperado: {e}")
        print("\n✗ No se puede continuar sin un identificador_cuenta válido.")
        sys.exit(1)

    # ── 2. solicitud_pago_recurrente ──────────────────────────────
    separador("2. solicitud_pago_recurrente")
    numero_op = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    fecha_debito = date.today() + timedelta(days=7)
    print(f"numero_operacion : {numero_op}")
    print(f"id_cuenta        : {id_cuenta}")
    print(f"fecha_debito     : {fecha_debito}")
    print(f"importe          : {IMPORTE_PRUEBA}")

    try:
        resultado = client.solicitud_pago_recurrente(
            identificador_cliente=ID_CLIENTE_PRUEBA,
            identificador_cuenta=id_cuenta,
            importe=IMPORTE_PRUEBA,
            numero_operacion=numero_op,
            nombre_pagador=NOMBRE_PRUEBA,
            apellido_pagador=APELLIDO_PRUEBA,
            email_pagador=EMAIL_PRUEBA,
            dni_pagador=DNI_PRUEBA,
            cuit_pagador=CUIT_PRUEBA,
            descripcion="Test cobro recurrente",
            fecha_debito=fecha_debito,
        )
        print(f"\nOK → {resultado}")
        print(f"\n✓ id_transaccion : {resultado.get('id_transaccion')}")
        print(f"✓ numero_operacion: {resultado.get('numero_operacion')}")
    except EpagosError as e:
        print(f"\nEpagosError: {e}")
    except Exception as e:
        import traceback
        print(f"\nError inesperado: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
