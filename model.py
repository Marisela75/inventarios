"""Inventory demand inference and constrained allocation, in physical units."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO
import math
import re

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.stats import norm
from openpyxl import load_workbook

SHEETS = ("ENVASE-AUTO Y PARTES TECNICAS", "TAPAS")
CAPACITY_SHEET = "CAPACIDAD ALMACÉN"
DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
CONFIDENCE = 0.5
SERVICE = 0.95
LEAD_DAYS = 15
REVIEW_DAYS = 7


def number(value):
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def header_date(label):
    found = DATE_RE.search(str(label or ""))
    return datetime.strptime(found.group(1), "%d/%m/%Y").date() if found else None


def extract(book_bytes):
    wb = load_workbook(BytesIO(book_bytes), read_only=True, data_only=True)
    missing = [name for name in (*SHEETS, CAPACITY_SHEET) if name not in wb]
    if missing:
        raise ValueError("Faltan hojas requeridas: " + ", ".join(missing))
    records = []
    problems = []
    for sheet in SHEETS:
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        headers = next(rows)
        dated = [(i, header_date(h), str(h or "").upper()) for i, h in enumerate(headers)]
        stock_cols = [(i, date) for i, date, label in dated if date and ("EXISTENCIA" in label)]
        out_cols = [(i, date) for i, date, label in dated if date and ("SALIDA" in label) and (sheet != "TAPAS" or "VENTAS" in label)]
        if not stock_cols or not out_cols:
            raise ValueError(f"No se reconocieron fechas de existencias y salidas en {sheet}.")
        cutoff = max(date for _, date in stock_cols)
        start = cutoff - timedelta(days=365)
        grouped = defaultdict(list)
        for rowno, row in enumerate(rows, 2):
            key = str(row[1] or "").strip() if len(row) > 1 else ""
            if not key or key.upper() in ("CLAVE", "TOTAL", "NONE"):
                continue
            desc = str(row[2] or "").strip() if len(row) > 2 else ""
            grouped[key].append((rowno, row, desc))
        for key, entries in grouped.items():
            # Duplicate key rows can be parallel ledgers; never sum snapshots.
            if len(entries) != 1:
                problems.append((sheet, key, "Clave duplicada: no es seguro consolidar existencias"))
                continue
            rowno, row, desc = entries[0]
            latest_i = max((i for i, d in stock_cols if d == cutoff), default=None)
            stock = number(row[latest_i]) if latest_i is not None and latest_i < len(row) else None
            if stock is None or stock < 0:
                problems.append((sheet, key, "Existencia actual vacía, negativa o con error"))
                continue
            daily = defaultdict(float)
            found = False
            invalid = False
            for i, date in out_cols:
                if date < start or date >= cutoff or i >= len(row):
                    continue
                value = row[i]
                if isinstance(value, str) and value.startswith("#"):
                    invalid = True
                qty = number(value)
                if qty is not None:
                    if qty < 0:
                        invalid = True
                    else:
                        daily[date] += qty
                        found |= qty > 0
            if invalid:
                problems.append((sheet, key, "Salida negativa o con error"))
                continue
            if not found:
                problems.append((sheet, key, "Sin salidas de ventas en el periodo"))
                continue
            observed_start = min((d for _, d in out_cols if start <= d < cutoff), default=cutoff)
            records.append(dict(hoja=sheet, clave=key, descripcion=desc, existencia=stock,
                                daily=daily, inicio=observed_start, corte=cutoff, fila=rowno))
    # Capacity is by package/volume category; no key-level association is inferred.
    capacity = []
    for rowno, row in enumerate(wb[CAPACITY_SHEET].iter_rows(values_only=True), 1):
        if rowno < 6:
            continue
        label = str(row[2] or "").strip() if len(row) > 2 else ""
        total, available, pieces = (number(row[i]) if len(row) > i else None for i in (3, 9, 10))
        if label and total is not None and available is not None:
            capacity.append(dict(presentacion=label, posiciones_total=total,
                                 posiciones_disponibles=available, piezas_disponibles=pieces))
    return records, pd.DataFrame(problems, columns=["Hoja", "Clave", "Motivo"]), pd.DataFrame(capacity)


def estimate(records, views=None):
    """Gaussian BL analogue on demand, not an investment-return portfolio.

    Prior mean: mean quantity per calendar day; positive-day mean is displayed.
    Prior covariance: regularized empirical daily covariance. Absolute views use
    Omega = tau*diag(Sigma)*(1-c)/c, c=.5; no view means prior unchanged.
    """
    if not records:
        return pd.DataFrame()
    views = views or {}
    end = max(r["corte"] for r in records)
    start = min(r["inicio"] for r in records)
    dates = [start + timedelta(days=i) for i in range((end-start).days)]
    X = np.array([[r["daily"].get(d, 0.) for r in records] for d in dates], dtype=float)
    prior = X.mean(axis=0)
    n = len(records)
    # Diagonal shrinkage makes short and sparse series numerically stable.
    variance = X.var(axis=0, ddof=1)
    floor = np.maximum(0.01, prior * 0.1)
    diag = np.maximum(variance, floor)
    covariance = np.diag(diag)
    tau = 1. / max(len(dates), 1)
    selected = [i for i, r in enumerate(records) if (r["hoja"], r["clave"]) in views
                and views[r["hoja"], r["clave"]] is not None]
    posterior = prior.copy()
    post_cov = tau * covariance
    if selected:
        q = np.array([float(views[records[i]["hoja"], records[i]["clave"]]) for i in selected])
        if not np.isfinite(q).all() or (q < 0).any():
            raise ValueError("Las expectativas de demanda deben ser números no negativos.")
        P = np.eye(n)[selected]
        omega = np.diag(tau * diag[selected] * (1 - CONFIDENCE) / CONFIDENCE)
        gain = post_cov @ P.T @ np.linalg.inv(P @ post_cov @ P.T + omega)
        posterior = prior + gain @ (q - P @ prior)
        post_cov = post_cov - gain @ P @ post_cov
    posterior = np.maximum(posterior, 0)
    z = norm.ppf(SERVICE)
    result = []
    for i, r in enumerate(records):
        positive = X[:, i][X[:, i] > 0]
        # Operational variance describes future daily departures; posterior
        # uncertainty represents the uncertainty in the estimated mean.
        safety = z * math.sqrt(LEAD_DAYS + REVIEW_DAYS) * math.sqrt(diag[i] + post_cov[i, i])
        target = math.ceil((LEAD_DAYS + REVIEW_DAYS) * posterior[i] + safety)
        result.append(dict(Hoja=r["hoja"], Clave=r["clave"], Descripcion=r["descripcion"],
                           Existencia_actual=int(r["existencia"]), Dias_con_salidas=len(positive),
                           Promedio_dias_con_salidas=positive.mean(), Frecuencia=len(positive)/len(dates),
                           Demanda_historica_diaria=prior[i], Demanda_posterior_diaria=posterior[i],
                           Desviacion_diaria=math.sqrt(diag[i]), Inventario_objetivo=target,
                           Excedente=max(0, int(r["existencia"])-target),
                           Reposicion_sugerida=max(0, target-int(r["existencia"])),
                           Vista_manual=views.get((r["hoja"], r["clave"])),
                           Piezas_por_posicion=None, Posiciones_asignadas=None,
                           Estado_capacidad="Sin equivalencia verificable por clave"))
    return pd.DataFrame(result)


def allocate_if_mapped(result, capacity, mapping):
    """Allocate available positions only when keys have exact, verified mappings.

    The supplied workbook reports category capacity but no unambiguous SKU map.
    Keep all unmatched keys preliminary. Never pool category-specific free slots.
    """
    result = result.copy()
    if not mapping or result.empty:
        return result
    for category, group in mapping.items():
        matching = capacity[capacity.presentacion.astype(str) == str(category)]
        if len(matching) != 1:
            continue
        budget = max(0, float(matching.iloc[0].posiciones_disponibles))
        eligible = [(i, pieces) for i, pieces in group.items() if i in result.index and pieces > 0]
        if not eligible:
            continue
        idx, pieces = zip(*eligible)
        risk = (result.loc[list(idx), "Desviacion_diaria"].to_numpy() + 0.01)
        upper = result.loc[list(idx), "Inventario_objetivo"].to_numpy(dtype=float)
        solution = linprog(-risk, A_ub=[1 / np.asarray(pieces)], b_ub=[budget],
                           bounds=[(0, x) for x in upper], method="highs")
        if solution.success:
            for k, index in enumerate(idx):
                quantity = math.floor(solution.x[k])
                result.loc[index, "Inventario_objetivo"] = quantity
                result.loc[index, "Piezas_por_posicion"] = pieces[k]
                result.loc[index, "Posiciones_asignadas"] = quantity / pieces[k]
                result.loc[index, "Estado_capacidad"] = "Sujeto a capacidad"
    result["Excedente"] = (result.Existencia_actual-result.Inventario_objetivo).clip(lower=0)
    result["Reposicion_sugerida"] = (result.Inventario_objetivo-result.Existencia_actual).clip(lower=0)
    return result
