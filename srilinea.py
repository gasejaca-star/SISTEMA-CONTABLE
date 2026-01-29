import streamlit as st
import xml.etree.ElementTree as ET
import pandas as pd
import re
import json
import io
import os
from datetime import datetime
import xlsxwriter

# --- 1. CONFIGURACIÓN Y SEGURIDAD ---
st.set_page_config(page_title="RAPIDITO AI - Portal Contable", layout="wide", page_icon="📊")

# Reemplaza con tu link de "Publicar en la web" como CSV
URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        usuarios = {
            str(row['usuario']).strip(): str(row['clave']).strip() 
            for _, row in df.iterrows() 
            if str(row['estado']).lower().strip() == 'activo'
        }
        return usuarios
    except Exception:
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
            st.rerun()
        else:
            st.sidebar.error("Usuario o contraseña incorrectos.")
    
    st.info("### Bienvenido a RAPIDITO\nPor favor, ingresa tus credenciales en el panel izquierdo.")
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

# --- 4. MOTOR DE EXTRACCIÓN (TU LÓGICA DE CÁLCULO) ---
def extraer_datos_robusto(xml_file):
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
    except Exception:
        return None

# --- 5. INTERFAZ ---
st.title(f"🚀 RAPIDITO AI - Bienvenido, {st.session_state.usuario_actual}")

with st.sidebar:
    st.header("1. Aprendizaje")
    uploaded_excel = st.file_uploader("Cargar Excel Maestro", type=["xlsx"])
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
        st.success("Aprendizaje actualizado.")
    
    if st.button("Cerrar Sesión"):
        st.session_state.autenticado = False
        st.rerun()

st.header("2. Procesar Comprobantes")
uploaded_xmls = st.file_uploader("Sube tus archivos XML aquí", type=["xml"], accept_multiple_files=True)

if uploaded_xmls and st.button("GENERAR REPORTE"):
    lista_data = []
    progress_bar = st.progress(0)
    
    for idx, xml in enumerate(uploaded_xmls):
        res = extraer_datos_robusto(xml)
        if res: lista_data.append(res)
        progress_bar.progress((idx + 1) / len(uploaded_xmls))
    
    if lista_data:
        df = pd.DataFrame(lista_data)
        orden = ["MES", "FECHA", "N. FACTURA", "TIPO DE DOCUMENTO", "RUC", "NOMBRE", "DETALLE", "MEMO", 
                 "NO IVA", "MONTO ICE", "OTRA BASE IVA", "OTRO MONTO IVA", "BASE. 0", "BASE. 12 / 15", "IVA.", "TOTAL", "SUBDETALLE"]
        df = df[orden]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            workbook = writer.book
            
            # FORMATOS
            fmt_contabilidad = '_-$ * #,##0.00_-;[Red]_-$ * -#,##0.00_-;_-$ * "-"??_-;_-@_-'
            f_titulo_anio = workbook.add_format({'bold': True, 'font_size': 12})
            f_header_top = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'border': 1, 'bg_color': 'white', 'text_wrap': True})
            f_subheader_gris = workbook.add_format({'bold': True, 'align': 'center', 'border': 1, 'bg_color': '#F2F2F2'})
            f_data_blanco = workbook.add_format({'num_format': fmt_contabilidad, 'border': 1, 'bg_color': 'white'})
            f_data_gris = workbook.add_format({'num_format': fmt_contabilidad, 'border': 1, 'bg_color': '#FAFAFA'})
            f_total_row = workbook.add_format({'bold': True, 'num_format': fmt_contabilidad, 'top': 2, 'border': 1, 'bg_color': '#EFEFEF'})
            f_meses_col = workbook.add_format({'bold': False, 'border': 1, 'bg_color': '#F2F2F2'})

            # HOJA COMPRAS
            df.to_excel(writer, sheet_name='COMPRAS', index=False)
            ws_compras = writer.sheets['COMPRAS']
            ws_compras.set_column('A:Q', 15)

            # HOJA REPORTE ANUAL
            ws_reporte = workbook.add_worksheet('REPORTE ANUAL')
            ws_reporte.set_column('A:A', 12)
            ws_reporte.set_column('B:K', 14)

            ws_reporte.write('A1', datetime.now().year, f_titulo_anio)
            ws_reporte.merge_range('B1:B2', "Negocios y\nServicios", f_header_top)
            
            cats_personales = ["VIVIENDA", "SALUD", "EDUCACION", "ALIMENTACION", "VESTIMENTA", "TURISMO", "NO DEDUCIBLE", "SERVICIOS BASICOS"]
            iconos = ["🏠", "❤️", "🎓", "🛒", "🧢", "✈️", "🚫", "💡"]
            
            for i, (cat, icono) in enumerate(zip(cats_personales, iconos)):
                col_idx = i + 2
                ws_reporte.write(0, col_idx, icono, f_header_top)
                ws_reporte.write(1, col_idx, cat.title(), f_header_top)
            
            ws_reporte.merge_range('K1:K2', "Total Mes", f_header_top)
            ws_reporte.write('B3', "PROFESIONALES", f_subheader_gris)
            ws_reporte.merge_range('C3:J3', "GASTOS PERSONALES", f_subheader_gris)
            
            meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
            start_row = 3
            for r, mes in enumerate(meses):
                fila_excel = start_row + r + 1
                current_row_idx = start_row + r
                formato_fila = f_data_gris if r % 2 != 0 else f_data_blanco
                ws_reporte.write(current_row_idx, 0, mes.title(), f_meses_col)
                
                # Fórmulas SUMIFS EXACTAS (sumando las 6 columnas de bases/iva de COMPRAS)
                f_prof = (f"=SUMIFS('COMPRAS'!$I:$I,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                          f"SUMIFS('COMPRAS'!$J:$J,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                          f"SUMIFS('COMPRAS'!$K:$K,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                          f"SUMIFS('COMPRAS'!$L:$L,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                          f"SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")+"
                          f"SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"PROFESIONAL\")")
                ws_reporte.write_formula(current_row_idx, 1, f_prof, formato_fila)

                for c, cat in enumerate(cats_personales):
                    # Suma Base 12 y Base 0 para Gastos Personales
                    f_pers = (f"=SUMIFS('COMPRAS'!$M:$M,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")+"
                              f"SUMIFS('COMPRAS'!$N:$N,'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$G:$G,\"{cat}\")")
                    ws_reporte.write_formula(current_row_idx, c + 2, f_pers, formato_fila)
                
                ws_reporte.write_formula(current_row_idx, 10, f"=SUM(B{fila_excel}:J{fila_excel})", formato_fila)

            # Totales finales
            for col_idx in range(1, 11):
                letra = xlsxwriter.utility.xl_col_to_name(col_idx)
                ws_reporte.write_formula(16, col_idx, f"=SUM({letra}4:{letra}16)", f_total_row)
            ws_reporte.write(16, 0, "Total General", f_total_row)

        st.success("¡Reporte RAPIDITO generado con éxito!")
        st.download_button(
            label="📥 DESCARGAR EXCEL",
            data=output.getvalue(),
            file_name=f"Reporte_Rapidito_{datetime.now().strftime('%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
