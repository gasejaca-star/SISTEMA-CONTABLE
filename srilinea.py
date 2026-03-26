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
import time
import urllib.parse

# --- 1. CONFIGURACIÓN INICIAL ---
st.set_page_config(page_title="RAPIDITO AI - Portal Contable", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Endpoints y Configuración
URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
HEADERS_WS = {"Content-Type": "text/xml;charset=UTF-8","User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"}
URL_API_VIRAL = "https://script.google.com/macros/s/AKfycbzTqGeo2uygPVUYNfIk8MmCj9659sOON6di7ZkGDn6kQPw2z173c-EOaRUXaYV2udyB/exec" 

# --- FUNCIONES DE SOPORTE ---
def conectar_api(payload):
    try:
        r = requests.post(URL_API_VIRAL, json=payload, timeout=10)
        return r.json()
    except: return {"exito": False, "mensaje": "Error de conexión"}

def registrar_actividad(usuario, accion, cantidad=None):
    URL_LOGGING = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    payload = {"usuario": str(usuario), "accion": f"{accion} ({cantidad})" if cantidad else accion}
    try: requests.post(URL_LOGGING, json=payload, timeout=5)
    except: pass

# --- 2. GESTIÓN DE ESTADO Y LOGIN ---
if "autenticado" not in st.session_state: st.session_state.autenticado = False
if "es_premium" not in st.session_state: st.session_state.es_premium = False
if "invitaciones_disponibles" not in st.session_state: st.session_state.invitaciones_disponibles = 0
if "data_compras_cache" not in st.session_state: st.session_state.data_compras_cache = []
if "data_ventas_cache" not in st.session_state: st.session_state.data_ventas_cache = []
if "sri_results" not in st.session_state: st.session_state.sri_results = {}
if "id_proceso" not in st.session_state: st.session_state.id_proceso = 0

if not st.session_state.autenticado:
    st.sidebar.title("🔐 Acceso RAPIDITO")
    u = st.sidebar.text_input("Usuario")
    p = st.sidebar.text_input("Clave", type="password")
    if st.sidebar.button("Entrar", use_container_width=True):
        resp = conectar_api({"accion": "LOGIN", "usuario": u.strip(), "clave": p.strip()})
        if resp.get("exito"):
            st.session_state.autenticado = True
            st.session_state.usuario_actual = u.strip()
            st.session_state.invitaciones_disponibles = resp.get("invitaciones", 0)
            st.session_state.es_premium = resp.get("premium", False)
            st.rerun()
        else: st.sidebar.error("Credenciales incorrectas")
    st.stop()

# --- 3. LA PARED DE BLOQUEO (PAYWALL) ---
if not st.session_state.es_premium and st.session_state.invitaciones_disponibles > 0:
    st.title("🚧 ACCESO RESTRINGIDO")
    st.error(f"### Lo sentimos, {st.session_state.usuario_actual}")
    st.markdown(f"Para desbloquear el sistema, debes agotar tus **{st.session_state.invitaciones_disponibles} invitaciones** mensuales o adquirir la versión Premium.")
    
    col_w1, col_w2 = st.columns(2)
    
    with col_w1:
        st.subheader("🎁 Versión Gratuita")
        email_inv = st.text_input("Correo del colega:", placeholder="colega@ejemplo.com")
        if st.button("ENVIAR INVITACIÓN AHORA", type="primary", use_container_width=True):
            if email_inv:
                resp = conectar_api({"accion": "INVITAR", "usuario": st.session_state.usuario_actual, "invitado": email_inv})
                if resp.get("exito"):
                    st.success("¡Invitación registrada!")
                    msg_wa = urllib.parse.quote(f"🎁 Regalo Pase *RAPIDITO AI*.\n👤 Usuario: {email_inv}\n🔑 Clave: Rapidito2026\n👉 Entra aquí: https://pruebas1998.streamlit.app")
                    st.markdown(f'''
                        <a href="https://wa.me/?text={msg_wa}" target="_blank">
                            <button style="background-color:#25D366;color:white;width:100%;font-weight:bold;padding:15px;border-radius:10px;border:none;cursor:pointer;">
                                📲 CLIC AQUÍ PARA ENVIAR POR WHATSAPP Y ENTRAR
                            </button>
                        </a>
                    ''', unsafe_allow_html=True)
                    if st.button("YA LO ENVIÉ, ENTRAR AL SISTEMA"):
                        st.session_state.invitaciones_disponibles -= 1
                        st.rerun()
            else: st.error("Ingresa un correo.")

    with col_w2:
        st.subheader("👑 Versión Premium")
        with st.expander("💎 VER VALOR Y DATOS DE PAGO", expanded=True):
            st.markdown(f"""
            ### 💰 Costo: $25.00 / Año
            **Transferencia Bancaria (Ecuador):**
            * **Banco:** Banco Pichincha (Ahorros) 2205082283
            * **Beneficiario:** Gabriel Sebastián Jácome Carvajal
            * **Enviar el comprobante al siguiente whatsapp para activación** 0982258418
            """)
            st.link_button("📩 ENVIAR COMPROBANTE POR WHATSAPP", f"https://wa.me/593987654321?text=Hola%20Gabriel,%20pago%20realizado%20usuario%20{st.session_state.usuario_actual}")
    st.stop()

# --- 4. MOTOR DE EXTRACCIÓN ROBUSTO ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f: st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f: json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

def extraer_datos_robusto(xml_file):
    try:
        xml_file.seek(0); tree = ET.parse(xml_file); root = tree.getroot(); xml_data = None
        for elem in root.iter():
            if 'comprobante' in elem.tag.lower() and elem.text and "<" in elem.text:
                xml_data = ET.fromstring(re.sub(r'<\?xml.*?\?>', '', elem.text).strip()); break
        if xml_data is None: xml_data = root

        def buscar(tags):
            for t in tags:
                f = xml_data.find(f".//{t}")
                if f is not None and f.text: return f.text.strip()
            return ""

        tipo = "NC" if "notacredito" in xml_data.tag.lower() else "RET" if "retencion" in xml_data.tag.lower() else "LC" if "liquidacion" in xml_data.tag.lower() else "FC"
        razon_social = buscar(["razonSocial"]).upper()
        ruc_emisor = buscar(["ruc"])
        num_fact = f"{buscar(['estab']) or '000'}-{buscar(['ptoEmi']) or '000'}-{buscar(['secuencial']) or '000'}"
        fecha = buscar(["fechaEmision"])
        ruc_cli = buscar(["identificacionComprador", "identificacionSujetoRetenido"])
        nom_cli = buscar(["razonSocialComprador", "razonSocialSujetoRetenido"]).upper()

        info_json = st.session_state.memoria["empresas"].get(razon_social)
        detalle_f = info_json["DETALLE"] if info_json else ("PERSONAL" if len(ruc_cli)==10 else "OTROS")
        memo_f = info_json["MEMO"] if info_json else "PROFESIONAL"

        data = {"TIPO": tipo, "FECHA": fecha, "N. FACTURA": num_fact, "RUC": ruc_emisor, "NOMBRE": razon_social, "RUC CLIENTE": ruc_cli, "CLIENTE": nom_cli, "DETALLE": detalle_f, "MEMO": memo_f, "N AUTORIZACION": buscar(["numeroAutorizacion", "claveAcceso"])}
        
        # Fecha a Mes
        if "/" in fecha:
            ms = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
            data["MES"] = ms.get(fecha.split('/')[1], "DESCONOCIDO")

        # Lógica de Montos
        m = -1 if tipo == "NC" else 1
        b0, b12, i12, ice, prop, no_obj, exento = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        for imp in xml_data.findall(".//totalImpuesto"):
            try:
                c, cp = imp.find("codigo").text, imp.find("codigoPorcentaje").text
                b, v = float(imp.find("baseImponible").text or 0)*m, float(imp.find("valor").text or 0)*m
                if c == "2":
                    if cp == "0": b0 += b
                    elif cp in ["2","3","4","8","10"]: b12 += b; i12 += v
                    elif cp == "6": no_obj += b
                    elif cp == "7": exento += b
                elif c == "3": ice += v
            except: continue
        
        tot_val = 0.0
        for t_tag in ["importeTotal", "total"]:
            f = xml_data.find(f".//{t_tag}")
            if f is not None: tot_val = float(f.text)*m; break
        
        data.update({"BASE. 0": b0, "BASE. 12 / 15": b12, "IVA.": i12, "MONTO ICE": ice, "TOTAL": tot_val, "EXENTO DE IVA": exento, "NO OBJ IVA": no_obj})
        return data
    except: return None

# --- 5. GENERADOR EXCEL INTEGRAL (ORIGINAL) ---
def generar_excel_integral(compras=None, ventas=None, sri=None, sri_tipo=None):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        wb = writer.book
        # Formatos
        f_azul = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#002060','font_color':'white'})
        f_amar = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#FFD966'})
        f_verd = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#92D050'})
        f_num = wb.add_format({'num_format':'_-$ * #,##0.00_-','border':1})
        f_tot = wb.add_format({'bold':True,'num_format':'_-$ * #,##0.00_-','border':1,'bg_color':'#EFEFEF'})
        
        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

        if compras:
            df_c = pd.DataFrame(compras)
            ws_c = wb.add_worksheet('COMPRAS')
            cols = ["MES","FECHA","N. FACTURA","RUC","NOMBRE","DETALLE","MEMO","BASE. 0","BASE. 12 / 15","IVA.","TOTAL"]
            for i, c in enumerate(cols): ws_c.write(0, i, c, f_amar if "BASE" in c else f_azul)
            for r, row in enumerate(df_c[cols].values, 1):
                for c, v in enumerate(row): ws_c.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))
            
            # REPORTE ANUAL
            ws_ra = wb.add_worksheet('REPORTE ANUAL')
            cats = ["VIVIENDA","SALUD","EDUCACION","ALIMENTACION","VESTIMENTA","TURISMO","NO DEDUCIBLE","SERVICIOS BASICOS"]
            icos = ["🏠","❤️","🎓","🛒","🧢","✈️","🚫","💡"]
            ws_ra.write(0, 0, "MES", f_azul)
            ws_ra.write(0, 1, "PROFESIONAL", f_azul)
            for i, (ct, ic) in enumerate(zip(cats, icos)): ws_ra.write(0, i+2, f"{ic} {ct}", f_azul)
            
            for r, m in enumerate(meses):
                ws_ra.write(r+1, 0, m)
                ws_ra.write_formula(r+1, 1, f'=SUMIFS(COMPRAS!K:K, COMPRAS!A:A, "{m}", COMPRAS!G:G, "PROFESIONAL")', f_num)
                for c, ct in enumerate(cats):
                    ws_ra.write_formula(r+1, c+2, f'=SUMIFS(COMPRAS!K:K, COMPRAS!A:A, "{m}", COMPRAS!F:F, "{ct}")', f_num)

        if ventas:
            df_v = pd.DataFrame(ventas)
            ws_v = wb.add_worksheet('VENTAS')
            for i, c in enumerate(df_v.columns): ws_v.write(0, i, c, f_verd)
            for r, row in enumerate(df_v.values, 1):
                for c, v in enumerate(row): ws_v.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))

    return output.getvalue()

# --- 6. INTERFAZ Y PESTAÑAS ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.usuario_actual}")

with st.sidebar:
    st.header("⚙️ Menú")
    if st.button("🧹 NUEVO INFORME", use_container_width=True):
        st.session_state.id_proceso += 1
        st.session_state.data_compras_cache = []
        st.rerun()
    if st.button("🚪 CERRAR SESIÓN", use_container_width=True):
        st.session_state.autenticado = False; st.rerun()

t_xml, t_sri, t_tut = st.tabs(["📂 XMLs Manual/ZIP", "📡 Descarga SRI", "📺 Tutorial"])

with t_xml:
    c1, c2 = st.columns(2)
    with c1:
        up_c = st.file_uploader("Compras/NC", type=["xml","zip"], key=f"c_{st.session_state.id_proceso}")
        if up_c and st.button("Procesar Compras"):
            raw = []
            if up_c.name.endswith(".xml"): raw.append(extraer_datos_robusto(up_c))
            else:
                with zipfile.ZipFile(up_c) as z:
                    for n in z.namelist():
                        if n.endswith(".xml"): raw.append(extraer_datos_robusto(io.BytesIO(z.read(n))))
            st.session_state.data_compras_cache = [r for r in raw if r]
            st.success(f"{len(st.session_state.data_compras_cache)} procesados.")

    with c2:
        if st.session_state.data_compras_cache:
            st.download_button("📥 DESCARGAR EXCEL COMPRAS", generar_excel_integral(compras=st.session_state.data_compras_cache), "Reporte_Compras.xlsx")

with t_sri:
    st.subheader("📡 Extractor Automático por TXT")
    txt = st.file_uploader("Sube tu TXT del SRI", type=["txt"])
    if txt and st.button("Iniciar Descarga"):
        claves = list(set(re.findall(r'\d{49}', txt.read().decode("latin-1"))))
        bar = st.progress(0)
        res = []
        for i, cl in enumerate(claves):
            try:
                r = requests.post(URL_WS, data=f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{cl}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>', headers=HEADERS_WS, verify=False, timeout=5)
                if "<autorizaciones>" in r.text:
                    d = extraer_datos_robusto(io.BytesIO(r.content))
                    if d: res.append(d)
            except: pass
            bar.progress((i+1)/len(claves))
        st.session_state.sri_results["data"] = res
        st.success("Descarga finalizada.")
    
    if "data" in st.session_state.sri_results:
        st.download_button("📥 DESCARGAR RESULTADOS SRI", generar_excel_integral(compras=st.session_state.sri_results["data"]), "Descarga_SRI.xlsx")

with t_tut:
    st.video("https://youtu.be/0iUAI3NAkww?si=aR-Xf9F-GeD1Kj1S")
