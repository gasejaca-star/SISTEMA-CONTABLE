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

def registrar_actividad(usuario, accion, cantidad=None):
    URL_PUENTE = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    detalle_accion = f"{accion} ({cantidad} XMLs)" if cantidad is not None else accion
    payload = {"usuario": str(usuario), "accion": str(detalle_accion)}
    try:
        requests.post(URL_PUENTE, json=payload, timeout=10)
    except:
        pass

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        return {str(row['usuario']).strip(): str(row['clave']).strip() 
                for _, row in df.iterrows() if str(row['estado']).lower().strip() == 'activo'}
    except:
        return {}

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
        else:
            st.sidebar.error("Usuario o contraseña incorrectos.")
    st.stop()

# --- 3. MEMORIA DE APRENDIZAJE ---
if 'memoria' not in st.session_state:
    archivo_memoria = "conocimiento_contable.json"
    if os.path.exists(archivo_memoria):
        with open(archivo_memoria, "r", encoding="utf-8") as f:
            st.session_state.memoria = json.load(f)
    else:
        st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f:
        json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

# --- 4. MOTOR DE EXTRACCIÓN XML (COPIADO EXACTAMENTE DE TU CÓDIGO) ---
def extraer_datos_robusto(xml_file):
    try:
        # Nota: ET.parse funciona con objetos de archivo (subidos) o BytesIO (SRI)
        tree = ET.parse(xml_file)
        root = tree.getroot()
        xml_data = None
        tipo_doc = "FC"
        for elem in root.iter():
            tag_lower = elem.tag.lower()
            if 'notacredito' in tag_lower: tipo_doc = "NC"
            elif 'liquidacioncompra' in tag_lower: tipo_doc = "LC"
            if 'comprobante' in tag_lower and elem.text:
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
        base_0, base_12_15, iva_12_15 = 0.0, 0.0, 0.0
        otra_base, otro_monto_iva, ice_val = 0.0, 0.0, 0.0
        for imp in xml_data.findall(".//totalImpuesto"):
            cod = imp.find("codigo").text if imp.find("codigo") is not None else ""
            cod_por = imp.find("codigoPorcentaje").text if imp.find("codigoPorcentaje") is not None else ""
            base = float(imp.find("baseImponible").text or 0)
            valor = float(imp.find("valor").text or 0)
            if cod == "2":
                if cod_por == "0": base_0 += base
                elif cod_por in ["2", "3", "4", "10"]: base_12_15 += base; iva_12_15 += valor
                else: otra_base += base; otro_monto_iva += valor
            elif cod == "3": ice_val += valor
            
        no_iva = round(total - (subtotal + iva_12_15 + otro_monto_iva + ice_val), 2)
        if no_iva < 0.01: no_iva = 0.0
        m = -1 if tipo_doc == "NC" else 1
        
        fecha = buscar(["fechaEmision"])
        mes_nombre = "DESCONOCIDO"
        if "/" in fecha:
            try:
                meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO",
                             "07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
                mes_num = fecha.split('/')[1]
                mes_nombre = meses_dict.get(mes_num, "DESCONOCIDO")
            except: pass
            
        nombre_emisor = buscar(["razonSocial"]).upper().strip()
        info = st.session_state.memoria["empresas"].get(nombre_emisor, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
        items_raw = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
        subdetalle = " | ".join(items_raw[:5]) if items_raw else "Sin descripción"
        
        return {
            "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}",
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": buscar(["ruc"]), "NOMBRE": nombre_emisor,
            "DETALLE": info["DETALLE"], "MEMO": info["MEMO"],
            "NO IVA": no_iva * m, "MONTO ICE": ice_val * m, "OTRA BASE IVA": otra_base * m,
            "OTRO MONTO IVA": otro_monto_iva * m, "BASE. 0": base_0 * m, "BASE. 12 / 15": base_12_15 * m,
            "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": subdetalle
        }
    except Exception: return None

# --- 5. INTERFAZ Y GENERACIÓN DE EXCEL ---
def procesar_a_excel(lista_data):
    df = pd.DataFrame(lista_data)
    orden = ["MES", "FECHA", "N. FACTURA", "TIPO DE DOCUMENTO", "RUC", "NOMBRE", "DETALLE", "MEMO", 
             "NO IVA", "MONTO ICE", "OTRA BASE IVA", "OTRO MONTO IVA", "BASE. 0", "BASE. 12 / 15", "IVA.", "TOTAL", "SUBDETALLE"]
    df = df[orden]
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        fmt_contabilidad = '_-$ * #,##0.00_-;[Red]_-$ * -#,##0.00_-;_-$ * "-"??_-;_-@_-'
        f_header = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': '#FFFFFF', 'text_wrap': True})
        f_subh = workbook.add_format({'bold': True, 'align': 'center', 'border': 1, 'bg_color': '#F2F2F2'})
        f_data_b = workbook.add_format({'num_format': fmt_contabilidad, 'border': 1, 'bg_color': 'white'})
        f_data_g = workbook.add_format({'num_format': fmt_contabilidad, 'border': 1, 'bg_color': '#FAFAFA'})
        f_total = workbook.add_format({'bold': True, 'num_format': fmt_contabilidad, 'border': 1, 'bg_color': '#EFEFEF'})

        df.to_excel(writer, sheet_name='COMPRAS', index=False)
        ws_reporte = workbook.add_worksheet('REPORTE ANUAL')
        ws_reporte.set_column('A:K', 14)
        ws_reporte.merge_range('B1:B2', "Negocios y\nServicios", f_header)
        
        cats = ["VIVIENDA", "SALUD", "EDUCACION", "ALIMENTACION", "VESTIMENTA", "TURISMO", "NO DEDUCIBLE", "SERVICIOS BASICOS"]
        iconos = ["🏠", "❤️", "🎓", "🛒", "🧢", "✈️", "🚫", "💡"]
        for i, (cat, ico) in enumerate(zip(cats, iconos)):
            ws_reporte.write(0, i+2, ico, f_header)
            ws_reporte.write(1, i+2, cat.title(), f_header)
        
        ws_reporte.merge_range('K1:K2', "Total Mes", f_header)
        ws_reporte.write('B3', "PROFESIONALES", f_subh)
        ws_reporte.merge_range('C3:J3', "GASTOS PERSONALES", f_subh)

        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
        for r, mes in enumerate(meses):
            fila_ex = r + 4
            fmt = f_data_g if r % 2 != 0 else f_data_b
            ws_reporte.write(r+3, 0, mes.title(), fmt)
            
            f_prof = (f"=SUMIFS('COMPRAS'!$I:$I,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                      f"SUMIFS('COMPRAS'!$J:$J,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                      f"SUMIFS('COMPRAS'!$K:$K,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                      f"SUMIFS('COMPRAS'!$L:$L,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                      f"SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                      f"SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")")
            ws_reporte.write_formula(r+3, 1, f_prof, fmt)

            for c, cat in enumerate(cats):
                f_pers = (f"=SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")+"
                          f"SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")")
                ws_reporte.write_formula(r+3, c+2, f_pers, fmt)
            
            ws_reporte.write_formula(r+3, 10, f"=SUM(B{fila_ex}:J{fila_ex})", fmt)

        for col in range(1, 11):
            letra = xlsxwriter.utility.xl_col_to_name(col)
            ws_reporte.write_formula(15, col, f"=SUM({letra}4:{letra}15)", f_total)
        ws_reporte.write(15, 0, "TOTAL", f_total)
    return output.getvalue()

st.title(f"🚀 RAPIDITO - {st.session_state.usuario_actual}")

with st.sidebar:
    if st.session_state.usuario_actual == "GABRIEL":
        st.header("1. Herramientas Master")
        uploaded_excel = st.file_uploader("Entrenar con Excel Maestro", type=["xlsx"])
        if uploaded_excel:
            df_entrena = pd.read_excel(uploaded_excel)
            df_entrena.columns = [c.upper().strip() for c in df_entrena.columns]
            for _, fila in df_entrena.iterrows():
                nombre = str(fila.get("NOMBRE", "")).upper().strip()
                if nombre and nombre != "NAN":
                    st.session_state.memoria["empresas"][nombre] = {
                        "DETALLE": str(fila.get("DETALLE", "OTROS")).upper(),
                        "MEMO": str(fila.get("MEMO", "PROFESIONAL")).upper() 
                    }
            guardar_memoria()
            st.success("Cerebro actualizado.")
    
    if st.button("Cerrar Sesión"):
        registrar_actividad(st.session_state.usuario_actual, "SALIÓ")
        st.session_state.autenticado = False
        st.rerun()

# --- FLUJO DE TRABAJO ---
tab_manual, tab_sri = st.tabs(["📂 Subir XMLs", "📡 Descarga SRI (TXT)"])

with tab_manual:
    st.header("Subida de Comprobantes")
    uploaded_xmls = st.file_uploader("Subir archivos XML", type=["xml"], accept_multiple_files=True)
    if uploaded_xmls and st.button("GENERAR EXCEL RAPIDITO"):
        lista_data = []
        for xml in uploaded_xmls:
            res = extraer_datos_robusto(xml)
            if res: lista_data.append(res)
        
        if lista_data:
            registrar_actividad(st.session_state.usuario_actual, "GENERÓ EXCEL MANUAL", len(uploaded_xmls))
            excel = procesar_a_excel(lista_data)
            st.download_button("📥 DESCARGAR REPORTE", excel, f"Rapidito_{datetime.now().strftime('%H%M%S')}.xlsx")

with tab_sri:
    st.header("Descarga Masiva SRI")
    uploaded_txt = st.file_uploader("Subir Recibidos.txt", type=["txt"])
    if uploaded_txt and st.button("🚀 INICIAR DESCARGA"):
        content = uploaded_txt.read().decode("latin-1")
        claves = list(dict.fromkeys(re.findall(r'\d{49}', content)))
        
        if claves:
            progreso = st.progress(0)
            status = st.empty()
            lista_sri = []
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "a") as zf:
                for i, clave in enumerate(claves):
                    url = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
                    env = f'''<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion">
                              <soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{clave}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body>
                              </soapenv:Envelope>'''
                    try:
                        r = requests.post(url, data=env, headers={'Content-Type':'text/xml'}, verify=False, timeout=10)
                        if r.status_code == 200 and "<autorizaciones>" in r.text:
                            zf.writestr(f"{clave}.xml", r.text)
                            # Convertimos la respuesta a BytesIO para que ET.parse lo lea igual que un archivo
                            xml_io = io.BytesIO(r.content)
                            res = extraer_datos_robusto(xml_io)
                            if res: lista_sri.append(res)
                    except: pass
                    progreso.progress((i + 1) / len(claves))
                    status.text(f"Procesando {i+1} de {len(claves)}")
            
            if lista_sri:
                st.success("¡Proceso completado!")
                st.download_button("📦 DESCARGAR XMLs (ZIP)", zip_buffer.getvalue(), "comprobantes.zip")
                st.download_button("📊 DESCARGAR EXCEL", procesar_a_excel(lista_sri), "Reporte_SRI.xlsx")
