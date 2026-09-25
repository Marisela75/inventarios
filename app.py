from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from model import (CAPACITY_SHEET, CONFIDENCE, LEAD_DAYS, REVIEW_DAYS,
                   SERVICE, allocate_if_mapped, estimate, extract)

st.set_page_config(page_title="Inventario Black-Litterman", layout="wide")
st.title("Optimización básica de inventario")
st.caption("Envase y partes técnicas; tapas: únicamente salidas de ventas")
st.markdown("**Configuración:** servicio 95 % · reposición 15 días · revisión semanal · "
            "confianza fija 50 % · historial máximo 12 meses · capacidad: CAPACIDAD ALMACÉN")

uploaded = st.file_uploader("Sube una versión del archivo de existencias (.xlsx)", type="xlsx")
example = Path(__file__).parent / "data" / "existencias_ejemplo.xlsx"
if uploaded:
    source = uploaded.getvalue()
elif example.exists():
    source = example.read_bytes()
    st.info("Se está usando el archivo de ejemplo incluido. Sube una versión nueva para actualizar los cálculos.")
else:
    st.info("Sube el archivo Excel para comenzar. Descarga desde GitHub también el archivo de datos si deseas un ejemplo precargado.")
    st.stop()

try:
    records, excluded, capacity = extract(source)
except Exception as exc:
    st.error(f"No se pudo procesar el archivo: {exc}")
    st.stop()

if not records:
    st.warning("No hay claves con existencia y salidas utilizables. Revisa los datos excluidos.")
    st.dataframe(excluded, use_container_width=True)
    st.stop()

earliest = min(r["inicio"] for r in records)
latest = max(r["corte"] for r in records)
st.info(f"Movimientos diarios identificados del {earliest:%d/%m/%Y} al "
        f"{latest:%d/%m/%Y} (fecha de corte de existencias). "
        "El límite de 12 meses no crea datos históricos que no estén presentes en las hojas seleccionadas.")

st.subheader("Expectativas de demanda")
st.write("Opcional: introduce una demanda diaria esperada para claves específicas. "
         "Una celda vacía conserva la estimación histórica. Los cambios no modifican el Excel original.")
if "source_id" not in st.session_state or st.session_state.source_id != hash(source):
    st.session_state.source_id = hash(source)
    st.session_state.views = pd.DataFrame([
        {"Hoja": r["hoja"], "Clave": r["clave"], "Descripción": r["descripcion"],
         "Demanda diaria esperada": None} for r in records])
edited = st.data_editor(st.session_state.views, hide_index=True, use_container_width=True,
                        disabled=["Hoja", "Clave", "Descripción"], num_rows="fixed",
                        column_config={"Demanda diaria esperada": st.column_config.NumberColumn(
                            "Demanda diaria esperada", min_value=0., step=1., format="%.2f")})
views = {}
for _, row in edited.iterrows():
    value = row["Demanda diaria esperada"]
    if pd.notna(value):
        views[row["Hoja"], row["Clave"]] = float(value)

try:
    result = allocate_if_mapped(estimate(records, views), capacity, mapping={})
except ValueError as exc:
    st.error(str(exc))
    st.stop()

st.subheader("Niveles sugeridos")
st.warning("El archivo no aporta una equivalencia verificable entre cada clave y sus piezas por "
           "posición de almacén. Los niveles mostrados son preliminares y NO están sujetos "
           "al límite de capacidad. La columna de capacidad identifica cada caso.")
st.dataframe(result, hide_index=True, use_container_width=True)
a, b, c = st.columns(3)
a.metric("Claves con nivel preliminar", len(result))
b.metric("Unidades excedentes estimadas", f"{int(result.Excedente.sum()):,}")
c.metric("Claves excluidas", len(excluded))
st.caption("El excedente es existencia actual menos nivel objetivo, cuando es positivo. "
           "La recomendación no calcula ahorro en MXN porque el archivo no contiene costo unitario validado.")

with st.expander("Exclusiones y capacidad de almacén"):
    st.dataframe(excluded, hide_index=True, use_container_width=True)
    st.write(f"Fuente de capacidad: {CAPACITY_SHEET}. Se muestran las categorías reconocidas "
             "sin asignarlas a claves por semejanza de descripción.")
    st.dataframe(capacity, hide_index=True, use_container_width=True)

st.subheader("Descargar resultados")
csv = result.to_csv(index=False).encode("utf-8-sig")
st.download_button("Descargar CSV", csv, "niveles_inventario.csv", "text/csv")
buffer = BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    result.to_excel(writer, sheet_name="Niveles", index=False)
    excluded.to_excel(writer, sheet_name="Exclusiones", index=False)
    capacity.to_excel(writer, sheet_name="Capacidad fuente", index=False)
    pd.DataFrame([
        ("Servicio objetivo", SERVICE), ("Días de reposición", LEAD_DAYS),
        ("Días entre revisiones", REVIEW_DAYS), ("Confianza de vistas", CONFIDENCE),
        ("Capacidad aplicada", "No: sin equivalencia clave-posiciones verificable"),
        ("Periodo observado desde", str(earliest)), ("Fecha de corte", str(latest)),
    ], columns=["Parámetro", "Valor"]).to_excel(writer, sheet_name="Metodología", index=False)
st.download_button("Descargar Excel", buffer.getvalue(), "niveles_inventario.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
