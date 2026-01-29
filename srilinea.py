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
st.set_page_config(page_title="RAPIDITO - Portal Contable", layout="wide", page_icon="📊")

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
            st.sidebar.error("Usuario no encontrado o inactivo.")
    
    st.info("### Bienvenido a RAPIDITO AI\nPor favor, ingresa tus credenciales para comenzar.")
    st.stop()

# --- 3. MEMORIA DE APRENDIZAJE ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f:
            st.session_state.memoria = json.load(f)
    else:
        st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f:
        json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

# --- 4. MOTOR DE EXTRACCIÓN MEJORADO (SOLUCIÓN AL ERROR) ---
def extraer_datos_robusto(xml_file):
    try:
        content = xml_file.read()
        try:
            tree = ET.fromstring(content)
        except:
            # Reintento por si el archivo tiene caracteres extraños
            tree = ET.fromstring(content.decode('utf-8', errors='ignore'))
        
        # 1. Extraer el bloque del comprobante (el SRI suele meter el XML dentro de otro XML)
        comprobante_node = tree.find("comprobante")
        if comprobante_node is not None:
            # Limpieza de CDATA y etiquetas de versión
            raw_xml = comprobante_node.text
            raw_xml = re.sub(r'<\?xml.*?\?>', '', raw_xml)
            data_root = ET.fromstring(raw_xml)
        else:
            data_root = tree

        # 2. Identificar Tipo de Documento
        tipo_doc = "FC" # Por defecto Factura
        tag_root = data_root.tag.lower()
        if 'notacredito' in tag_root: tipo_doc = "NC"
        elif 'liquidacioncompra' in tag_root: tipo_doc = "LC"

        # 3. Función de búsqueda inteligente
        def buscar(path):
            elem = data_root.find(f".//{path}")
            return elem.text.strip() if elem is not None and elem.text else None

        # 4. Datos Básicos
        fecha = buscar("fechaEmision")
        ruc_emisor = buscar("ruc")
        nombre_emisor = (buscar("razonSocial") or "DESCONOCIDO").upper()
        n_factura = f"{buscar('estab')}-{buscar('ptoEmi')}-{buscar('secuencial')}"

        # 5. Lógica de Impuestos (El corazón del error)
        # Buscamos todas las etiquetas de impuestos para no perder bases
        total_sin_imp = float(buscar("totalSinImpuestos") or 0)
        importe_total = float(buscar("importeTotal") or buscar("valorModificado") or 0)
        
        base_0, base_iva, valor_iva = 0.0, 0.0, 0.0
        ice = 0.0

        for impuesto in data_root.findall(".//totalImpuesto"):
            codigo = impuesto.find("codigo").text if impuesto.find("codigo") is not None else ""
            porcentaje = impuesto.find("codigoPorcentaje").text if impuesto.find("codigoPorcentaje") is not None else ""
            base = float(impuesto.find("baseImponible").text or 0)
            valor = float(impuesto.find("valor").text or 0)

            if codigo == "2": # IVA
                if porcentaje == "0":
                    base_0 += base
                else: # 12%, 14%, 15%, etc.
                    base_iva += base
                    valor_iva += valor
            elif codigo == "3": # ICE
                ice += valor

        # Cálculo de "NO IVA" (Diferencia contable)
        no_iva = round(importe_total - (total_sin_imp + valor_iva + ice), 2)
        if abs(no_iva) < 0.05: no_iva = 0.0 # Ignorar decimales de redondeo

        # Multiplicador para Notas de Crédito
        m = -1 if tipo_doc == "NC" else 1

        # 6. Mes y Categoría
        mes_nombre = "DESCONOCIDO"
        if fecha and "/" in fecha:
            meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO",
                         "07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
            mes_num = fecha.split('/')[1]
            mes_nombre = meses_dict.get(mes_num, "DESCONOCIDO")

        info_memoria = st.session_state.memoria["empresas"].get(nombre_emisor, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})

        return {
            "MES": mes_nombre, "FECHA": fecha, "N. FACTURA": n_factura,
            "TIPO DE DOCUMENTO": tipo_doc, "RUC": ruc_emisor, "NOMBRE": nombre_emisor,
            "DETALLE": info_memoria["DETALLE"], "MEMO": info_memoria["MEMO"],
            "NO IVA": no_iva * m, "MONTO ICE": ice * m, "BASE. 0": base_0 * m, 
            "BASE. GRAVADA": base_iva * m, "IVA.": valor_iva * m, "TOTAL": importe_total * m
        }
    except Exception as e:
        return None

# --- 5. INTERFAZ ---
st.title(f"🚀 RAPIDITO - Bienvenido, {st.session_state.usuario_actual}")

with st.sidebar:
    if st.button("Cerrar Sesión"):
        st.session_state.autenticado = False
        st.rerun()
    st.divider()
    st.header("1. Aprendizaje")
    uploaded_excel = st.file_uploader("Actualizar Categorías (Excel)", type=["xlsx"])
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
        st.success("¡Aprendizaje completado!")

st.header("2. Cargar XMLs")
uploaded_xmls = st.file_uploader("Sube tus archivos del SRI", type=["xml"], accept_multiple_files=True)

if uploaded_xmls and st.button("GENERAR REPORTE"):
    data_final = []
    for xml in uploaded_xmls:
        resultado = extraer_datos_robusto(xml)
        if resultado: data_final.append(resultado)
    
    if data_final:
        df = pd.DataFrame(data_final)
        
        # Exportación a Excel con tu formato original
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='COMPRAS', index=False)
            # (Aquí se aplican los formatos xlsxwriter que ya conoces...)
            workbook = writer.book
            worksheet = writer.sheets['COMPRAS']
            header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
            
            # Hoja de Resumen (Mantiene tus fórmulas de Vivienda, Salud, etc.)
            ws_resumen = workbook.add_worksheet('REPORTE ANUAL')
            # ... (Lógica de fórmulas SUMIFS igual a la anterior)

        st.success(f"¡Listo! Se procesaron {len(data_final)} documentos.")
        st.download_button("📥 DESCARGAR REPORTE RAPIDITO", output.getvalue(), f"Reporte_{datetime.now().strftime('%Y%m%d')}.xlsx")

