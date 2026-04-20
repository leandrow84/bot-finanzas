import os
import json
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from anthropic import Anthropic
from datetime import datetime
import re

app = Flask(__name__)
anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_sheets_client():
    raw = os.environ.get("GCREDS")
    creds_data = json.loads(raw)
    creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_sheets_client()
    return client.open_by_key(os.environ.get("SPREADSHEET_ID"))

def get_local_from_number(phone_number):
    try:
        ss = get_spreadsheet()
        config = ss.worksheet("CONFIG")
        data = config.get_all_records()
        phone_clean = str(phone_number).strip().lstrip("+")
        for row in data:
            config_num = str(row["NUMERO"]).strip().lstrip("+")
            if config_num == phone_clean:
                return row["LOCAL"]
        return None
    except Exception as e:
        print(f"Error obteniendo local: {e}")
        return None

def next_empty_row(worksheet, col, start_row, end_row):
    values = worksheet.col_values(col)
    for i in range(start_row - 1, end_row):
        if i >= len(values) or values[i] == "":
            return i + 1
    return None

def cargar_ingreso(local, fecha, descripcion, monto, categoria="General", responsable="", observaciones="", comprobante=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 5, 34)
        if not row:
            return "❌ No hay más espacio en ingresos. Ampliá la planilla."
        ws.update(f"A{row}:G{row}", [[fecha, descripcion, categoria, float(monto), responsable, observaciones, comprobante]])
        return f"✅ Ingreso: {descripcion} — ${float(monto):,.2f}"
    except Exception as e:
        return f"❌ Error ingreso '{descripcion}': {e}"

def cargar_gasto(local, fecha, descripcion, monto, categoria="General", proveedor="", observaciones="", comprobante=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 39, 68)
        if not row:
            return "❌ No hay más espacio en gastos. Ampliá la planilla."
        ws.update(f"A{row}:G{row}", [[fecha, descripcion, categoria, float(monto), proveedor, observaciones, comprobante]])
        return f"✅ Gasto: {descripcion} — ${float(monto):,.2f}"
    except Exception as e:
        return f"❌ Error gasto '{descripcion}': {e}"

def cargar_factura(local, nro_factura, proveedor, fecha_emision, fecha_vencimiento, monto_total):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 73, 92)
        if not row:
            return "❌ No hay más espacio en facturas. Ampliá la planilla."
        ws.update(f"A{row}:F{row}", [[nro_factura, proveedor, fecha_emision, fecha_vencimiento, float(monto_total), 0]])
        return f"✅ Factura: {proveedor} Nº{nro_factura} — ${float(monto_total):,.2f}"
    except Exception as e:
        return f"❌ Error factura '{proveedor}': {e}"

def cargar_pago(local, fecha, nro_factura, proveedor, monto, forma_pago="Efectivo", banco="", observaciones=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 97, 116)
        if not row:
            return "❌ No hay más espacio en pagos. Ampliá la planilla."
        ws.update(f"A{row}:G{row}", [[fecha, nro_factura, proveedor, float(monto), forma_pago, banco, observaciones]])
        facturas = ws.get(f"A73:F92")
        for i, fila in enumerate(facturas):
            if fila and str(fila[0]).strip() == str(nro_factura).strip():
                fact_row = 73 + i
                pagado_actual = float(fila[5]) if fila[5] else 0
                ws.update_cell(fact_row, 6, pagado_actual + float(monto))
                break
        return f"✅ Pago: {proveedor} Nº{nro_factura} — ${float(monto):,.2f} ({forma_pago})"
    except Exception as e:
        return f"❌ Error pago '{proveedor}': {e}"

def registrar_fecha_cashflow(local, fecha):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        fechas = ws.col_values(1)[123:154]
        if any(f.strip() == fecha.strip() for f in fechas if f):
            return None
        row = next_empty_row(ws, 1, 124, 154)
        if row:
            ws.update_cell(row, 1, fecha)
        return row
    except Exception as e:
        print(f"Error registrando fecha cashflow: {e}")
        return None

def interpretar_mensaje(mensaje, local):
    hoy = datetime.now().strftime("%d/%m/%Y")
    system_prompt = f"""Sos un asistente que interpreta mensajes en español para cargar datos financieros.
Hoy es {hoy}. El local es: {local}.

El mensaje puede contener UNA o MÚLTIPLES operaciones. Analizá todo el mensaje y devolvé SIEMPRE una lista JSON con todas las operaciones encontradas.

Respondé ÚNICAMENTE con un array JSON válido, por ejemplo:
[
  {{"tipo": "gasto", "fecha": "DD/MM/YYYY", "descripcion": "Remis", "monto": 20000, "categoria": "Transporte", "proveedor": "", "observaciones": "", "comprobante": ""}},
  {{"tipo": "gasto", "fecha": "DD/MM/YYYY", "descripcion": "Moto", "monto": 45000, "categoria": "Transporte", "proveedor": "", "observaciones": "", "comprobante": ""}}
]

Estructuras disponibles:

INGRESO:
{{"tipo": "ingreso", "fecha": "DD/MM/YYYY", "descripcion": "...", "monto": 0000, "categoria": "...", "responsable": "", "observaciones": "", "comprobante": ""}}

GASTO:
{{"tipo": "gasto", "fecha": "DD/MM/YYYY", "descripcion": "...", "monto": 0000, "categoria": "...", "proveedor": "", "observaciones": "", "comprobante": ""}}

FACTURA:
{{"tipo": "factura", "nro_factura": "...", "proveedor": "...", "fecha_emision": "DD/MM/YYYY", "fecha_vencimiento": "DD/MM/YYYY", "monto_total": 0000}}

PAGO:
{{"tipo": "pago", "fecha": "DD/MM/YYYY", "nro_factura": "...", "proveedor": "...", "monto": 0000, "forma_pago": "Efectivo", "banco": "", "observaciones": ""}}

CONSULTA:
{{"tipo": "consulta", "mensaje": "..."}}

Reglas:
- Siempre devolvé un array, aunque sea con un solo elemento
- Si dice "hoy" usá {hoy}
- Si dice "ayer" calculá la fecha de ayer
- Si no menciona fecha, usá {hoy}
- Si no menciona categoría, inferila del contexto (Transporte, Servicios, Insumos, etc.)
- Montos siempre como número sin símbolos ni puntos de miles
- Fechas siempre en formato DD/MM/YYYY
- Respondé SOLO el array JSON, sin explicaciones ni texto adicional"""

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": mensaje}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)

def procesar_operacion(datos, local):
    tipo = datos.get("tipo")
    if tipo == "ingreso":
        registrar_fecha_cashflow(local, datos["fecha"])
        return cargar_ingreso(local, datos["fecha"], datos["descripcion"],
                              datos["monto"], datos.get("categoria", "General"),
                              datos.get("responsable", ""), datos.get("observaciones", ""),
                              datos.get("comprobante", ""))
    elif tipo == "gasto":
        registrar_fecha_cashflow(local, datos["fecha"])
        return cargar_gasto(local, datos["fecha"], datos["descripcion"],
                            datos["monto"], datos.get("categoria", "General"),
                            datos.get("proveedor", ""), datos.get("observaciones", ""),
                            datos.get("comprobante", ""))
    elif tipo == "factura":
        return cargar_factura(local, datos["nro_factura"], datos["proveedor"],
                              datos["fecha_emision"], datos["fecha_vencimiento"],
                              datos["monto_total"])
    elif tipo == "pago":
        registrar_fecha_cashflow(local, datos["fecha"])
        return cargar_pago(local, datos["fecha"], datos["nro_factura"],
                           datos["proveedor"], datos["monto"],
                           datos.get("forma_pago", "Efectivo"),
                           datos.get("banco", ""), datos.get("observaciones", ""))
    elif tipo == "consulta":
        return (f"🤖 Hola! Puedo registrar:\n\n"
                f"💰 *Ingresos:* 'ingreso 15000 venta mostrador'\n"
                f"📤 *Gastos:* 'gasto 3500 luz'\n"
                f"🧾 *Facturas:* 'factura 001 Coca-Cola vence 30/04 8000'\n"
                f"✅ *Pagos:* 'pague factura 001 5000 transferencia'\n\n"
                f"📌 También podés cargar varios en un mensaje:\n"
                f"'Gastos: Remis 20000, Moto 45000, Papelera 25000'\n\n"
                f"📍 Estás operando: *{local}*")
    else:
        return "❌ No reconocí la operación."

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").replace("whatsapp:", "")

    print(f"MENSAJE RECIBIDO DE: {from_number}")

    resp = MessagingResponse()
    msg = resp.message()

    local = get_local_from_number(from_number)
    if not local:
        msg.body(f"⛔ Número no autorizado: {from_number}\nContactá al administrador.")
        return str(resp)

    try:
        lista_operaciones = interpretar_mensaje(incoming_msg, local)
    except Exception as e:
        msg.body(f"❌ No pude entender el mensaje. Intentá de nuevo.\nError: {e}")
        return str(resp)

    resultados = []
    for operacion in lista_operaciones:
        resultado = procesar_operacion(operacion, local)
        resultados.append(resultado)

    # Resumen final
    total_ops = len(resultados)
    resumen = f"📋 *{local}* — {total_ops} operación{'es' if total_ops > 1 else ''} registrada{'s' if total_ops > 1 else ''}:\n\n"
    resumen += "\n".join(resultados)

    msg.body(resumen)
    return str(resp)

@app.route("/", methods=["GET"])
def home():
    return "✅ Bot Finanzas activo!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
