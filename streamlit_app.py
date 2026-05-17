"""
Dashboard ePagos — Cobros Recurrentes
Correr: streamlit run streamlit_app.py
"""
import uuid
from datetime import date, datetime, timedelta

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import Cliente, Cobro, Cuenta, engine, init_db
from epagos import EpagosClient, EpagosError

load_dotenv()
init_db()

# ── Configuración de página ──────────────────────────────────────────────────
st.set_page_config(
    page_title="ePagos Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Autenticación simple ─────────────────────────────────────────────────────
import os
ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_PASSWORD", "admin1234")

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.markdown("<h2 style='text-align:center;margin-top:80px'>⚡ ePagos Dashboard</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        with st.form("login"):
            st.subheader("Iniciar sesión")
            usuario = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            ok = st.form_submit_button("Ingresar", use_container_width=True)
            if ok:
                if usuario == ADMIN_USER and password == ADMIN_PASS:
                    st.session_state.autenticado = True
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos")
    st.stop()

# ── Helpers ──────────────────────────────────────────────────────────────────
def db() -> Session:
    return Session(engine)

def epagos() -> EpagosClient:
    return EpagosClient()

ESTADO_COLOR = {
    "acreditado": "🟢",
    "pendiente":  "🟡",
    "programado": "🔵",
    "error":      "🔴",
    "enviado":    "⚪",
    "devuelto":   "🟠",
}

def badge(estado: str) -> str:
    return f"{ESTADO_COLOR.get(estado, '⚪')} {estado}"

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚡ ePagos")
    st.caption("Cobros recurrentes · Sandbox")
    pagina = st.radio(
        "Navegación",
        ["📊 Dashboard", "👥 Clientes / CBU", "🧾 Historial cobros", "⚡ Cobros masivos"],
        label_visibility="collapsed",
    )
    st.divider()
    if st.button("🚪 Salir", use_container_width=True):
        st.session_state.autenticado = False
        st.rerun()

# ════════════════════════════════════════════════════════════════════════════
# PÁGINA: DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
if pagina == "📊 Dashboard":
    st.title("Panel principal")

    with db() as s:
        total_clientes = s.query(func.count(Cliente.id)).scalar()
        total_cuentas  = s.query(func.count(Cuenta.id)).scalar()
        total_cobros   = s.query(func.count(Cobro.id)).scalar()
        monto_acred    = s.query(func.sum(Cobro.importe)).filter(Cobro.estado == "acreditado").scalar() or 0
        cobros_all     = s.query(Cobro).join(Cliente).order_by(Cobro.creado_en.desc()).limit(200).all()
        cobros_data    = [
            {
                "Fecha":        c.creado_en.strftime("%d/%m/%Y %H:%M"),
                "Cliente":      f"{c.cliente.apellido}, {c.cliente.nombre}",
                "Importe":      c.importe,
                "Tipo":         c.tipo,
                "Estado":       badge(c.estado),
                "N° Operación": c.numero_operacion,
            }
            for c in cobros_all
        ]

    # Tarjetas de stats
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👥 Clientes", total_clientes)
    c2.metric("🏦 CBU registrados", total_cuentas)
    c3.metric("🧾 Cobros generados", total_cobros)
    c4.metric("💰 Monto acreditado", f"$ {monto_acred:,.2f}")

    st.divider()

    # Gráficos en dos columnas
    col_chart, col_estado = st.columns([2, 1])

    with col_chart:
        st.subheader("Cobros por mes (últimos 6 meses)")
        with db() as s:
            hoy = date.today()
            meses, cantidades, montos = [], [], []
            for i in range(5, -1, -1):
                mes_dt = hoy.replace(day=1) - timedelta(days=i * 28)
                mes_dt = mes_dt.replace(day=1)
                siguiente = (mes_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
                cant = s.query(func.count(Cobro.id)).filter(
                    Cobro.creado_en >= mes_dt,
                    Cobro.creado_en < siguiente,
                ).scalar() or 0
                monto = s.query(func.sum(Cobro.importe)).filter(
                    Cobro.creado_en >= mes_dt,
                    Cobro.creado_en < siguiente,
                    Cobro.estado == "acreditado",
                ).scalar() or 0
                meses.append(mes_dt.strftime("%b %Y"))
                cantidades.append(cant)
                montos.append(monto)

        fig = go.Figure()
        fig.add_bar(name="Cantidad", x=meses, y=cantidades, marker_color="#0d6efd")
        fig.add_scatter(name="Monto $", x=meses, y=montos, yaxis="y2",
                        line=dict(color="#198754", width=2), mode="lines+markers")
        fig.update_layout(
            yaxis=dict(title="Cantidad"),
            yaxis2=dict(title="Monto ARS", overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.1),
            margin=dict(l=0, r=0, t=10, b=0),
            height=260,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_estado:
        st.subheader("Por estado")
        with db() as s:
            rows = s.query(Cobro.estado, func.count(Cobro.id)).group_by(Cobro.estado).all()
        if rows:
            estados, counts = zip(*rows)
            colores = {"acreditado":"#198754","pendiente":"#ffc107","programado":"#0d6efd",
                       "error":"#dc3545","enviado":"#6c757d","devuelto":"#fd7e14"}
            fig2 = px.pie(
                names=[badge(e) for e in estados],
                values=counts,
                color=list(estados),
                color_discrete_map={badge(e): colores.get(e,"#aaa") for e in estados},
                hole=0.45,
            )
            fig2.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=260,
                               showlegend=True, legend=dict(orientation="v"))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Sin cobros todavía")

    st.divider()
    st.subheader(f"Últimos cobros ({len(cobros_data)})")
    if cobros_data:
        st.dataframe(cobros_data, use_container_width=True, hide_index=True,
                     column_config={"Importe": st.column_config.NumberColumn(format="$ %.2f")})
    else:
        st.info("Sin cobros todavía")


# ════════════════════════════════════════════════════════════════════════════
# PÁGINA: CLIENTES / CBU
# ════════════════════════════════════════════════════════════════════════════
elif pagina == "👥 Clientes / CBU":
    st.title("Clientes / CBU")

    tab_lista, tab_nuevo = st.tabs(["📋 Lista de clientes", "➕ Nuevo cliente"])

    # ── Tab: Lista ────────────────────────────────────────────────────────
    with tab_lista:
        busqueda = st.text_input("🔍 Buscar", placeholder="Nombre, apellido, email o ID ePagos…")

        with db() as s:
            q = s.query(Cliente)
            if busqueda:
                like = f"%{busqueda}%"
                q = q.filter(
                    Cliente.nombre.ilike(like) |
                    Cliente.apellido.ilike(like) |
                    Cliente.email.ilike(like) |
                    Cliente.identificador_cliente.ilike(like)
                )
            clientes = q.order_by(Cliente.apellido).all()
            clientes_data = [
                {
                    "id": c.id,
                    "Apellido": c.apellido,
                    "Nombre": c.nombre,
                    "Email": c.email,
                    "DNI": c.dni,
                    "ID ePagos": c.identificador_cliente,
                    "CBU": len(c.cuentas),
                    "Acreditado": sum(cb.importe for cb in c.cobros if cb.estado == "acreditado"),
                }
                for c in clientes
            ]

        st.caption(f"{len(clientes_data)} cliente(s) encontrado(s)")

        if not clientes_data:
            st.info("No hay clientes. Usá la pestaña **Nuevo cliente** para agregar el primero.")
        else:
            for row in clientes_data:
                with st.expander(f"**{row['Apellido']}, {row['Nombre']}** — {row['ID ePagos']} | CBU: {row['CBU']} | Acreditado: $ {row['Acreditado']:,.2f}"):
                    col_info, col_acc = st.columns([3, 1])
                    with col_info:
                        st.write(f"📧 {row['Email']}  |  DNI: {row['DNI']}")
                        # Cuentas CBU
                        with db() as s:
                            cuentas = s.query(Cuenta).filter(Cuenta.cliente_id == row["id"]).all()
                        if cuentas:
                            st.write("**Cuentas CBU/CVU:**")
                            for ct in cuentas:
                                st.code(f"{ct.identificador_cuenta}  {ct.cbu or ''}  {ct.alias or ''}", language=None)
                        else:
                            st.caption("Sin CBU registrados — sincronizá desde ePagos")

                        # Historial cobros del cliente
                        with db() as s:
                            cobros_cli = s.query(Cobro).filter(Cobro.cliente_id == row["id"]).order_by(Cobro.creado_en.desc()).all()
                        if cobros_cli:
                            st.write("**Cobros:**")
                            st.dataframe(
                                [{"Fecha": c.creado_en.strftime("%d/%m/%Y"), "Importe": c.importe,
                                  "Estado": badge(c.estado), "N° Op": c.numero_operacion,
                                  "Error": c.error or ""} for c in cobros_cli],
                                hide_index=True, use_container_width=True,
                                column_config={"Importe": st.column_config.NumberColumn(format="$ %.2f")},
                            )

                    with col_acc:
                        # Sincronizar CBU
                        if st.button("🔄 Sincronizar CBU", key=f"sync_{row['id']}"):
                            with st.spinner("Consultando ePagos…"):
                                try:
                                    with db() as s:
                                        cli = s.get(Cliente, row["id"])
                                        ep = epagos()
                                        cuentas_api = ep.obtener_cuentas_cliente(cli.identificador_cliente)
                                        nuevas = 0
                                        for c in cuentas_api:
                                            id_c = str(c.get("identificador_cuenta", ""))
                                            if not id_c:
                                                continue
                                            existe = s.query(Cuenta).filter(Cuenta.identificador_cuenta == id_c).first()
                                            if not existe:
                                                s.add(Cuenta(
                                                    cliente_id=row["id"],
                                                    identificador_cuenta=id_c,
                                                    alias=c.get("alias") or c.get("descripcion") or "",
                                                    cbu=c.get("cbu") or c.get("numero_cuenta") or "",
                                                ))
                                                nuevas += 1
                                        s.commit()
                                    st.success(f"{nuevas} cuenta(s) nueva(s) agregada(s)")
                                    st.rerun()
                                except EpagosError as e:
                                    st.error(str(e))

                        st.divider()

                        # Generar cobro
                        st.write("**Generar cobro**")
                        with db() as s:
                            cuentas_disp = s.query(Cuenta).filter(Cuenta.cliente_id == row["id"]).all()
                            cuentas_opts = {f"{ct.identificador_cuenta} ({ct.alias or ct.cbu or ''})": ct for ct in cuentas_disp}

                        if not cuentas_opts:
                            st.caption("Sin CBU — sincronizá primero")
                        else:
                            with st.form(f"cobro_{row['id']}"):
                                cuenta_sel = st.selectbox("Cuenta CBU", list(cuentas_opts.keys()))
                                importe = st.number_input("Importe (ARS)", min_value=1.0, step=100.0, format="%.2f")
                                tipo = st.radio("Tipo", ["Inmediato (72 hs)", "Programado"], horizontal=True)
                                fecha_cobro = None
                                if tipo == "Programado":
                                    fecha_cobro = st.date_input("Fecha de cobro", min_value=date.today())
                                descripcion = st.text_input("Descripción", value="Cobro recurrente")
                                cobrar = st.form_submit_button("💸 Generar cobro", use_container_width=True)

                            if cobrar:
                                with st.spinner("Enviando a ePagos…"):
                                    try:
                                        ct = cuentas_opts[cuenta_sel]
                                        with db() as s:
                                            cli = s.get(Cliente, row["id"])
                                            ep = epagos()
                                            nop = f"OP-{uuid.uuid4().hex[:12].upper()}"
                                            cobro = Cobro(
                                                cliente_id=row["id"], cuenta_id=ct.id,
                                                numero_operacion=nop, importe=importe,
                                                descripcion=descripcion,
                                                tipo="inmediato" if tipo.startswith("Inm") else "programado",
                                                fecha_cobro=fecha_cobro,
                                                estado="enviado",
                                            )
                                            s.add(cobro)
                                            s.flush()
                                            try:
                                                if tipo == "Programado" and fecha_cobro:
                                                    res = ep.solicitud_pago_recurrente_suscripcion(
                                                        identificador_cliente=cli.identificador_cliente,
                                                        identificador_cuenta=ct.identificador_cuenta,
                                                        importe=importe, numero_operacion=nop,
                                                        fecha_cobro=fecha_cobro,
                                                        nombre_pagador=cli.nombre, apellido_pagador=cli.apellido,
                                                        email_pagador=cli.email, dni_pagador=cli.dni,
                                                        cuit_pagador=cli.cuit, descripcion=descripcion,
                                                    )
                                                    cobro.estado = "programado"
                                                else:
                                                    res = ep.solicitud_pago_recurrente(
                                                        identificador_cliente=cli.identificador_cliente,
                                                        identificador_cuenta=ct.identificador_cuenta,
                                                        importe=importe, numero_operacion=nop,
                                                        nombre_pagador=cli.nombre, apellido_pagador=cli.apellido,
                                                        email_pagador=cli.email, dni_pagador=cli.dni,
                                                        cuit_pagador=cli.cuit, descripcion=descripcion,
                                                    )
                                                    cobro.id_transaccion = str(res.get("id_transaccion", ""))
                                                    cobro.estado = "pendiente"
                                                s.commit()
                                                st.success(f"✅ Cobro enviado — N° {nop}")
                                            except EpagosError as e:
                                                cobro.estado = "error"
                                                cobro.error = str(e)
                                                s.commit()
                                                st.error(str(e))
                                    except Exception as e:
                                        st.error(str(e))

                        st.divider()
                        if st.button("🗑️ Eliminar cliente", key=f"del_{row['id']}", type="secondary"):
                            with db() as s:
                                cli = s.get(Cliente, row["id"])
                                if cli:
                                    s.delete(cli)
                                    s.commit()
                            st.success("Cliente eliminado")
                            st.rerun()

    # ── Tab: Nuevo cliente ────────────────────────────────────────────────
    with tab_nuevo:
        st.subheader("Agregar cliente")
        with st.form("nuevo_cliente", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nombre   = c1.text_input("Nombre *")
            apellido = c2.text_input("Apellido *")
            email    = st.text_input("Email *")
            c3, c4 = st.columns(2)
            dni  = c3.number_input("DNI *", min_value=1000000, max_value=99999999, step=1, format="%d")
            cuit = c4.number_input("CUIT *", min_value=10000000000, max_value=99999999999, step=1, format="%d")
            id_ep = st.text_input("Identificador ePagos *", placeholder="ej: FCT-30123456",
                                  help="Mínimo 6 caracteres, único por contribuyente")
            guardar = st.form_submit_button("✅ Guardar cliente", use_container_width=True)

        if guardar:
            if not all([nombre, apellido, email, id_ep]) or len(id_ep) < 6:
                st.error("Completá todos los campos. El identificador debe tener al menos 6 caracteres.")
            else:
                try:
                    with db() as s:
                        s.add(Cliente(
                            identificador_cliente=id_ep,
                            nombre=nombre, apellido=apellido,
                            email=email, dni=int(dni), cuit=int(cuit),
                        ))
                        s.commit()
                    st.success(f"✅ Cliente **{apellido}, {nombre}** agregado correctamente")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")


# ════════════════════════════════════════════════════════════════════════════
# PÁGINA: HISTORIAL COBROS
# ════════════════════════════════════════════════════════════════════════════
elif pagina == "🧾 Historial cobros":
    st.title("Historial de cobros")

    col_bus, col_est = st.columns([3, 2])
    busqueda = col_bus.text_input("🔍 Buscar", placeholder="Cliente, N° operación, transacción…")
    estado_filtro = col_est.selectbox("Estado", ["Todos", "acreditado", "pendiente", "programado", "error", "enviado", "devuelto"])

    with db() as s:
        q = s.query(Cobro).join(Cliente).order_by(Cobro.creado_en.desc())
        if estado_filtro != "Todos":
            q = q.filter(Cobro.estado == estado_filtro)
        cobros = q.limit(500).all()
        rows = [
            {
                "Fecha":        c.creado_en.strftime("%d/%m/%Y %H:%M"),
                "Cliente":      f"{c.cliente.apellido}, {c.cliente.nombre}",
                "Importe":      c.importe,
                "Tipo":         c.tipo,
                "Fecha cobro":  c.fecha_cobro.strftime("%d/%m/%Y") if c.fecha_cobro else "—",
                "Estado":       badge(c.estado),
                "N° Operación": c.numero_operacion,
                "Transacción":  c.id_transaccion or "—",
                "Error":        c.error or "",
            }
            for c in cobros
        ]

    if busqueda:
        q_lower = busqueda.lower()
        rows = [r for r in rows if
                q_lower in r["Cliente"].lower() or
                q_lower in r["N° Operación"].lower() or
                q_lower in r["Transacción"].lower()]

    st.caption(f"{len(rows)} cobro(s) encontrado(s)")

    if rows:
        # Resumen rápido
        total_monto = sum(r["Importe"] for r in rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Total cobros", len(rows))
        c2.metric("Monto total filtrado", f"$ {total_monto:,.2f}")
        acred = sum(1 for r in rows if "acreditado" in r["Estado"])
        c3.metric("Acreditados", acred)
        st.divider()

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Importe": st.column_config.NumberColumn(format="$ %.2f"),
            },
        )
    else:
        st.info("No hay cobros con los filtros aplicados.")

# ════════════════════════════════════════════════════════════════════════════
# PÁGINA: COBROS MASIVOS
# ════════════════════════════════════════════════════════════════════════════
elif pagina == "⚡ Cobros masivos":
    st.title("⚡ Cobros masivos")
    st.caption("Cobrá a todos los CBU registrados de una sola vez")

    # — Config ----------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)
    importe_global = col1.number_input("Importe global (ARS)", min_value=0.0, step=0.01,
                                       format="%.2f", help="Se aplica a todos. Podés editarlo por fila.")
    tipo = col2.selectbox("Tipo", ["inmediato", "programado"])
    fecha_cobro_dt = col3.date_input("Fecha de cobro", min_value=date.today()) if tipo == "programado" else None
    descripcion = col4.text_input("Descripción", value="Cobro recurrente")

    # — Cargar clientes con CBU -----------------------------------------------
    with db() as s:
        pares = (
            s.query(Cliente, Cuenta)
            .join(Cuenta, Cuenta.cliente_id == Cliente.id)
            .order_by(Cliente.apellido, Cliente.nombre)
            .all()
        )

    if not pares:
        st.warning("No hay clientes con CBU registrado. Sincronizá CBU desde Clientes / CBU.")
        st.stop()

    import pandas as pd
    df = pd.DataFrame([
        {
            "✓": True,
            "Cliente":      f"{c.apellido}, {c.nombre}",
            "ID ePagos":    c.identificador_cliente,
            "Cuenta CBU":   ct.identificador_cuenta,
            "Alias":        ct.alias or "",
            "Importe (ARS)": importe_global if importe_global > 0 else 0.0,
            "_cid":         c.id,
            "_ctid":        ct.id,
        }
        for c, ct in pares
    ])

    st.caption(f"{len(df)} cliente(s) con CBU disponibles")
    edited = st.data_editor(
        df,
        column_config={
            "✓":             st.column_config.CheckboxColumn("✓", default=True, width="small"),
            "Importe (ARS)": st.column_config.NumberColumn("Importe (ARS)", min_value=0.01,
                                                            step=0.01, format="$ %.2f"),
            "_cid":          None,
            "_ctid":         None,
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="masivo_table",
    )

    sel = edited[edited["✓"] == True]
    total_sel = sel["Importe (ARS)"].sum()
    st.caption(f"**{len(sel)} seleccionado(s)** · Total: **$ {total_sel:,.2f}**")
    st.divider()

    if st.button(f"⚡ Cobrar {len(sel)} cliente(s)", type="primary",
                 disabled=len(sel) == 0, use_container_width=False):
        errores_importe = sel[sel["Importe (ARS)"] <= 0]
        if not errores_importe.empty:
            st.error(f"{len(errores_importe)} cliente(s) sin importe válido.")
        elif tipo == "programado" and not fecha_cobro_dt:
            st.error("Seleccioná una fecha de cobro.")
        else:
            client = EpagosClient()
            resultados = []
            barra = st.progress(0, text="Iniciando cobros…")
            total = len(sel)

            for i, (_, row) in enumerate(sel.iterrows()):
                try:
                    if tipo == "inmediato":
                        res = client.solicitud_pago_recurrente(
                            identificador_cliente=row["ID ePagos"],
                            identificador_cuenta=row["Cuenta CBU"],
                            importe=float(row["Importe (ARS)"]),
                            descripcion=descripcion,
                        )
                    else:
                        from datetime import datetime as _dt
                        res = client.solicitud_pago_recurrente_suscripcion(
                            identificador_cliente=row["ID ePagos"],
                            identificador_cuenta=row["Cuenta CBU"],
                            importe=float(row["Importe (ARS)"]),
                            descripcion=descripcion,
                            fecha_cobro=_dt.combine(fecha_cobro_dt, _dt.min.time()),
                        )
                    with db() as s2:
                        cobro = Cobro(
                            cliente_id=int(row["_cid"]),
                            cuenta_id=int(row["_ctid"]),
                            numero_operacion=res["numero_operacion"],
                            importe=float(row["Importe (ARS)"]),
                            descripcion=descripcion,
                            tipo=tipo,
                            fecha_cobro=fecha_cobro_dt if tipo == "programado" else None,
                            estado="pendiente" if tipo == "inmediato" else "programado",
                            id_transaccion=res.get("id_transaccion"),
                        )
                        s2.add(cobro)
                        s2.commit()
                    resultados.append({
                        "Cliente":       row["Cliente"],
                        "Importe":       float(row["Importe (ARS)"]),
                        "Estado":        "✅ enviado",
                        "N° Operación":  res["numero_operacion"],
                    })
                except EpagosError as e:
                    resultados.append({
                        "Cliente":       row["Cliente"],
                        "Importe":       float(row["Importe (ARS)"]),
                        "Estado":        "❌ error",
                        "N° Operación":  str(e),
                    })
                barra.progress((i + 1) / total, text=f"Procesando {i + 1}/{total}…")

            barra.empty()
            exitosos = sum(1 for r in resultados if "enviado" in r["Estado"])
            fallidos = len(resultados) - exitosos
            if fallidos:
                st.warning(f"✅ {exitosos} enviados · ⚠️ {fallidos} fallidos")
            else:
                st.success(f"✅ {exitosos} cobro(s) enviados correctamente")

            st.dataframe(
                resultados,
                use_container_width=True,
                hide_index=True,
                column_config={"Importe": st.column_config.NumberColumn(format="$ %.2f")},
            )
