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

# Desactivar avisos de certificados (Fiddler Mode)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# --- 4. MOTOR DE DESCARGA WEB SERVICE (SRI ENGINE) ---

def descargar_xmls_ws(file_txt):
    try:
        # Usamos latin-1 para evitar el error 'utf-8' codec can't decode
        content = file_txt.read().decode("latin-1")
        claves = list(dict.fromkeys(re.findall(r'\d{49}', content)))
        
        if not claves:
            st.error("❌ No se encontraron claves de acceso en el archivo.")
            return None

        # URL y Headers capturados de Fiddler
        url_ws = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
        headers = {
            "Content-Type": "text/xml;charset=UTF-8",
            "User-Agent": "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.2; WOW64; Trident/7.0)",
            "Cookie": "TS010a7529=0115ac86d2859bb60ce3e314743f6a0cee3bcf365d8cb7ce8e5ef76bbc09c6509733dfb5dcf2a1b1dc29feb273505a1d0838bc427c",
            "SOAPAction": ""
        }

        zip_buffer = io.BytesIO()
        exitos = 0
        
        # UI: Barra de progreso y log
        progreso_barra = st.progress(0)
        progreso_texto = st.empty()
        
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED) as zip_file:
            for i, clave in enumerate(claves):
                payload = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion">
                   <soapenv:Body>
                      <ec:autorizacionComprobante>
                         <claveAccesoComprobante>{clave}</claveAccesoComprobante>
                      </ec:autorizacionComprobante>
                   </soapenv:Body>
                </soapenv:Envelope>"""
                
                try:
                    # Intento de descarga (timeout de 15s como en tu código original)
                    r = requests.post(url_ws, data=payload, headers=headers, verify=False, timeout=15)
                    
                    if r.status_code == 200 and "<autorizaciones>" in r.text:
                        zip_file.writestr(f"{clave}.xml", r.text)
                        exitos += 1
                except Exception:
                    pass
                
                # Actualizar progreso en la interfaz
                avance = (i + 1) / len(claves)
                progreso_barra.progress(avance)
                progreso_texto.text(f"🚀 Procesando: {int(avance*100)}% ({i+1}/{len(claves)})")

        if exitos > 0:
            st.success(f"🏁 ¡Proceso finalizado! Se descargaron {exitos} comprobantes con éxito.")
            return zip_buffer.getvalue()
        else:
            st.error("🚨 Error de conexión: El servidor del SRI no respondió o las claves no están autorizadas.")
            return None
            
    except Exception as e:
        st.error(f"⚠️ Error al leer el archivo: {str(e)}")
        return None

# --- 5. MOTOR DE EXTRACCIÓN XML (PARA EL EXCEL) ---

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
            "BASE. 12 / 15": base_12_15 * m, "IVA.": iva_12_15 * m, "TOTAL": total * m, "SUBDETALLE": "XML"
        }
    except: return None

# --- 6. INTERFAZ ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.usuario_actual}")

with st.sidebar:
    if st.session_state.usuario_actual == "GABRIEL":
        st.header("⚙️ Entrenamiento")
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
    st.subheader("📊 Generar Reporte Excel")
    up_xmls = st.file_uploader("Subir XMLs Manuales", type=["xml"], accept_multiple_files=True)
    if st.button("📝 PROCESAR Y GENERAR EXCEL"):
        if up_xmls:
            datos_finales = [extraer_datos_xml(x) for x in up_xmls if extraer_datos_xml(x) is not None]
            if datos_finales:
                df = pd.DataFrame(datos_finales)
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df.to_excel(writer, sheet_name='COMPRAS', index=False)
                st.success(f"Reporte listo con {len(datos_finales)} documentos.")
                st.download_button("📥 DESCARGAR EXCEL", output.getvalue(), f"Reporte_{st.session_state.usuario_actual}.xlsx")
        else: st.warning("Sube primero los archivos XML.")

with col2:
    st.subheader("📦 Descargar Comprobantes (SRI)")
    up_txt = st.file_uploader("Subir Recibidos.txt", type=["txt"])
    if st.button("📥 INICIAR DESCARGA XMLs"):
        if up_txt:
            zip_final = descargar_xmls_ws(up_txt)
            if zip_final:
                st.download_button("💾 GUARDAR ARCHIVO ZIP", zip_final, "comprobantes_sri.zip", "application/zip")
                registrar_actividad(st.session_state.usuario_actual, "DESCARGÓ XMLs WS")
        else: st.warning("Sube primero el archivo .txt del SRI.")

