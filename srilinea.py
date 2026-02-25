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

# --- 1. CONFIGURACIÓN Y SEGURIDAD ---
st.set_page_config(page_title="RAPIDITO AI - Portal Contable", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
HEADERS_WS = {"Content-Type": "text/xml;charset=UTF-8","User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"}
URL_API_VIRAL = "https://script.google.com/macros/s/AKfycbz3vRq203m7vcdor30hJiXuAGNGr8n_kM2dCpf63LW4KhaeY9wqAijBC473AwywYes/exec" 

# --- CONEXIÓN API ---
def conectar_api(payload):
    try:
        if "TU_URL" in URL_API_VIRAL: return {"exito": False, "mensaje": "Configurar URL_API_VIRAL"}
        r = requests.post(URL_API_VIRAL, json=payload, timeout=10)
        return r.json()
    except: return {"exito": False, "mensaje": "Error de conexión"}

def registrar_actividad(usuario, accion, cantidad=None, sugerencia=None):
    URL_LOGGING = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    detalle = f"{accion} ({cantidad} XMLs)" if cantidad is not None else accion
    payload = {"usuario": str(usuario), "accion": str(detalle)}
    if sugerencia: payload["sugerencia"] = str(sugerencia)
    try: requests.post(URL_LOGGING, json=payload, timeout=5); return True
    except: return False

# --- 2. SISTEMA DE ESTADO ---
if "autenticado" not in st.session_state: st.session_state.autenticado = False
if "id_proceso" not in st.session_state: st.session_state.id_proceso = 0
if "data_compras_cache" not in st.session_state: st.session_state.data_compras_cache = []
if "data_ventas_cache" not in st.session_state: st.session_state.data_ventas_cache = []
if "invitaciones_disponibles" not in st.session_state: st.session_state.invitaciones_disponibles = 0
if "sri_results" not in st.session_state: st.session_state.sri_results = {}
if "mostrar_integral" not in st.session_state: st.session_state.mostrar_integral = False

if not st.session_state.autenticado:
    st.sidebar.title("🔐 Acceso RAPIDITO")
    u, p = st.sidebar.text_input("Usuario"), st.sidebar.text_input("Clave", type="password")
    if st.sidebar.button("Entrar"):
        resp = conectar_api({"accion": "LOGIN", "usuario": u.strip(), "clave": p.strip()})
        if resp.get("exito"):
            st.session_state.autenticado, st.session_state.usuario_actual = True, u.strip()
            st.session_state.invitaciones_disponibles = resp.get("invitaciones", 0)
            registrar_actividad(u, "LOGIN"); st.rerun()
        else: st.sidebar.error("Credenciales incorrectas")
    st.stop()

# --- 3. MEMORIA JSON ---
if 'memoria' not in st.session_state:
    if os.path.exists("conocimiento_contable.json"):
        with open("conocimiento_contable.json", "r", encoding="utf-8") as f: st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f: json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

def procesar_archivos_entrada(lista):
    xmls = []
    for f in lista:
        if f.name.lower().endswith('.xml'): xmls.append(io.BytesIO(f.getvalue()))
        elif f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.lower().endswith('.xml') and not n.startswith('__MACOSX'): xmls.append(io.BytesIO(z.read(n)))
    return xmls

# --- 4. MOTOR DE EXTRACCIÓN (DIFERENCIACIÓN 10/13) ---
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

        len_id = len(ruc_cli)
        info_json = st.session_state.memoria["empresas"].get(razon_social)
        
        # Lógica de clasificación solicitada
        if len_id == 10:
            memo_final = "PERSONAL"
            detalle_final = info_json["DETALLE"] if info_json else "NO DEDUCIBLE"
        else:
            detalle_final = info_json["DETALLE"] if info_json else "OTROS"
            memo_final = info_json["MEMO"] if info_json else "PROFESIONAL"

        data = {
            "TIPO": tipo, "TIPO DE DOCUMENTO": tipo, "FECHA": fecha, "N. FACTURA": num_fact,
            "RUC": ruc_emisor, "CONTRIBUYENTE": ruc_cli, "NOMBRE": razon_social,
            "RUC CLIENTE": ruc_cli, "CLIENTE": nom_cli, "DETALLE": detalle_final, "MEMO": memo_final,
            "N AUTORIZACION": buscar(["numeroAutorizacion", "claveAcceso"])
        }
        
        if "/" in fecha:
            ms = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
            data["MES"] = ms.get(fecha.split('/')[1], "DESCONOCIDO")

        if tipo == "RET":
            r_renta, r_iva, b_renta, b_iva = 0.0, 0.0, 0.0, 0.0
            node = xml_data.find(".//numDocSustento")
            sus = node.text.replace('-','') if (node is not None and node.text) else ""
            if len(sus) >= 15: sus = f"{sus[0:3]}-{sus[3:6]}-{sus[6:]}"
            for item in (xml_data.findall(".//impuesto") + xml_data.findall(".//retencion")):
                try:
                    c, v, b = item.find("codigo").text, float(item.find("valorRetenido").text or 0), float(item.find("baseImponible").text or 0)
                    if c == "1": r_renta += v; b_renta += b
                    elif c == "2": r_iva += v; b_iva += b
                except: continue
            data.update({"numfact": sus, "numreten": num_fact, "baserenta": b_renta, "rt_renta": r_renta, "baseiva": b_iva, "rt_iva": r_iva, "TOTAL RET": r_renta+r_iva, "SUSTENTO": sus, "fechaemi": fecha})
        else:
            m = -1 if tipo == "NC" else 1
            b0, b12, i12, ice, prop, no_obj, exento, otra_b, otro_i = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            for imp in xml_data.findall(".//totalImpuesto"):
                try:
                    c, cp = imp.find("codigo").text, imp.find("codigoPorcentaje").text
                    b, v = float(imp.find("baseImponible").text or 0)*m, float(imp.find("valor").text or 0)*m
                    if c == "2":
                        if cp == "0": b0 += b
                        elif cp in ["2","3","4","8","10"]: b12 += b; i12 += v
                        elif cp == "6": no_obj += b
                        elif cp == "7": exento += b
                        else: otra_b += b; otro_i += v
                    elif c == "3": ice += v
                except: continue
            
            total_val = 0.0
            for t_tag in ["importeTotal", "total", "valorModificado"]:
                f = xml_data.find(f".//{t_tag}")
                if f is not None: total_val = float(f.text) * m; break
            
            p_node = xml_data.find(".//propina")
            prop = float(p_node.text or 0) * m if p_node is not None else 0.0

            items = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
            data.update({"OTRA BASE IVA": otra_b, "OTRO IVA": otro_i, "MONTO ICE": ice, "PROPINAS": prop, "EXENTO DE IVA": exento, "NO OBJ IVA": no_obj, "BASE. 0": b0, "BASE. 12 / 15": b12, "IVA.": i12, "TOTAL": total_val, "SUBDETALLE": " | ".join(items[:5])})
        return data
    except: return None

# --- 5. LÓGICA DE INTEGRACIÓN VENTAS ---
def procesar_ventas_con_retenciones(lista):
    vts, rets = [], {}
    for d in lista:
        if d["TIPO"] == "FC": vts.append(d)
        elif d["TIPO"] == "RET" and d.get("SUSTENTO"): rets[d["SUSTENTO"]] = d
    res = []
    for v in vts:
        r = rets.get(v["N. FACTURA"], {})
        res.append({
            "MES": v.get("MES"), "FECHA": v["FECHA"], "N. FACTURA": v["N. FACTURA"],
            "RUC": v["RUC CLIENTE"], "CLIENTE": v["CLIENTE"], "DETALLE": "SERVICIOS", "MEMO": "PROFESIONAL", "MONTO REEMBOLS": 0.0,
            "BASE. 0": v.get("BASE. 0", 0), "BASE. 12 / 15": v.get("BASE. 12 / 15", 0), "IVA": v.get("IVA.", 0), "TOTAL": v.get("TOTAL", 0),
            "FECHA RET": r.get("fechaemi", ""), "N° RET": r.get("numreten", ""), "N° AUTORIZACIÓN": r.get("N AUTORIZACION", ""),
            "RET RENTA": r.get("rt_renta", 0), "RET IVA": r.get("rt_iva", 0), "ISD": 0.0, "TOTAL RET": r.get("TOTAL RET", 0)
        })
    return res

# --- 6. GENERADOR EXCEL INTEGRAL (CÁLCULOS EXACTOS) ---
def generar_excel_multiexcel(data_compras=None, data_ventas_ret=None, data_sri_lista=None, sri_mode=None):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        wb = writer.book
        f_azul = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#002060','font_color':'white'})
        f_amar = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#FFD966'})
        f_verd = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#92D050'})
        f_gris = wb.add_format({'bold':True,'align':'center','border':1,'bg_color':'#F2F2F2'})
        f_num = wb.add_format({'num_format':'_-$ * #,##0.00_-','border':1})
        f_tot = wb.add_format({'bold':True,'num_format':'_-$ * #,##0.00_-','border':1,'bg_color':'#EFEFEF'})
        
        texto_pie = "&LGenerado por RAPIDITO AI&Rrapidito.ec"
        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

        # === MODO SRI ===
        if sri_mode:
            df = pd.DataFrame(data_sri_lista)
            if sri_mode == "NC":
                cols = ["NOMBRE","RUC","N AUTORIZACION","FECHA","TIPO DE DOCUMENTO","N. FACTURA","MES","RUC CLIENTE","CLIENTE","PROPINAS","BASE. 0","NO OBJ IVA","BASE. 12 / 15","IVA.","TOTAL"]
                fmt_h = f_amar; sh_nm = "NOTAS DE CREDITO"
            elif sri_mode == "RET":
                cols = ["ruc_recep", "nomrecep", "fechaemi", "razonsocial", "ruc_emisor", "numfact", "numreten", "baserenta", "rt_renta", "baseiva", "rt_iva", "numautori"]
                fmt_h = f_verd; sh_nm = "RETENCIONES"
            else:
                cols = ["MES","FECHA","N. FACTURA","TIPO DE DOCUMENTO","RUC","CONTRIBUYENTE","NOMBRE","DETALLE","MEMO","OTRA BASE IVA","OTRO IVA","MONTO ICE","PROPINAS","EXENTO DE IVA","NO OBJ IVA","BASE. 0","BASE. 12 / 15","IVA.","TOTAL","SUBDETALLE"]
                fmt_h = f_azul; sh_nm = "FACTURAS"
            
            for c in cols: 
                if c not in df.columns: df[c] = ""
            ws = wb.add_worksheet(sh_nm)
            ws.set_footer(texto_pie)
            for i, c in enumerate(cols): ws.write(0, i, c, fmt_h)
            for r, row in enumerate(df[cols].values, 1):
                for c, v in enumerate(row): ws.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))
            ws.set_column(0, len(cols)-1, 15)

        # === MODO MANUAL (CON CÁLCULOS



