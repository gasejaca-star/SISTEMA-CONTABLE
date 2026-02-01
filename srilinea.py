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
st.set_page_config(page_title="RAPIDITO AI - Master Web", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"

def registrar_actividad(usuario, accion, cantidad=None):
    URL_PUENTE = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    detalle = f"{accion} ({cantidad} XMLs)" if cantidad is not None else accion
    try: requests.post(URL_PUENTE, json={"usuario": str(usuario), "accion": str(detalle)}, timeout=10)
    except: pass

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        return {str(row['usuario']).strip(): str(row['clave']).strip() for _, row in df.iterrows() if str(row['estado']).lower().strip() == 'activo'}
    except: return {}

# --- 2. LOGIN ---
if "autenticado" not in st.session_state: st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.sidebar.title("🔐 Acceso")
    u, p = st.sidebar.text_input("Usuario"), st.sidebar.text_input("Contraseña", type="password")
    if st.sidebar.button("Entrar"):
        db = cargar_usuarios()
        if u in db and db[u] == p:
            st.session_state.autenticado, st.session_state.usuario_actual = True, u
            registrar_actividad(u, "ENTRÓ AL PORTAL")
            st.rerun()
        else: st.sidebar.error("Error de credenciales.")
    st.stop()

# --- 3. MEMORIA (Cerebro Contable) ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f: st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

# --- 4. MOTOR DE EXTRACCIÓN ROBUSTO (Tu lógica exacta) ---
def extraer_datos_robusto(xml_content):
    try:
        if isinstance(xml_content, bytes):
            root = ET.fromstring(xml_content)
        else:
            tree = ET.parse(xml_content)
            root = tree.getroot()
            
        xml_data = None
        tipo_doc = "FC"
        for elem in root.iter():
            tag_l = elem.tag.lower()
            if 'notacredito' in tag_l: tipo_doc = "NC"
            elif 'liquidacioncompra' in tag_l: tipo_doc = "LC"
            if 'comprobante' in tag_l and elem.text:
                try:
                    clean_text = re.sub(r'<\?xml.*?\?>', '', elem.text).strip()
                    xml_data = ET.fromstring(clean_text)
                    break
                except: continue
        if xml_data is None: xml_data = root

        def buscar(tags):
            for t in tags:
                f = xml_data.find(f".//{t}")
                if f is not None and f.text: return f.text
            return "0"

        total = float(buscar(["importeTotal", "valorModificado", "total"]))
        subtotal = float(buscar(["totalSinImpuestos", "subtotal"]))
        base_0, base_12_15, iva_12_15, otra_base, otro_monto_iva, ice_val = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        
        for imp in xml_data.findall(".//totalImpuesto"):
            cod = imp.find("codigo").text if imp.find("codigo") is not None else ""
            cod_p = imp.find("codigoPorcentaje").text if imp.find("codigoPorcentaje") is not None else ""
            b = float(imp.find("baseImponible").text or 0)
            v = float(imp.find("valor").text or 0)
            if cod == "2":
                if cod_p == "0": base_0 += b
                elif cod_p in ["2", "3", "4", "10"]: base_12_15 += b; iva_12_15 += v
                else: otra_base += b; otro_monto_iva += v
            elif cod == "3": ice_val += v
            
        no_iva = round(total - (subtotal + iva_12_15 + otro_monto_iva + ice_val), 2)
        if no_iva < 0.01: no_iva = 0.0
        m = -1 if tipo_doc == "NC" else 1
        
        fecha = buscar(["fechaEmision"])
        meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
        mes_nombre = meses_dict.get(fecha.split('/')[1], "DESCONOCIDO") if "/" in fecha else "DESCONOCIDO"
        
        emisor = buscar(["razonSocial"]).upper().strip()
        info = st.session_state.memoria["empresas"].get(emisor, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
        
        items = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
        
        return {
            "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}",
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": buscar(["ruc"]), "NOMBRE": emisor,
            "DETALLE": info["DETALLE"], "MEMO": info["MEMO"], "NO IVA": no_iva * m, "MONTO ICE": ice_val * m, 
            "OTRA BASE IVA": otra_base * m, "OTRO MONTO IVA": otro_monto_iva * m, "BASE. 0": base_0 * m, 
            "BASE. 12 / 15": base_12_15 * m, "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": " | ".join(items[:5])
        }
    except: return None

# --- 5. GENERADOR DE EXCEL MAESTRO (Tu lógica exacta de Reporte Anual) ---
def generar_excel_profesional(lista_data):
    df = pd.DataFrame(lista_data)
    orden = ["MES", "FECHA", "N. FACTURA", "TIPO DE DOCUMENTO", "RUC", "NOMBRE", "DETALLE", "MEMO", "NO IVA", "MONTO ICE", "OTRA BASE IVA", "OTRO MONTO IVA", "BASE. 0", "BASE. 12 / 15", "IVA.", "TOTAL", "SUBDETALLE"]
    df = df[orden]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        # Formatos
        fmt_cont = '_-$ * #,##0.00_-;[Red]_-$ * -#,##0.00_-;_-$ * "-"??_-;_-@_-'
        f_header = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'text_wrap': True})
        f_subh = workbook.add_format({'bold': True, 'align': 'center', 'border': 1, 'bg_color': '#F2F2F2'})
        f_data_b = workbook.add_format({'num_format': fmt_cont, 'border': 1})
        f_total = workbook.add_format({'bold': True, 'num_format': fmt_cont, 'border': 1, 'bg_color': '#EFEFEF'})

        # Pestaña Compras
        df.to_excel(writer, sheet_name='COMPRAS', index=False)
        
        # Pestaña Reporte Anual
        ws = workbook.add_worksheet('REPORTE ANUAL')
        ws.set_column('A:K', 15)
        ws.merge_range('B1:B2', "Negocios y\nServicios", f_header)
        cats = ["VIVIENDA", "SALUD", "EDUCACION", "ALIMENTACION", "VESTIMENTA", "TURISMO", "NO DEDUCIBLE", "SERVICIOS BASICOS"]
        iconos = ["🏠", "❤️", "🎓", "🛒", "🧢", "✈️", "🚫", "💡"]
        for i, (cat, ico) in enumerate(zip(cats, iconos)):
            ws.write(0, i+2, ico, f_header)
            ws.write(1, i+2, cat.title(), f_header)
        
        ws.merge_range('K1:K2', "Total Mes", f_header)
        ws.write('B3', "PROFESIONALES", f_subh)
        ws.merge_range('C3:J3', "GASTOS PERSONALES", f_subh)

        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        for r, mes in enumerate(meses):
            fila = r + 4
            ws.write(r+3, 0, mes.title(), f_data_b)
            # Fórmulas SUMIFS exactas
            f_prof = f"=SUMIFS('COMPRAS'!$I:$I,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+SUMIFS('COMPRAS'!$J:$J,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+SUMIFS('COMPRAS'!$K:$K,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+SUMIFS('COMPRAS'!$L:$L,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")"
            ws.write_formula(r+3, 1, f_prof, f_data_b)
            for c, cat in enumerate(cats):
                f_pers = f"=SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")+SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")"
                ws.write_formula(r+3, c+2, f_pers, f_data_b)
            ws.write_formula(r+3, 10, f"=SUM(B{fila}:J{fila})", f_data_b)
        
        for col in range(1, 11):
            letra = xlsxwriter.utility.xl_col_to_name(col)
            ws.write_formula(15, col, f"=SUM({letra}4:{letra}15)", f_total)
    return output.getvalue()

# --- 6. INTERFAZ PRINCIPAL ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.usuario_actual}")

col1, col2 = st.columns(2)

with col1:
    st.subheader("📊 Reporte desde XMLs")
    up_xmls = st.file_uploader("Subir archivos XML", type=["xml"], accept_multiple_files=True)
    if st.button("📝 GENERAR EXCEL RAPIDITO"):
        if up_xmls:
            datos = [extraer_datos_robusto(x) for x in up_xmls if extraer_datos_robusto(x)]
            if datos:
                excel = generar_excel_profesional(datos)
                st.download_button("📥 DESCARGAR REPORTE", excel, f"Rapidito_{datetime.now().strftime('%H%M%S')}.xlsx")
                registrar_actividad(st.session_state.usuario_actual, "GENERÓ EXCEL", len(up_xmls))

with col2:
    st.subheader("📦 Descarga y Avance SRI")
    up_txt = st.file_uploader("Subir Recibidos.txt", type=["txt"])
    if st.button("📥 INICIAR DESCARGA E INFORME"):
        if up_txt:
            content = up_txt.read().decode("latin-1")
            claves = list(dict.fromkeys(re.findall(r'\d{49}', content)))
            if claves:
                url_ws = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
                headers = {"Content-Type": "text/xml;charset=UTF-8", "User-Agent": "Mozilla/4.0"}
                
                zip_buffer = io.BytesIO()
                datos_para_excel = []
                progreso = st.progress(0)
                status = st.empty()
                
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zf:
                    for i, clave in enumerate(claves):
                        payload = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{clave}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>"""
                        try:
                            r = requests.post(url_ws, data=payload, headers=headers, verify=False, timeout=10)
                            if r.status_code == 200 and "<autorizaciones>" in r.text:
                                zf.writestr(f"{clave}.xml", r.text)
                                # Extraer datos inmediatamente para el Excel
                                info = extraer_datos_robusto(io.BytesIO(r.content))
                                if info: datos_para_excel.append(info)
                        except: pass
                        progreso.progress((i+1)/len(claves))
                        status.text(f"Procesando: {i+1}/{len(claves)}")
                
                if datos_para_excel:
                    st.success(f"✅ Descarga Exitosa: {len(datos_para_excel)} archivos.")
                    st.download_button("💾 DESCARGAR ZIP", zip_buffer.getvalue(), "comprobantes.zip")
                    excel_auto = generar_excel_profesional(datos_para_excel)
                    st.download_button("📊 DESCARGAR REPORTE DE DESCARGAS", excel_auto, "Reporte_Automatico.xlsx")
            else: st.error("No se encontraron claves.")
