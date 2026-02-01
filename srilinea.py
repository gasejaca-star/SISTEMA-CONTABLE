import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
import re
import json
import io
import os
import requests
import urllib3
from datetime import datetime
import xlsxwriter

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="RAPIDITO AI - Portal Contable", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"
URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"

# --- 2. MOTOR DE EXTRACCIÓN (PARA XMLs) ---
def extraer_datos_robusto(xml_input):
    try:
        # Si viene del SRI es bytes, si es subida manual es un archivo
        xml_content = xml_input.read() if hasattr(xml_input, 'read') else xml_input
        text = xml_content.decode('utf-8', errors='ignore') if isinstance(xml_content, bytes) else xml_content
        
        root = ET.fromstring(text)
        xml_data = None
        tipo_doc = "FC"
        
        # Buscar el nodo del comprobante (limpieza de CDATA/duplicados)
        for elem in root.iter():
            tag_l = elem.tag.lower()
            if 'notacredito' in tag_l: tipo_doc = "NC"
            elif 'liquidacioncompra' in tag_l: tipo_doc = "LC"
            if 'comprobante' in tag_l and elem.text:
                clean_inner = re.sub(r'<\?xml.*?\?>', '', elem.text).strip()
                xml_data = ET.fromstring(clean_inner)
                break
        
        if xml_data is None: xml_data = root

        def buscar(tags):
            for t in tags:
                f = xml_data.find(f".//{t}")
                if f is not None and f.text: return f.text
            return "0"

        # Cálculo de valores
        total = float(buscar(["importeTotal", "valorModificado", "total"]))
        subtotal = float(buscar(["totalSinImpuestos", "subtotal"]))
        base_0, base_12_15, iva_12_15 = 0.0, 0.0, 0.0
        
        for imp in xml_data.findall(".//totalImpuesto"):
            cod = imp.find("codigo").text if imp.find("codigo") is not None else ""
            cp = imp.find("codigoPorcentaje").text if imp.find("codigoPorcentaje") is not None else ""
            base = float(imp.find("baseImponible").text or 0)
            val = float(imp.find("valor").text or 0)
            if cod == "2": # IVA
                if cp == "0": base_0 += base
                else: base_12_15 += base; iva_12_15 += val
            
        m = -1 if tipo_doc == "NC" else 1
        fecha = buscar(["fechaEmision"])
        meses = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO",
                 "07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
        mes_n = meses.get(fecha.split('/')[1], "OTRO") if "/" in fecha else "OTRO"
        
        nombre = buscar(["razonSocial"]).upper().strip()
        info = st.session_state.memoria["empresas"].get(nombre, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
        
        # Subdetalle (Productos)
        detalles = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
        sub = " | ".join(detalles[:5]) if detalles else "Sin descripción"
        
        return {
            "MES": mes_n, "FECHA": fecha, "N. FACTURA": f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}",
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": buscar(["ruc"]), "NOMBRE": nombre,
            "DETALLE": info["DETALLE"], "MEMO": info["MEMO"], "BASE. 0": base_0 * m, 
            "BASE. 12 / 15": base_12_15 * m, "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": sub
        }
    except: return None

# --- 3. EXCEL ---
def generar_excel_rapidito(lista_datos):
    df = pd.DataFrame(lista_datos)
    orden = ["MES", "FECHA", "N. FACTURA", "TIPO DE DOCUMENTO", "RUC", "NOMBRE", "DETALLE", "MEMO", 
             "BASE. 0", "BASE. 12 / 15", "IVA.", "TOTAL", "SUBDETALLE"]
    
    # Asegurar columnas para evitar KeyError
    for c in orden:
        if c not in df.columns: df[c] = 0.0
    
    df = df[orden]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='COMPRAS', index=False)
        # Formato de dinero
        workbook = writer.book
        ws = writer.sheets['COMPRAS']
        money_fmt = workbook.add_format({'num_format': '#,##0.00'})
        for i, col in enumerate(df.columns):
            if i >= 8 and i <= 11: ws.set_column(i, i, 12, money_fmt)
            else: ws.set_column(i, i, 15)
    return output.getvalue()

# --- 4. INTERFAZ STREAMLIT ---
st.title(f"🚀 RAPIDITO AI")

tab1, tab2 = st.tabs(["📂 Subir tus XML", "📡 SRI (Subir Recibidos.txt)"])

with tab1:
    st.header("Procesar tus XML guardados")
    archivos_xml = st.file_uploader("Arrastra aquí tus archivos XML", type="xml", accept_multiple_files=True)
    if archivos_xml and st.button("GENERAR EXCEL DE MIS XML"):
        datos = [extraer_datos_robusto(a) for a in archivos_xml]
        datos = [d for d in datos if d is not None]
        if datos:
            st.download_button("📥 DESCARGAR REPORTE", generar_excel_rapidito(datos), "Reporte_XML_Locales.xlsx")

with tab2:
    st.header("Descarga Masiva desde el SRI")
    archivo_txt = st.file_uploader("Sube el archivo Recibidos.txt del SRI", type="txt")
    if archivo_txt and st.button("INICIAR DESCARGA Y EXCEL"):
        # Leer TXT con latin-1 para evitar errores de tildes
        contenido = archivo_txt.read().decode("latin-1", errors="ignore")
        claves = list(dict.fromkeys(re.findall(r'\d{49}', contenido)))
        
        if claves:
            barra = st.progress(0)
            datos_sri = []
            for i, c in enumerate(claves):
                payload = f'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion">
                              <soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{c}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body>
                              </soapenv:Envelope>'''
                try:
                    r = requests.post(URL_WS, data=payload, timeout=10)
                    if "<autorizacion>" in r.text:
                        resultado = extraer_datos_robusto(r.text)
                        if resultado: datos_sri.append(resultado)
                except: pass
                barra.progress((i+1)/len(claves))
            
            if datos_sri:
                st.success(f"¡Listo! Se procesaron {len(datos_sri)} facturas del SRI.")
                st.download_button("📊 DESCARGAR EXCEL SRI", generar_excel_rapidito(datos_sri), "Reporte_SRI.xlsx")
