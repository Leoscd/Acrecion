"""Presupuesto de obra sintetico y realista con formulas conectadas: Parametros -> Insumos -> APU -> Presupuesto -> Resumen.

Incluye errores tipicos SEMBRADOS a proposito (ver SEMBRADOS) para probar el analizador.
Si LibreOffice esta instalado, recalcula el libro para que tenga valores cacheados como un archivo guardado en Excel.

Uso: python scripts/demo_presupuesto.py salida.xlsx
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

PARAMETROS = {"sup_construida": 2450, "sup_vendible": 1980, "gg": 0.12, "utilidad": 0.10, "iva": 0.19}

INSUMOS = [  # codigo, nombre, unidad, precio
    ("I01", "Cemento", "saco", 6900), ("I02", "Arena", "m3", 22000), ("I03", "Grava", "m3", 24000),
    ("I04", "Acero A630", "kg", 1150), ("I05", "Alambre negro", "kg", 1600), ("I06", "Madera moldaje", "m2", 8500),
    ("I07", "Clavos", "kg", 2100), ("I08", "Ladrillo fiscal", "un", 320), ("I09", "Mortero premezclado", "saco", 4200),
    ("I10", "Maestro", "hh", 7500), ("I11", "Ayudante", "hh", 5200), ("I12", "Betonera", "día", 18000),
    ("I13", "Cerámica piso", "m2", 11900), ("I14", "Adhesivo cerámico", "saco", 5900), ("I15", "Pintura látex", "gl", 21000),
]

APUS = {  # codigo: (nombre, unidad, [(insumo, rendimiento)])
    "A1": ("Excavación manual", "m3", [("I11", 3.0)]),
    "A2": ("Hormigón H25", "m3", [("I01", 8.5), ("I02", 0.45), ("I03", 0.75), ("I12", 0.1), ("I10", 1.5), ("I11", 4.0)]),
    "A3": ("Acero A630 colocado", "kg", [("I04", 1.05), ("I05", 0.02), ("I10", 0.02), ("I11", 0.03)]),
    "A4": ("Moldaje de madera", "m2", [("I06", 0.35), ("I07", 0.15), ("I10", 0.6), ("I11", 0.6)]),
    "A5": ("Albañilería de ladrillo", "m2", [("I08", 39), ("I09", 0.6), ("I10", 1.0), ("I11", 1.0)]),
    "A6": ("Cerámica de piso", "m2", [("I13", 1.05), ("I14", 0.2), ("I10", 0.5), ("I11", 0.3)]),
    "A7": ("Pintura látex", "m2", [("I15", 0.025), ("I10", 0.15)]),
    "A8": ("Estuco (no usado)", "m2", [("I09", 0.4), ("I10", 0.3)]),  # APU huerfano, con un #REF!
}

# item, partida, unidad, cantidad, apu (o precio fijo si es numero)
PRESUPUESTO = [
    ("1", "OBRAS PRELIMINARES", None, None, None),
    ("1.1", "Instalación de faenas", "gl", 1, 18_500_000),
    ("1.2", "Excavación", "m3", 850, "A1"),
    ("2", "OBRA GRUESA", None, None, None),
    ("2.1", "Hormigón H25 fundaciones", "m3", 320, "A2"),
    ("2.2", "Hormigón H25 losas", "m3", 410, "A2"),
    ("2.3", "Acero A630", "kg", 68_000, "A3"),
    ("2.4", "Moldajes", "m2", 5200, "A4"),
    ("2.5", "Albañilería", "m2", 1850, 14_500),          # SEMBRADO: PU escrito a mano aunque existe el APU A5
    ("3", "TERMINACIONES", None, None, None),
    ("3.1", "Cerámica piso", "m2", 1900, "A6"),
    ("3.2", "Pintura muros", "m2", 7400, "A7"),
    ("3.3", "Pintura muros", "m2", 7400, "A7"),         # SEMBRADO: partida duplicada (deberia ser cielos)
    ("3.4", "Puertas y ventanas", "gl", 1, 42_000_000),
    ("4", "INSTALACIONES", None, None, None),
    ("4.1", "Instalación eléctrica", "gl", 1, 58_000_000),
    ("4.2", "Instalación sanitaria", "gl", 1, 46_000_000),
    ("4.3", "Instalación de gas", "Gl", 1, 9_500_000),  # SEMBRADO: unidad escrita distinto ("Gl")
]
RECARGO_OCULTO = ("2.4", 1.05)  # SEMBRADO: el total de esta partida multiplica por 1.05 (formula distinta al resto)

SEMBRADOS = {
    "pu_sin_apu": ["2.5"],
    "partida_duplicada": ["Pintura muros"],
    "unidad_inconsistente": ["Gl", "Cemento en APU Hormigon H25 como kg"],
    "formula_inconsistente": ["2.4"],
    "numero_magico": [0.19, 1.05],
    "error_formula": ["APU A8 #REF!"],
    "vinculo_externo": ["Insumos!E"],
    "hoja_oculta": ["Precios_viejos"],
    "apu_sin_uso": ["A8"],
    "alta_proporcion_global": [],
}


def _pu_apu(cod: str) -> float:
    precios = {c: p for c, _, _, p in INSUMOS}
    return sum(precios[i] * r for i, r in APUS[cod][2])


def esperado() -> dict:
    """Valores correctos que deberia calcular el libro (para verificar el analizador)."""
    partidas = []
    for item, nombre, unidad, cant, origen in PRESUPUESTO:
        if cant is None:
            continue
        pu = origen if isinstance(origen, (int, float)) else _pu_apu(origen)
        total = cant * pu * (RECARGO_OCULTO[1] if item == RECARGO_OCULTO[0] else 1)
        partidas.append({"item": item, "partida": nombre, "unidad": unidad, "total": total})
    cd = sum(p["total"] for p in partidas)
    gg = cd * PARAMETROS["gg"]
    util = (cd + gg) * PARAMETROS["utilidad"]
    neto = cd + gg + util
    total = neto * (1 + PARAMETROS["iva"])
    return {"partidas": partidas, "costo_directo": cd, "total": total,
            "costo_m2": cd / PARAMETROS["sup_construida"], "venta_m2": total / PARAMETROS["sup_vendible"]}


def crear(destino: Path) -> Path:
    wb = openpyxl.Workbook()
    negrita = Font(bold=True)

    par = wb.active
    par.title = "Parametros"
    par["A1"] = "Parametros del proyecto"
    par["A1"].font = negrita
    filas = [("Superficie construida (m2)", "sup_construida"), ("Superficie vendible (m2)", "sup_vendible"),
             ("Gastos generales (%)", "gg"), ("Utilidad (%)", "utilidad"), ("IVA (%)", "iva")]
    celda_par = {}
    for i, (etiqueta, clave) in enumerate(filas, start=3):
        par.cell(i, 1, etiqueta)
        par.cell(i, 2, PARAMETROS[clave])
        celda_par[clave] = f"Parametros!$B${i}"

    ins = wb.create_sheet("Insumos")
    ins.append(["Codigo", "Insumo", "Unidad", "Precio unitario", "Precio ref. 2023"])
    fila_insumo = {}
    for i, (cod, nombre, unidad, precio) in enumerate(INSUMOS, start=2):
        ins.append([cod, nombre, unidad, precio])
        fila_insumo[cod] = i
    # SEMBRADO: vinculo a un libro externo (columna de referencia, no se usa en los calculos)
    ins.cell(fila_insumo["I04"], 5, "='C:\\precios\\[Precios_2023.xlsx]Hoja1'!C14")

    apu = wb.create_sheet("APU")
    celda_pu = {}
    fila = 1
    for cod, (nombre, unidad, lineas) in APUS.items():
        apu.cell(fila, 1, f"APU {cod}").font = negrita
        apu.cell(fila, 2, nombre).font = negrita
        apu.cell(fila, 3, unidad)
        fila += 1
        for j, enc in enumerate(["Codigo", "Insumo", "Unidad", "Rendimiento", "Precio", "Parcial"], start=1):
            apu.cell(fila, j, enc)
        fila += 1
        inicio = fila
        for ins_cod, rend in lineas:
            r = fila_insumo[ins_cod]
            apu.cell(fila, 1, f"=Insumos!A{r}")
            apu.cell(fila, 2, f"=Insumos!B{r}")
            unidad_linea = f"=Insumos!C{r}"
            if cod == "A2" and ins_cod == "I01":
                unidad_linea = "kg"  # SEMBRADO: unidad escrita a mano distinta a la del insumo (saco)
            apu.cell(fila, 3, unidad_linea)
            apu.cell(fila, 4, rend)
            apu.cell(fila, 5, "=#REF!" if (cod == "A8" and ins_cod == "I10") else f"=Insumos!D{r}")  # SEMBRADO
            apu.cell(fila, 6, f"=D{fila}*E{fila}")
            fila += 1
        apu.cell(fila, 2, "Precio unitario").font = negrita
        apu.cell(fila, 6, f"=SUM(F{inicio}:F{fila - 1})").font = negrita
        celda_pu[cod] = f"APU!$F${fila}"
        fila += 2

    pre = wb.create_sheet("Presupuesto")
    pre["A1"] = "Presupuesto Edificio Los Alamos"
    pre["A1"].font = negrita
    pre.append([])
    pre.append(["Item", "Partida", "Unidad", "Cantidad", "P. Unitario", "Total"])
    fila = 4
    subtotales, cap_inicio, cap_fila = [], None, None

    def cerrar_capitulo(hasta: int):
        if cap_fila:
            pre.cell(cap_fila, 6, f"=SUM(F{cap_fila + 1}:F{hasta})")
            subtotales.append(f"F{cap_fila}")

    for item, nombre, unidad, cant, origen in PRESUPUESTO:
        if cant is None:
            cerrar_capitulo(fila - 1)
            cap_fila = fila
            pre.cell(fila, 1, item).font = negrita
            pre.cell(fila, 2, nombre).font = negrita
        else:
            pre.cell(fila, 1, item)
            pre.cell(fila, 2, nombre)
            pre.cell(fila, 3, unidad)
            pre.cell(fila, 4, cant)
            pre.cell(fila, 5, origen if isinstance(origen, (int, float)) else f"={celda_pu[origen]}")
            recargo = f"*{RECARGO_OCULTO[1]}" if item == RECARGO_OCULTO[0] else ""
            pre.cell(fila, 6, f"=D{fila}*E{fila}{recargo}")
        fila += 1
    cerrar_capitulo(fila - 1)
    fila += 1
    pie = {}
    for etiqueta, formula in [
        ("Costo directo", "=" + "+".join(subtotales)),
        ("Gastos generales", f"=F{{cd}}*{celda_par['gg']}"),
        ("Utilidad", f"=(F{{cd}}+F{{gg}})*{celda_par['utilidad']}"),
        ("Neto", "=F{cd}+F{gg}+F{util}"),
        ("IVA", "=F{neto}*0.19"),  # SEMBRADO: IVA escrito en la formula en vez de usar Parametros
        ("Total", "=F{neto}+F{iva}"),
    ]:
        pre.cell(fila, 2, etiqueta).font = negrita
        clave = {"Costo directo": "cd", "Gastos generales": "gg", "Utilidad": "util", "Neto": "neto", "IVA": "iva",
                 "Total": "total"}[etiqueta]
        pie[clave] = fila
        pre.cell(fila, 6, formula.format(**pie) if "{" in formula else formula)
        fila += 1

    res = wb.create_sheet("Resumen")
    res["A1"] = "Resumen del proyecto"
    res["A1"].font = negrita
    for i, (etiqueta, formula) in enumerate([
        ("Costo directo", f"=Presupuesto!F{pie['cd']}"),
        ("Total presupuesto", f"=Presupuesto!F{pie['total']}"),
        ("Costo de construccion por m2", f"=B3/{celda_par['sup_construida']}"),
        ("Precio de venta por m2", f"=B4/{celda_par['sup_vendible']}"),
    ], start=3):
        res.cell(i, 1, etiqueta)
        res.cell(i, 2, formula)

    viejo = wb.create_sheet("Precios_viejos")  # SEMBRADO: hoja oculta con precios desactualizados
    viejo.append(["Insumo", "Precio 2022"])
    viejo.append(["Cemento", 5200])
    viejo.sheet_state = "hidden"

    for ws in wb.worksheets:
        ws.column_dimensions["B"].width = 32
    destino.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destino)
    recalcular(destino)
    return destino


def recalcular(ruta: Path) -> bool:
    """Recalcula con LibreOffice (si existe) para que el libro tenga valores cacheados, como uno guardado en Excel."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx", "--outdir", tmp, str(ruta)],
                           capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
        salida = Path(tmp) / ruta.name
        if r.returncode or not salida.exists():
            return False
        shutil.copyfile(salida, ruta)
    return True


if __name__ == "__main__":
    print(crear(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("presupuesto_demo.xlsx")))
