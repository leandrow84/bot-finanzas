import os
import json
import base64
import re
import threading
from datetime import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from anthropic import Anthropic
import requests as http_requests

app = Flask(__name__)
anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Estado de sesión en memoria
# {numero: {"responsable": "nombre", "pendiente": {...}, "timestamp": datetime}}
sesiones = {}

DIAS_ES = {0: "Lunes", 1: "Martes", 2: "Miercoles", 3: "Jueves",
           4: "Viernes", 5: "Sabado", 6: "Domingo"}

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

def get_responsable_turno(local):
    if local == "FABRICA":
        return None  # Fabrica siempre pregunta
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet("TURNOS")
        data = ws.get_all_records()
        ahora = datetime.now()
        dia_actual = DIAS_ES[ahora.weekday()]
        hora_actual = ahora.hour + ahora.minute / 60

        for row in data:
            if str(row["LOCAL"]).strip().upper() != local.upper():
                continue
            if str(row["DIA"]).strip() != dia_actual:
                continue
            hora_inicio = float(str(row["HORA_INICIO"]).replace(":", ".").replace(",", "."))
            hora_fin = float(str(row["HORA_FIN"]).replace(":", ".").replace(",", "."))
            if hora_inicio <= hora_actual < hora_fin:
                return str(row["RESPONSABLE"]).strip()
        return None
    except Exception as e:
        print(f"Error obteniendo turno: {e}")
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
            return "❌ No hay mas espacio en ingresos."
        ws.update(values=[[fecha, descripcion, categoria, float(monto), responsable, observaciones, comprobante]], range_name=f"A{row}:G{row}")
        return f"✅ Ingreso: {descripcion} — ${float(monto):,.2f}"
    except Exception as e:
        return f"❌ Error ingreso '{descripcion}': {e}"

def cargar_posnet(local, fecha, debito, credito, cuotas, total, observaciones=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 39, 68)
        if not row:
            return "❌ No hay mas espacio en Posnet."
        ws.update(values=[[fecha, "Cierre Posnet", float(debito), float(credito), float(cuotas), float(total), observaciones]], range_name=f"A{row}:G{row}")
        return f"✅ Posnet cargado — Total: ${float(total):,.2f}\n💳 Debito: ${float(debito):,.2f} | Credito: ${float(credito):,.2f} | Cuotas: ${float(cuotas):,.2f}"
    except Exception as e:
        return f"❌ Error Posnet: {e}"

def cargar_gasto(local, fecha, descripcion, monto, categoria="General", proveedor="", observaciones="", comprobante=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 73, 102)
        if not row:
            return "❌ No hay mas espacio en gastos."
        ws.update(values=[[fecha, descripcion, categoria, float(monto), proveedor, observaciones, comprobante]], range_name=f"A{row}:G{row}")
        return f"✅ Gasto: {descripcion} — ${float(monto):,.2f}"
    except Exception as e:
        return f"❌ Error gasto '{descripcion}': {e}"

def cargar_factura(local, nro_factura, proveedor, fecha_emision, fecha_vencimiento, monto_total):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 107, 126)
        if not row:
            return "❌ No hay mas espacio en facturas."
        ws.update(values=[[nro_factura, proveedor, fecha_emision, fecha_vencimiento, float(monto_total), 0]], range_name=f"A{row}:F{row}")
        return f"✅ Factura: {proveedor} N{nro_factura} — ${float(monto_total):,.2f}"
    except Exception as e:
        return f"❌ Error factura '{proveedor}': {e}"

def cargar_pago(local, fecha, nro_factura, proveedor, monto, forma_pago="Efectivo", banco="", observaciones=""):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        row = next_empty_row(ws, 1, 131, 150)
        if not row:
            return "❌ No hay mas espacio en pagos."
        ws.update(values=[[fecha, nro_factura, proveedor, float(monto), forma_pago, banco, observaciones]], range_name=f"A{row}:G{row}")
        facturas = ws.get("A107:F126")
        for i, fila in enumerate(facturas):
            if fila and str(fila[0]).strip() == str(nro_factura).strip():
                fact_row = 107 + i
                pagado_actual = float(fila[5]) if fila[5] else 0
                ws.update_cell(fact_row, 6, pagado_actual + float(monto))
                break
        return f"✅ Pago: {proveedor} N{nro_factura} — ${float(monto):,.2f} ({forma_pago})"
    except Exception as e:
        return f"❌ Error pago '{proveedor}': {e}"

def registrar_fecha_cashflow(local, fecha):
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(local)
        fechas = ws.col_values(1)[154:185]
        if any(f.strip() == fecha.strip() for f in fechas if f):
            return None
        row = next_empty_row(ws, 1, 155, 185)
        if row:
            ws.update_cell(row, 1, fecha)
        return row
    except Exception as e:
        print(f"Error registrando fecha cashflow: {e}")
        return None

def enviar_whatsapp(to_number, mensaje):
    try:
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
        sandbox_number = os.environ.get("TWILIO_SANDBOX_NUMBER", "+14155238886")
        client = TwilioClient(twilio_sid, twilio_token)
        client.messages.create(
            from_=f"whatsapp:{sandbox_number}",
            to=f"whatsapp:+{to_number}",
            body=mensaje
        )
        print(f"Mensaje enviado a {to_number}")
    except Exception as e:
        print(f"Error enviando mensaje: {e}")

def descargar_imagen(media_url):
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
    response = http_requests.get(media_url, auth=(twilio_sid, twilio_token))
    print(f"STATUS DESCARGA: {response.status_code}")
    if response.status_code == 200:
        return base64.standard_b64encode(response.content).decode("utf-8"), response.headers.get("Content-Type", "image/jpeg")
    print(f"ERROR DESCARGA: {response.text[:200]}")
    return None, None

def analizar_imagen(image_b64, media_type, local):
    hoy = datetime.now().strftime("%d/%m/%Y")
    prompt = f"""Analiza esta imagen. Puede ser un remito, factura o cierre de Posnet.

Hoy es {hoy}. Local: {local}.

Si es un REMITO o FACTURA, responde con este JSON:
{{"tipo": "factura", "nro_factura": "...", "proveedor": "...", "fecha_emision": "DD/MM/YYYY", "fecha_vencimiento": "DD/MM/YYYY", "monto_total": 0000}}

Si es un CIERRE DE POSNET, responde con este JSON:
{{"tipo": "posnet", "fecha": "DD/MM/YYYY", "debito": 0000, "credito": 0000, "cuotas": 0000, "total": 0000, "observaciones": ""}}

Reglas:
- Si no hay fecha de vencimiento en la factura, usa la misma que la de emision
- Si no encontras algun monto de Posnet, pone 0
- El total del Posnet es la suma de todos los cobros con tarjeta
- Responde SOLO el JSON, sin explicaciones"""

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)

def interpretar_mensaje(mensaje, local):
    hoy = datetime.now().strftime("%d/%m/%Y")
    system_prompt = f"""Sos un asistente que interpreta mensajes en español para cargar datos financieros.
Hoy es {hoy}. El local es: {local}.

El mensaje puede contener UNA o MULTIPLES operaciones. Devolvé SIEMPRE una lista JSON con todas las operaciones encontradas.

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
- Siempre devolvé un array aunque sea con un solo elemento
- Si dice "hoy" usa {hoy}
- Si dice "ayer" calcula la fecha de ayer
- Si no menciona fecha, usa {hoy}
- Si no menciona categoria, inferila del contexto
- Montos siempre como numero sin simbolos ni puntos de miles
- Fechas siempre en formato DD/MM/YYYY
- Responde SOLO el array JSON"""

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": mensaje}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)

def procesar_operacion(datos, local, responsable=""):
    tipo = datos.get("tipo")
    if tipo == "ingreso":
        registrar_fecha_cashflow(local, datos["fecha"])
        return cargar_ingreso(local, datos["fecha"], datos["descripcion"],
                              datos["monto"], datos.get("categoria", "General"),
                              responsable, datos.get("observaciones", ""),
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
    elif tipo == "posnet":
        registrar_fecha_cashflow(local, datos["fecha"])
        return cargar_posnet(local, datos["fecha"],
                             datos.get("debito", 0), datos.get("credito", 0),
                             datos.get("cuotas", 0), datos["total"],
                             datos.get("observaciones", ""))
    elif tipo == "consulta":
        return (f"🤖 Hola! Puedo registrar:\n\n"
                f"💰 *Ingresos:* 'ingreso 15000 venta mostrador'\n"
                f"📤 *Gastos:* 'gasto 3500 luz'\n"
                f"🧾 *Facturas:* 'factura 001 Coca-Cola vence 30/04 8000'\n"
                f"✅ *Pagos:* 'pague factura 001 5000 transferencia'\n"
                f"📸 *Foto:* manda foto de remito o cierre Posnet\n\n"
                f"📌 Podes cargar varios en un mensaje:\n"
                f"'Gastos: Remis 20000, Moto 45000'\n\n"
                f"📍 Estas operando: *{local}*")
    else:
        return "❌ No reconoci la operacion."

@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "").replace("whatsapp:", "")
    num_media = int(request.values.get("NumMedia", 0))

    print(f"MENSAJE DE: {from_number} | Media: {num_media}")

    resp = MessagingResponse()
    msg = resp.message()

    local = get_local_from_number(from_number)
    if not local:
        msg.body(f"⛔ Numero no autorizado: {from_number}")
        return str(resp)

    sesion = sesiones.get(from_number, {})
    ahora = datetime.now()

    # Limpiar sesión si tiene más de 8 horas
    if sesion.get("timestamp"):
        diff = (ahora - sesion["timestamp"]).seconds / 3600
        if diff > 8:
            sesion = {}
            sesiones[from_number] = {}

    # Si está esperando confirmación de responsable
    if sesion.get("esperando_responsable"):
        respuesta = incoming_msg.strip().upper()
        responsable_sugerido = sesion.get("responsable_sugerido", "")
        operaciones_pendientes = sesion.get("pendiente", [])

        if respuesta == "SI":
            responsable = responsable_sugerido
        else:
            responsable = incoming_msg.strip()

        sesiones[from_number] = {
            "responsable": responsable,
            "timestamp": ahora,
            "esperando_responsable": False
        }

        # Procesar operaciones pendientes
        if operaciones_pendientes:
            resultados = []
            for op in operaciones_pendientes:
                resultado = procesar_operacion(op, local, responsable)
                resultados.append(resultado)
            total_ops = len(resultados)
            resumen = f"👤 *{responsable}* | 📋 *{local}*\n{total_ops} operacion{'es' if total_ops > 1 else ''} registrada{'s' if total_ops > 1 else ''}:\n\n"
            resumen += "\n".join(resultados)
            msg.body(resumen)
        else:
            msg.body(f"👤 Hola *{responsable}*! Listo para cargar operaciones en *{local}*.")
        return str(resp)

    # Determinar responsable
    responsable_actual = sesion.get("responsable", "")

    if not responsable_actual:
        responsable_turno = get_responsable_turno(local)

        if local.upper() == "FABRICA":
            pregunta = f"👤 Hola! ¿Quién está cargando en *{local}*? Escribí tu nombre."
        elif responsable_turno:
            pregunta = f"👤 Hola! ¿Sos *{responsable_turno}*?\nRespondé *SI* para confirmar o escribí tu nombre."
        else:
            pregunta = f"👤 Hola! ¿Quién está cargando en *{local}*? Escribí tu nombre."

        # Guardar operaciones pendientes si hay mensaje
        if num_media > 0 or incoming_msg:
            if num_media > 0:
                sesiones[from_number] = {
                    "esperando_responsable": True,
                    "responsable_sugerido": responsable_turno or "",
                    "pendiente_media": {
                        "url": request.values.get("MediaUrl0"),
                        "type": request.values.get("MediaContentType0", "image/jpeg")
                    },
                    "timestamp": ahora
                }
            else:
                try:
                    lista_ops = interpretar_mensaje(incoming_msg, local)
                    sesiones[from_number] = {
                        "esperando_responsable": True,
                        "responsable_sugerido": responsable_turno or "",
                        "pendiente": lista_ops,
                        "timestamp": ahora
                    }
                except:
                    sesiones[from_number] = {
                        "esperando_responsable": True,
                        "responsable_sugerido": responsable_turno or "",
                        "pendiente": [],
                        "timestamp": ahora
                    }

        msg.body(pregunta)
        return str(resp)

    # Ya tiene responsable identificado
    responsable = responsable_actual

    # Procesar imagen
    if num_media > 0:
        media_url = request.values.get("MediaUrl0")
        media_type = request.values.get("MediaContentType0", "image/jpeg")
        print(f"Imagen recibida: {media_url}")
        msg.body("📸 Imagen recibida, procesando... Un momento.")

        def procesar_en_background():
            try:
                image_b64, detected_type = descargar_imagen(media_url)
                if not image_b64:
                    enviar_whatsapp(from_number, "❌ No pude descargar la imagen. Intenta de nuevo.")
                    return
                datos = analizar_imagen(image_b64, detected_type or media_type, local)
                resultado = procesar_operacion(datos, local, responsable)
                resumen = f"👤 *{responsable}* | 📸 *{local}*\n\n{resultado}"
                enviar_whatsapp(from_number, resumen)
            except Exception as e:
                enviar_whatsapp(from_number, f"❌ No pude interpretar la imagen.\nError: {e}")

        threading.Thread(target=procesar_en_background).start()
        return str(resp)

    # Procesar texto
    if incoming_msg:
        # Comando para cambiar responsable
        if incoming_msg.lower() in ["cambiar usuario", "cambiar responsable", "soy otro"]:
            sesiones[from_number] = {}
            msg.body("👤 Ok! ¿Quién está cargando ahora? Escribí tu nombre.")
            return str(resp)

        try:
            lista_operaciones = interpretar_mensaje(incoming_msg, local)
        except Exception as e:
            msg.body(f"❌ No pude entender el mensaje.\nError: {e}")
            return str(resp)

        resultados = []
        for operacion in lista_operaciones:
            resultado = procesar_operacion(operacion, local, responsable)
            resultados.append(resultado)

        total_ops = len(resultados)
        resumen = f"👤 *{responsable}* | 📋 *{local}*\n{total_ops} operacion{'es' if total_ops > 1 else ''} registrada{'s' if total_ops > 1 else ''}:\n\n"
        resumen += "\n".join(resultados)

        # Actualizar timestamp de sesión
        sesiones[from_number]["timestamp"] = ahora
        msg.body(resumen)

    return str(resp)

@app.route("/", methods=["GET"])
def home():
    return "✅ Bot Finanzas activo!", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
