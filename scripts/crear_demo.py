"""Genera un cliente ficticio (constructora) con archivos de todos los tipos para probar el pipeline.

Uso: python scripts/crear_demo.py [carpeta_cliente]
"""
from __future__ import annotations

import sys
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "data" / "demo_constructora"
RAW = BASE / "raw"


def presupuesto():
    partidas = pd.DataFrame({
        "Item": ["1.1", "1.2", "2.1", "2.2", "3.1"],
        "Partida": ["Excavacion", "Relleno compactado", "Hormigon H25 fundaciones", "Acero A630", "Moldajes"],
        "Unidad": ["m3", "m3", "m3", "kg", "m2"],
        "Cantidad": [120, 80, 45, 3200, 210],
        "Precio unitario": [8500, 6200, 98000, 1150, 12500],
    })
    partidas["Total"] = partidas["Cantidad"] * partidas["Precio unitario"]
    with pd.ExcelWriter(RAW / "presupuestos" / "presupuesto_edificio_los_alamos.xlsx") as w:
        partidas.to_excel(w, sheet_name="Itemizado", index=False)
        pd.DataFrame({"Nota": ["Cubicacion hecha a mano desde planos PDF", "Precios copiados de cotizaciones por correo",
                               "Revisar con oficina tecnica antes de enviar la licitacion"]}).to_excel(w, sheet_name="Notas", index=False)


def log_compras():
    """Log de eventos: cada solicitud de material pasa por etapas con fecha y responsable."""
    filas = []
    flujo = [("Solicitud de material", "Jefe de obra", 0), ("Cotizacion proveedor", "Adquisiciones", 2),
             ("Aprobacion", "Gerente", 5), ("Orden de compra", "Adquisiciones", 6), ("Recepcion en obra", "Bodeguero", 10)]
    for caso in range(1, 13):
        t = pd.Timestamp("2024-03-01") + pd.Timedelta(days=caso * 3)
        for i, (etapa, resp, dias) in enumerate(flujo):
            filas.append({"N solicitud": f"SM-{caso:03d}", "Etapa": etapa, "Responsable": resp,
                          "Fecha": (t + pd.Timedelta(days=dias + caso % 3)).strftime("%d/%m/%Y %H:%M")})
            if etapa == "Cotizacion proveedor" and caso % 4 == 0:  # retrabajo: se pide una segunda cotizacion
                filas.append({"N solicitud": f"SM-{caso:03d}", "Etapa": etapa, "Responsable": resp,
                              "Fecha": (t + pd.Timedelta(days=dias + 2)).strftime("%d/%m/%Y %H:%M")})
    pd.DataFrame(filas).to_csv(RAW / "compras" / "registro_solicitudes_material.csv", index=False)


def informe_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    est = getSampleStyleSheet()
    textos = [
        ("Informe semanal de obra N 14 - Edificio Los Alamos", "Title"),
        ("Elaborado por: Pedro Soto, jefe de obra. Fecha: 15/03/2024.", "Normal"),
        ("Avance fisico: 42% (programado 47%). El atraso se debe a demora en la llegada de acero A630.", "Normal"),
        ("Este informe se arma manualmente cada viernes: se copian las fotos desde WhatsApp, se revisa el libro de obra "
         "y se digitan las cantidades en Excel para calcular el avance. Toma aproximadamente 4 horas.", "Normal"),
        ("Seguridad: charla de 5 minutos realizada todos los dias. Se registro una no conformidad por falta de EPP.", "Normal"),
        ("Pendientes: responder RFI 023 de especialidad electrica por interferencia con ducto de clima.", "Normal"),
    ]
    doc = SimpleDocTemplate(str(RAW / "informes" / "informe_semanal_obra_14.pdf"), pagesize=A4)
    doc.build([x for t, s in textos for x in (Paragraph(t, est[s]), Spacer(1, 10))])


def correos():
    hilo = [
        ("adquisiciones@constructora-demo.cl", "jefe.obra@constructora-demo.cl", "Cotizacion acero A630 - Los Alamos",
         "Pedro, adjunto las 3 cotizaciones de acero. Las copie a la planilla comparativa a mano.\n"
         "El proveedor B es mas barato pero entrega en 10 dias. Necesito aprobacion del gerente para emitir la orden de compra.\n"
         "Saludos, Maria Gonzalez. Telefono: +56 9 8765 4321", "Mon, 11 Mar 2024 09:12:00 -0300"),
        ("jefe.obra@constructora-demo.cl", "adquisiciones@constructora-demo.cl", "RE: Cotizacion acero A630 - Los Alamos",
         "Maria, es urgente, estamos con atraso. Otra vez la solicitud de material se demoro una semana en aprobarse.\n"
         "Por favor reenvia al gerente. Pedro Soto", "Tue, 12 Mar 2024 18:40:00 -0300"),
    ]
    (RAW / "correos").mkdir(parents=True, exist_ok=True)
    for i, (de, para, asunto, cuerpo, fecha) in enumerate(hilo, 1):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"], m["Date"] = de, para, asunto, fecha
        m.set_content(cuerpo)
        (RAW / "correos" / f"correo_{i}.eml").write_bytes(bytes(m))


def whatsapp():
    lineas = [
        "12/03/24, 08:01 - Pedro Soto: Buenos dias, mando fotos del avance para el informe de obra",
        "12/03/24, 08:02 - Pedro Soto: <Multimedia omitido>",
        "12/03/24, 08:15 - Luis Rojas: Jefe, falta material para moldajes, hay que hacer solicitud de material",
        "12/03/24, 08:20 - Pedro Soto: Ok, la mando hoy a adquisiciones por correo",
        "13/03/24, 17:30 - Luis Rojas: El proveedor no llego con el acero, otra vez atrasado",
        "13/03/24, 17:35 - Pedro Soto: Llamo a Maria para que apure la orden de compra",
        "14/03/24, 07:50 - Carla Diaz: Recordatorio charla de prevencion 8:00, traer EPP",
        "14/03/24, 12:10 - Luis Rojas: Consulta tecnica: el plano de electricidad choca con el ducto de clima, quien responde?",
        "14/03/24, 12:30 - Pedro Soto: Lo paso como RFI a oficina tecnica, revisen el plano BIM",
    ]
    (RAW / "chats").mkdir(parents=True, exist_ok=True)
    (RAW / "chats" / "Chat de WhatsApp con Obra Los Alamos.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def foto_documento():
    from PIL import Image, ImageDraw, ImageFont

    fuente = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28) \
        if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").exists() else ImageFont.load_default()
    img = Image.new("RGB", (1100, 500), "white")
    d = ImageDraw.Draw(img)
    for i, t in enumerate(["GUIA DE DESPACHO N 4581", "Proveedor: Aceros del Sur", "Material: Acero A630 12mm",
                           "Cantidad: 3200 kg", "Recibido por bodeguero en obra", "Firma: ____________"]):
        d.text((40, 30 + i * 70), t, fill="black", font=fuente)
    (RAW / "fotos").mkdir(parents=True, exist_ok=True)
    img.save(RAW / "fotos" / "guia_despacho_4581.jpg")


def cuestionario():
    (BASE / "cuestionario.yaml").write_text("""empresa:
  nombre: Constructora Demo SpA
  sector: construccion
  empleados: 45
  herramientas_actuales: [Excel, WhatsApp, Gmail, AutoCAD]
  restricciones: []

procesos:
  - id: informes_obra
    nombre: Informes semanales de obra
    responsable: jefe de obra
    horas_mes: 16
    personas: 2
    costo_hora: 18
    dolor: 4
    notas: Se arma cada viernes copiando fotos de WhatsApp
  - id: compras_materiales
    nombre: Solicitud y compra de materiales
    responsable: adquisiciones
    horas_mes: 40
    personas: 2
    costo_hora: 14
    dolor: 5
    notas: Aprobaciones lentas generan atrasos en obra
  - id: presupuestos
    nombre: Presupuestos para licitaciones
    responsable: estudio de propuestas
    horas_mes: 60
    personas: 1
    costo_hora: 22
    dolor: 3

roles:
  - rol: Jefe de obra
    personas: 3
    nivel_digital: medio
  - rol: Adquisiciones
    personas: 2
    nivel_digital: medio
  - rol: Bodeguero
    personas: 3
    nivel_digital: bajo
""", encoding="utf-8")


def crear(base: Path = BASE) -> Path:
    global BASE, RAW
    BASE, RAW = base, base / "raw"
    for d in ("presupuestos", "compras", "informes"):
        (RAW / d).mkdir(parents=True, exist_ok=True)
    for f in (presupuesto, log_compras, informe_pdf, correos, whatsapp, foto_documento, cuestionario):
        f()
    return base


if __name__ == "__main__":
    print(f"Demo creada en {crear(Path(sys.argv[1]) if len(sys.argv) > 1 else BASE)}")
