import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
import re
import json
import io
import os
import requests
import zipfile
import urllib3
from datetime import datetime
import xlsxwriter

# --- 1. CONFIGURACIÓN Y SEGURIDAD ---
st.set_page_config(page_title="RAPIDITO AI - Portal Contable", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"
URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"

# --- FUNCIONES DE APOYO ---
def registrar_actividad(usuario, accion, cantidad=None):
    URL_PUENTE = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    payload = {"usuario": str(usuario), "accion": f"{accion} ({cantidad} XMLs)" if cantidad else accion}
    try: requests.post(URL_PUENTE, json=payload, timeout=10)
    except: pass

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        return {str(row['usuario']).strip(): str(row['clave']).strip() 
                for _, row in df.iterrows() if str(row['estado']).lower().strip() == 'activo'}
    except: return {}

# --- 2. SISTEMA DE LOGIN ---
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.sidebar.title("🔐 Acceso Clientes")
    user = st.sidebar.text_input("Usuario")
    password = st.sidebar.text_input("Contraseña", type="password")
    if st.sidebar.button("Iniciar Sesión"):
        db = cargar_usuarios()
        if user in db and db[user] == password:
            st.session_state.autenticado = True
            st.session_state.usuario_actual = user
            registrar_actividad(user, "ENTRÓ AL PORTAL")
            st.rerun()
        else: st.sidebar.error("Error de acceso.")
    st.stop()

# --- 3. MEMORIA DE APRENDIZAJE ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f:
            st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f:
        json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

# --- 4. MOTOR DE EXTRACCIÓN XML (EL ROBUSTO) ---
def extraer_datos_robusto(xml_file):
    try:
        # Si es un objeto BytesIO (de subida o SRI), leerlo
        if hasattr(xml_file, 'read'): xml_content = xml_file.read()
        else: xml_content = xml_file
        
        # Parseo inicial para encontrar el contenido del comprobante
        root = ET.fromstring(xml_content)
        xml_data = None
        tipo_doc = "FC"
        
        for elem in root.iter():
            tag_l = elem.tag.lower()
            if 'notacredito' in tag_l: tipo_doc = "NC"
            elif 'liquidacioncompra' in tag_l: tipo_doc = "LC"
            
            if 'comprobante' in tag_l and elem.text:
                # LIMPIEZA: Quitamos declaraciones XML duplicadas dentro del nodo
                clean_text = re.sub(r'<\?xml.*?\?>', '', elem.text).strip()
                xml_data = ET.fromstring(clean_text)
                break
        
        if xml_data is None: xml_data = root

        def buscar(tags):
            for t in tags:
                f = xml_data.find(f".//{t}")
                if f is not None and f.text: return f.text
            return "0"

        # Lógica de Impuestos y Totales
        total = float(buscar(["importeTotal", "valorModificado", "total"]))
        subtotal = float(buscar(["totalSinImpuestos", "subtotal"]))
        base_0, base_12_15, iva_12_15, ice_val = 0.0, 0.0, 0.0, 0.0
        otra_base, otro_monto_iva = 0.0, 0.0
        
        for imp in xml_data.findall(".//totalImpuesto"):
            cod = imp.find("codigo").text if imp.find("codigo") is not None else ""
            cod_por = imp.find("codigoPorcentaje").text if imp.find("codigoPorcentaje") is not None else ""
            base = float(imp.find("baseImponible").text or 0)
            valor = float(imp.find("valor").text or 0)
            if cod == "2": # IVA
                if cod_por == "0": base_0 += base
                elif cod_por in ["2", "3", "4", "10"]: base_12_15 += base; iva_12_15 += valor
                else: otra_base += base; otro_monto_iva += valor
            elif cod == "3": ice_val += valor
            
        no_iva = round(total - (subtotal + iva_12_15 + otro_monto_iva + ice_val), 2)
        if no_iva < 0.01: no_iva = 0.0
        m = -1 if tipo_doc == "NC" else 1
        
        # Fecha y Clasificación
        fecha = buscar(["fechaEmision"])
        meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO",
                      "07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
        mes_nombre = meses_dict.get(fecha.split('/')[1], "DESCONOCIDO") if "/" in fecha else "DESCONOCIDO"
        
        nombre_emisor = buscar(["razonSocial"]).upper().strip()
        info = st.session_state.memoria["empresas"].get(nombre_emisor, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
        
        # Extraer descripción de productos
        detalles = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
        subdetalle = " | ".join(detalles[:5]) if detalles else "Sin descripción"
        
        return {
            "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}",
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": buscar(["ruc"]), "NOMBRE": nombre_emisor,
            "DETALLE": info["DETALLE"], "MEMO": info["MEMO"],
            "NO IVA": no_iva * m, "MONTO ICE": ice_val * m, "OTRA BASE IVA": otra_base * m,
            "OTRO MONTO IVA": otro_monto_iva * m, "BASE. 0": base_0 * m, "BASE. 12 / 15": base_12_15 * m,
            "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": subdetalle
        }
    except: return None

# --- 5. GENERACIÓN DE EXCEL ---
def procesar_a_excel(lista_data):
    df = pd.DataFrame(lista_data)
    orden = ["MES", "FECHA", "N. FACTURA", "TIPO DE DOCUMENTO", "RUC", "NOMBRE", "DETALLE", "MEMO", 
             "NO IVA", "MONTO ICE", "OTRA BASE IVA", "OTRO MONTO IVA", "BASE. 0", "BASE. 12 / 15", "IVA.", "TOTAL", "SUBDETALLE"]
    df = df[orden]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        fmt_cont = '_-$ * #,##0.00_-;[Red]_-$ * -#,##0.00_-;_-$ * "-"??_-;_-@_-'
        f_header = workbook.add_format({'bold': True, 'align': 'center', 'border': 1, 'bg_color': '#D9EAD3'})
        f_total = workbook.add_format({'bold': True, 'num_format': fmt_cont, 'border': 1, 'bg_color': '#EFEFEF'})
        
        # Hoja Datos
        df.to_excel(writer, sheet_name='COMPRAS', index=False)
        ws = writer.sheets['COMPRAS']
        for i, col in enumerate(df.columns):
            ws.set_column(i, i, 15 if i < 8 else 12, workbook.add_format({'num_format': fmt_cont}) if i >= 8 else None)

        # Hoja Reporte Anual (Resumen con fórmulas)
        ws_r = workbook.add_worksheet('REPORTE ANUAL')
        cats = ["VIVIENDA", "SALUD", "EDUCACION", "ALIMENTACION", "VESTIMENTA", "TURISMO", "NO DEDUCIBLE", "SERVICIOS BASICOS"]
        iconos = ["🏠", "❤️", "🎓", "🛒", "🧢", "✈️", "🚫", "💡"]
        
        for i, (cat, ico) in enumerate(zip(cats, iconos)):
            ws_r.write(0, i+2, f"{ico} {cat.title()}", f_header)
        
        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        for r, mes in enumerate(meses):
            ws_r.write(r+1, 0, mes)
            # Ejemplo de fórmula SUMIFS (ajustada a las columnas de la hoja COMPRAS)
            for c, cat in enumerate(cats):
                formula = f"=SUMIFS('COMPRAS'!$P:$P,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")"
                ws_r.write_formula(r+1, c+2, formula)
                
    return output.getvalue()

# --- 6. INTERFAZ ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.usuario_actual}")

tab1, tab2 = st.tabs(["📂 Subida Manual", "📡 Descarga SRI"])

with tab1:
    up_xmls = st.file_uploader("XMLs de Compras", type="xml", accept_multiple_files=True)
    if up_xmls and st.button("GENERAR EXCEL RAPIDITO"):
        datos = [extraer_datos_robusto(x) for x in up_xmls if extraer_datos_robusto(x)]
        if datos:
            registrar_actividad(st.session_state.usuario_actual, "EXCEL MANUAL", len(datos))
            st.download_button("📥 DESCARGAR", procesar_a_excel(datos), "Reporte.xlsx")

with tab2:
    up_txt = st.file_uploader("Subir Recibidos.txt", type="txt")
    if up_txt and st.button("📥 INICIAR DESCARGA SRI"):
        claves = list(dict.fromkeys(re.findall(r'\d{49}', up_txt.read().decode("latin-1"))))
        if claves:
            prog = st.progress(0)
            lista_sri = []
            for i, cl in enumerate(claves):
                env = f'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion">
                          <soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{cl}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body>
                          </soapenv:Envelope>'''
                try:
                    r = requests.post(URL_WS, data=env, verify=False, timeout=10)
                    if "<autorizaciones>" in r.text:
                        res = extraer_datos_robusto(r.content)
                        if res: lista_sri.append(res)
                except: pass
                prog.progress((i+1)/len(claves))
            
            if lista_sri:
                st.success(f"Procesados {len(lista_sri)} de {len(claves)}")
                st.download_button("📊 DESCARGAR EXCEL SRI", procesar_a_excel(lista_sri), "Reporte_SRI.xlsx")
