import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
import re
import json
import io
import os
import requests
from datetime import datetime
import xlsxwriter

# --- 1. CONFIGURACIÓN Y SEGURIDAD ---
st.set_page_config(page_title="RAPIDITO AI - Master Web", layout="wide", page_icon="📊")

URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"

def registrar_actividad(usuario, accion, cantidad=None):
    URL_PUENTE = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    detalle_accion = f"{accion} ({cantidad} docs)" if cantidad is not None else accion
    payload = {"usuario": str(usuario), "accion": str(detalle_accion)}
    try: requests.post(URL_PUENTE, json=payload, timeout=10)
    except: pass

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        return {str(row['usuario']).strip(): str(row['clave']).strip() for _, row in df.iterrows() if str(row['estado']).lower().strip() == 'activo'}
    except: return {}

# --- 2. LOGIN ---
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.sidebar.title("🔐 Acceso Clientes")
    user = st.sidebar.text_input("Usuario")
    password = st.sidebar.text_input("Contraseña", type="password")
    if st.sidebar.button("Iniciar Sesión"):
        db = cargar_usuarios()
        if user in db and db[user] == password:
            st.session_state.autenticado, st.session_state.usuario_actual = True, user
            registrar_actividad(user, "ENTRÓ AL PORTAL")
            st.rerun()
        else: st.sidebar.error("Error de acceso.")
    st.stop()

# --- 3. MEMORIA ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f: st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f: json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

# --- 4. MOTORES DE PROCESAMIENTO ---

def extraer_datos_xml(xml_file):
    try:
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
            cod = imp.find("codigo").text or ""
            cod_por = imp.find("codigoPorcentaje").text or ""
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
            meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
            mes_num = fecha.split('/')[1]
            mes_nombre = meses_dict.get(mes_num, "DESCONOCIDO")

        nombre_emisor = buscar(["razonSocial"]).upper().strip()
        info = st.session_state.memoria["empresas"].get(nombre_emisor, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
        
        return {
            "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}",
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": buscar(["ruc"]), "NOMBRE": nombre_emisor,
            "DETALLE": info["DETALLE"], "MEMO": info["MEMO"], "NO IVA": no_iva * m, "MONTO ICE": ice_val * m, 
            "OTRA BASE IVA": otra_base * m, "OTRO MONTO IVA": otro_monto_iva * m, "BASE. 0": base_0 * m, 
            "BASE. 12 / 15": base_12_15 * m, "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": "XML MANUAL"
        }
    except: return None

def procesar_txt_sri(file):
    try:
        df_txt = pd.read_csv(file, sep='\t', encoding='utf-8')
        lista = []
        meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
        for _, fila in df_txt.iterrows():
            nombre = str(fila['RAZON_SOCIAL_EMISOR']).upper().strip()
            fecha = str(fila['FECHA_EMISION'])
            mes_nombre = meses_dict.get(fecha.split('/')[1], "DESCONOCIDO") if "/" in fecha else "DESCONOCIDO"
            info = st.session_state.memoria["empresas"].get(nombre, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
            
            lista.append({
                "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": str(fila['SERIE_COMPROBANTE']),
                "TIPO DE DOCUMENTO": "FC", "RUC": str(fila['RUC_EMISOR']), "NOMBRE": nombre,
                "DETALLE": info["DETALLE"], "MEMO": info["MEMO"], "NO IVA": 0.0, "MONTO ICE": 0.0,
                "OTRA BASE IVA": 0.0, "OTRO MONTO IVA": 0.0, "BASE. 0": 0.0, 
                "BASE. 12 / 15": float(fila['VALOR_SIN_IMPUESTOS']), "IVA.": float(fila['IVA']),
                "TOTAL": float(fila['IMPORTE_TOTAL']), "SUBDETALLE": "IMPORTADO TXT SRI"
            })
        return lista
    except: return []

# --- 5. INTERFAZ ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.usuario_actual}")

with st.sidebar:
    if st.session_state.usuario_actual == "GABRIEL":
        st.header("⚙️ Entrenamiento Master")
        uploaded_excel = st.file_uploader("Subir Excel Maestro", type=["xlsx"])
        if uploaded_excel:
            df_e = pd.read_excel(uploaded_excel)
            df_e.columns = [c.upper().strip() for c in df_e.columns]
            for _, f in df_e.iterrows():
                n = str(f.get("NOMBRE", "")).upper().strip()
                if n and n != "NAN":
                    st.session_state.memoria["empresas"][n] = {"DETALLE": str(f.get("DETALLE", "OTROS")).upper(), "MEMO": str(f.get("MEMO", "PROFESIONAL")).upper()}
            guardar_memoria(); st.success("Cerebro actualizado.")
    if st.button("Cerrar Sesión"): st.session_state.autenticado = False; st.rerun()

col1, col2 = st.columns(2)
with col1:
    st.subheader("📁 XMLs Manuales")
    up_xmls = st.file_uploader("Archivos XML", type=["xml"], accept_multiple_files=True)
with col2:
    st.subheader("📋 SRI (Archivo TXT)")
    up_txt = st.file_uploader("Subir Recibidos.txt", type=["txt"])

if st.button("🚀 GENERAR REPORTE CONSOLIDADO"):
    datos_finales = []
    if up_xmls:
        for x in up_xmls:
            r = extraer_datos_xml(x)
            if r: datos_finales.append(r)
    if up_txt:
        r_t = procesar_txt_sri(up_txt)
        if r_t: datos_finales.extend(r_t)

    if datos_finales:
        registrar_actividad(st.session_state.usuario_actual, "GENERÓ REPORTE", len(datos_finales))
        df = pd.DataFrame(datos_finales)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            # --- FORMATOS ---
            f_cont = '_-$ * #,##0.00_-;[Red]_-$ * -#,##0.00_-;_-$ * "-"??_-;_-@_-'
            f_head = workbook.add_format({'bold':True, 'align':'center', 'border':1, 'bg_color':'#FFFFFF', 'text_wrap':True})
            f_data = workbook.add_format({'num_format': f_cont, 'border':1})
            f_total = workbook.add_format({'bold':True, 'num_format':f_cont, 'border':1, 'bg_color':'#EFEFEF'})

            # PESTAÑA COMPRAS
            df.to_excel(writer, sheet_name='COMPRAS', index=False)
            ws = writer.sheets['COMPRAS']
            for i, col in enumerate(df.columns): ws.set_column(i, i, 15)

            # PESTAÑA REPORTE ANUAL
            ws_r = workbook.add_worksheet('REPORTE ANUAL')
            ws_r.set_column('A:K', 15)
            ws_r.merge_range('B1:B2', "Negocios y\nServicios", f_head)
            cats = ["VIVIENDA", "SALUD", "EDUCACION", "ALIMENTACION", "VESTIMENTA", "TURISMO", "NO DEDUCIBLE", "SERVICIOS BASICOS"]
            for i, c in enumerate(cats): ws_r.write(1, i+2, c.title(), f_head)
            ws_r.write(1, 10, "Total Mes", f_head)
            
            meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
            for r, mes in enumerate(meses):
                ws_r.write(r+2, 0, mes, f_data)
                # Fórmulas de resumen
                f_prof = f"=SUMIFS('COMPRAS'!$P:$P,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")"
                ws_r.write_formula(r+2, 1, f_prof, f_data)
                for c, cat in enumerate(cats):
                    f_cat = f"=SUMIFS('COMPRAS'!$P:$P,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")"
                    ws_r.write_formula(r+2, c+2, f_cat, f_data)
                ws_r.write_formula(r+2, 10, f"=SUM(B{r+3}:J{r+3})", f_data)

        st.success(f"¡Listo! Se procesaron {len(datos_finales)} documentos.")
        st.download_button("📥 DESCARGAR EXCEL", output.getvalue(), f"Reporte_{st.session_state.usuario_actual}.xlsx")
    else: st.warning("No hay datos para procesar.")
