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

# --- 4. MOTOR DE EXTRACCIÓN ---
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

# --- 6. GENERADOR EXCEL INTEGRAL ---
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

        else:
            if data_compras:
                df_c = pd.DataFrame(data_compras)
                orden_c = ["MES","FECHA","N. FACTURA","TIPO DE DOCUMENTO","RUC","CONTRIBUYENTE","NOMBRE","DETALLE","MEMO","OTRA BASE IVA","OTRO IVA","MONTO ICE","PROPINAS","EXENTO DE IVA","NO OBJ IVA","BASE. 0","BASE. 12 / 15","IVA.","TOTAL","SUBDETALLE"]
                for c in orden_c: 
                    if c not in df_c.columns: df_c[c] = ""
                ws_c = wb.add_worksheet('COMPRAS')
                ws_c.set_footer(texto_pie)
                for i, c in enumerate(orden_c): ws_c.write(0, i, c, f_amar if i in range(9, 15) else f_azul)
                for r, row in enumerate(df_c[orden_c].values, 1):
                    for c, v in enumerate(row): ws_c.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))
                
                ft = len(df_c) + 1; ws_c.write(ft, 0, "TOTAL", f_tot)
                for ci in range(9, 19):
                    l = xlsxwriter.utility.xl_col_to_name(ci)
                    ws_c.write_formula(ft, ci, f"=SUM({l}2:{l}{ft})", f_tot)

                ws_ra = wb.add_worksheet('REPORTE ANUAL')
                ws_ra.set_footer(texto_pie)
                ws_ra.set_column('A:K', 14)
                ws_ra.merge_range('B1:B2', "Negocios y\nServicios", f_azul)
                cats=["VIVIENDA","SALUD","EDUCACION","ALIMENTACION","VESTIMENTA","TURISMO","NO DEDUCIBLE","SERVICIOS BASICOS"]
                icos=["🏠","❤️","🎓","🛒","🧢","✈️","🚫","💡"]
                for i,(ct,ic) in enumerate(zip(cats,icos)):
                    ws_ra.write(0,i+2,ic,f_azul); ws_ra.write(1,i+2,ct.title(),f_azul)
                ws_ra.merge_range('K1:K2',"Total Mes",f_azul); ws_ra.write('B3',"PROFESIONALES",f_gris); ws_ra.merge_range('C3:J3',"GASTOS PERSONALES",f_gris)
                
                cl_sum = ["P","Q","O","N","J","M"]
                for r, mes in enumerate(meses):
                    f_idx = r+4
                    ws_ra.write(r+3, 0, mes.title(), f_num)
                    f_pr = "+".join([f"SUMIFS('COMPRAS'!${l}:${l},'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$I:$I,\"PROFESIONAL\")" for l in cl_sum])
                    ws_ra.write_formula(r+3, 1, "="+f_pr, f_num)
                    for cidx, ct in enumerate(cats):
                        f_ct = "+".join([f"SUMIFS('COMPRAS'!${l}:${l},'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"{ct}\")" for l in cl_sum])
                        ws_ra.write_formula(r+3, cidx+2, "="+f_ct, f_num)
                    ws_ra.write_formula(r+3, 10, f"=SUM(B{f_idx}:J{f_idx})", f_num)
                ws_ra.write(15, 0, "TOTAL", f_tot)
                for c in range(1,11):
                    l = xlsxwriter.utility.xl_col_to_name(c)
                    ws_ra.write_formula(15, c, f"=SUM({l}4:{l}15)", f_tot)

            if data_ventas_ret:
                df_v = pd.DataFrame(data_ventas_ret)
                ord_v = ["MES","FECHA","N. FACTURA","RUC","CLIENTE","DETALLE","MEMO","MONTO REEMBOLS","BASE. 0","BASE. 12 / 15","IVA","TOTAL","FECHA RET","N° RET","N° AUTORIZACIÓN","RET RENTA","RET IVA","ISD","TOTAL RET"]
                for c in ord_v: 
                    if c not in df_v.columns: df_v[c] = ""
                ws_v = wb.add_worksheet('VENTAS')
                for i, c in enumerate(ord_v): ws_v.write(0, i, c, f_verd if i >= 12 else f_azul)
                for r, row in enumerate(df_v[ord_v].values, 1):
                    for c, v in enumerate(row): ws_v.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))
                
                ws_p = wb.add_worksheet('PROYECCION')
                ws_p.set_column('A:M', 15)
                for i, h in enumerate(["VENTAS", "COMPRAS", "TOTAL"]): ws_p.write(i+2, 0, h, f_azul)
                for c, mes in enumerate(meses):
                    col = c + 1; l = xlsxwriter.utility.xl_col_to_name(col)
                    ws_p.write(1, col, mes, f_azul)
                    ws_p.write_formula(2, col, f"=SUMIFS(VENTAS!$I:$I,VENTAS!$A:$A,\"{mes}\") + SUMIFS(VENTAS!$J:$J,VENTAS!$A:$A,\"{mes}\")", f_num)
                    if data_compras:
                        f_cp = "+".join([f"SUMIFS('COMPRAS'!${x}:${x},'COMPRAS'!$A:$A,{l}$2,'COMPRAS'!$I:$I,\"PROFESIONAL\")" for x in cl_sum])
                        ws_p.write_formula(3, col, "="+f_cp, f_num)
                    ws_p.write_formula(4, col, f"={l}3-{l}4", f_tot)

    return output.getvalue()

# --- 7. INTERFAZ ORGANIZADA ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.get('usuario_actual', 'Portal Contable')}")

with st.sidebar:
    st.header("⚙️ Panel de Control")
    if st.button("🧹 NUEVO INFORME", type="primary", use_container_width=True):
        st.session_state.id_proceso += 1
        st.session_state.data_compras_cache, st.session_state.data_ventas_cache, st.session_state.sri_results = [], [], {}
        st.session_state.mostrar_integral = False
        st.rerun()
    st.markdown("---")
    if st.session_state.usuario_actual == "GABRIEL":
        st.subheader("🔑 Master Config")
        up_xls = st.file_uploader("Actualizar JSON", type=["xlsx"], key=f"mst_{st.session_state.id_proceso}")
        if up_xls:
            df = pd.read_excel(up_xls); df.columns = [c.upper().strip() for c in df.columns]
            for _, r in df.iterrows():
                nm = str(r.get("NOMBRE","")).upper().strip()
                if nm: st.session_state.memoria["empresas"][nm] = {"DETALLE":str(r.get("DETALLE","OTROS")).upper(),"MEMO":str(r.get("MEMO","PROFESIONAL")).upper()}
            guardar_memoria(); st.success("Guardado.")
    st.subheader("📬 Sugerencias")
    sug = st.text_area("Ideas:")
    if st.button("Enviar Sugerencia", use_container_width=True):
        if sug: registrar_actividad(st.session_state.usuario_actual, "SUGERENCIA", sugerencia=sug); st.success("Recibido.")
    st.markdown("---")
    if st.button("🚪 Cerrar Sesión", use_container_width=True):
        st.session_state.autenticado = False; st.rerun()

st.subheader("💎 Mantén tu membresia gratuita invitando a 3 contadores colegas al mes")
inv = st.session_state.invitaciones_disponibles
if inv > 0:
    with st.expander(f"🎁 Regalar Invitación ({inv} disponibles)"):
        email = st.text_input("Email colega")
        if st.button("Generar"):
            resp = conectar_api({"accion": "INVITAR", "usuario": st.session_state.usuario_actual, "invitado": email})
            if resp.get("exito"):
                msg = urllib.parse.quote(f"🎁 Regalo Pase *RAPIDITO AI*.\n👤 Usuario: {email}\n🔑 Clave: Rapidito2026\n👉 https://pruebas1998.streamlit.app")
                st.markdown(f'<a href="https://wa.me/?text={msg}" target="_blank"><button style="background-color:#25D366;color:white;width:100%;font-weight:bold;padding:10px;border-radius:8px;border:none;">📲 WhatsApp</button></a>', unsafe_allow_html=True)

# PESTAÑAS PRINCIPALES
tab_xml, tab_sri, tab_tutorial = st.tabs(["📂 Subir XMLs (Manual/ZIP)", "📡 Descarga SRI (TXT)", "📺 Aprende a usarme"])

with tab_xml:
    # --- SOLUCIÓN DEL CRASH FRONTEND: Reemplazar pestañas anidadas por botones horizontales ---
    sub_xml = st.radio("Selecciona el módulo:", ["🛒 Compras y NC", "💰 Ventas y Retenciones", "📑 Informe Integral"], horizontal=True, label_visibility="collapsed")
    st.markdown("---")
    
    meses_orden = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    
    if sub_xml == "🛒 Compras y NC":
        up = st.file_uploader("Compras (XML/ZIP)", type=["xml","zip"], accept_multiple_files=True, key=f"c_{st.session_state.id_proceso}")
        st.info("💡 **Módulo de Compras:** Sube tus facturas recibidas y notas de crédito en formato xml o zip con xmls. Este reporte contiene la pestaña de compras y gasto anual. El sistema clasificará automáticamente tus gastos deducibles.")
        
        if up and st.button("Procesar Compras"):
            data = [extraer_datos_robusto(x) for x in procesar_archivos_entrada(up)]
            data = [d for d in data if d and d["TIPO"] in ["FC","NC"]]
            st.session_state.data_compras_cache = data
            registrar_actividad(st.session_state.usuario_actual, "PROCESÓ COMPRAS MANUAL", len(data))
        
        if st.session_state.data_compras_cache:
            st.download_button("📥 Excel Compras", generar_excel_multiexcel(data_compras=st.session_state.data_compras_cache), "Compras.xlsx", key="dl_c")
            st.divider()
            st.subheader("📊 Visualización de Compras")
            df_c = pd.DataFrame(st.session_state.data_compras_cache)
            
            meses_disp_c = [m for m in meses_orden if m in df_c["MES"].values]
            if "DESCONOCIDO" in df_c["MES"].values: meses_disp_c.append("DESCONOCIDO")
            filtro_c = st.selectbox("📅 Filtrar por Mes:", ["ACUMULADO"] + meses_disp_c, key="filt_c")
            df_c_filt = df_c if filtro_c == "ACUMULADO" else df_c[df_c["MES"] == filtro_c]
            
            c1, c2 = st.columns(2)
            with c1:
                st.caption(f"Gastos por Categoría ({filtro_c})")
                gastos_cat = df_c_filt.groupby("DETALLE")["TOTAL"].sum().reset_index()
                st.bar_chart(gastos_cat.set_index("DETALLE"))
            with c2:
                st.caption(f"Evolución de Gastos por Mes ({filtro_c})")
                gastos_mes = df_c_filt.groupby("MES")["TOTAL"].sum().reindex(meses_disp_c).fillna(0)
                st.bar_chart(gastos_mes)

    elif sub_xml == "💰 Ventas y Retenciones":
        up = st.file_uploader("Ventas (XML/ZIP)", type=["xml","zip"], accept_multiple_files=True, key=f"v_{st.session_state.id_proceso}")
        st.info("💡 **Módulo de Ventas:** Carga tus facturas emitidas y retenciones recibidas en formato xml o zip con xmls. El sistema cruzará la información usando los números de sustento.")
        
        if up and st.button("Procesar Ventas"):
            raw = [extraer_datos_robusto(x) for x in procesar_archivos_entrada(up)]
            data = procesar_ventas_con_retenciones([d for d in raw if d])
            st.session_state.data_ventas_cache = data
            registrar_actividad(st.session_state.usuario_actual, "PROCESÓ VENTAS MANUAL", len(data))

        if st.session_state.data_ventas_cache:
            st.download_button("📥 Excel Ventas", generar_excel_multiexcel(data_ventas_ret=st.session_state.data_ventas_cache), "Ventas.xlsx", key="dl_v")
            st.divider()
            st.subheader("📈 Visualización de Ventas")
            df_v = pd.DataFrame(st.session_state.data_ventas_cache)
            
            meses_disp_v = [m for m in meses_orden if m in df_v["MES"].values]
            if "DESCONOCIDO" in df_v["MES"].values: meses_disp_v.append("DESCONOCIDO")
            filtro_v = st.selectbox("📅 Filtrar por Mes:", ["ACUMULADO"] + meses_disp_v, key="filt_v")
            df_v_filt = df_v if filtro_v == "ACUMULADO" else df_v[df_v["MES"] == filtro_v]
            
            c1, c2 = st.columns(2)
            with c1:
                st.caption(f"Tendencia de Ingresos por Mes ({filtro_v})")
                ventas_mes = df_v_filt.groupby("MES")["TOTAL"].sum().reindex(meses_disp_v).fillna(0)
                st.line_chart(ventas_mes)
            with c2:
                st.caption(f"Top Mejores Clientes ({filtro_v})")
                top_cli = df_v_filt.groupby("CLIENTE")["TOTAL"].sum().nlargest(5).reset_index()
                st.bar_chart(top_cli.set_index("CLIENTE"))

    elif sub_xml == "📑 Informe Integral":
        st.info("💡 **Informe Integral:** Consumo de datos cruzados. Asegúrate de haber presionado primero el botón de procesar compras y ventas.")
        if st.button("🚀 Generar Informe Integral"):
            if st.session_state.data_compras_cache and st.session_state.data_ventas_cache:
                registrar_actividad(st.session_state.usuario_actual, "GENERÓ INFORME INTEGRAL")
                st.session_state.mostrar_integral = True
            else: 
                st.error("Falta procesar Compras y Ventas.")
                st.session_state.mostrar_integral = False
                
        if st.session_state.get("mostrar_integral") and st.session_state.data_compras_cache and st.session_state.data_ventas_cache:
            st.download_button("📥 DESCARGAR INTEGRAL", generar_excel_multiexcel(st.session_state.data_compras_cache, st.session_state.data_ventas_cache), "Integral.xlsx", key="dl_int")
            st.divider()
            st.subheader("⚖️ Dashboard Financiero Anual")
            df_c = pd.DataFrame(st.session_state.data_compras_cache)
            df_v = pd.DataFrame(st.session_state.data_ventas_cache)
            
            meses_disp_i = [m for m in meses_orden if m in df_c["MES"].values or m in df_v["MES"].values]
            filtro_i = st.selectbox("📅 Filtrar por Mes:", ["ACUMULADO"] + meses_disp_i, key="filt_i")
            df_c_filt = df_c if filtro_i == "ACUMULADO" else df_c[df_c["MES"] == filtro_i]
            df_v_filt = df_v if filtro_i == "ACUMULADO" else df_v[df_v["MES"] == filtro_i]
            
            tot_compras = df_c_filt["TOTAL"].sum()
            tot_ventas = df_v_filt["TOTAL"].sum()
            utilidad = tot_ventas - tot_compras
            
            k1, k2, k3 = st.columns(3)
            k1.metric("💰 Ingresos (Ventas)", f"${tot_ventas:,.2f}")
            k2.metric("🛒 Gastos (Compras)", f"${tot_compras:,.2f}")
            k3.metric("📊 Saldo Neto", f"${utilidad:,.2f}", delta=f"${utilidad:,.2f}", delta_color="normal" if utilidad >= 0 else "inverse")

            st.caption(f"Comparativa: Ventas vs Compras ({filtro_i})")
            v_mes = df_v_filt.groupby("MES")["TOTAL"].sum().reindex(meses_disp_i).fillna(0).rename("Ventas")
            c_mes = df_c_filt.groupby("MES")["TOTAL"].sum().reindex(meses_disp_i).fillna(0).rename("Compras")
            st.bar_chart(pd.concat([v_mes, c_mes], axis=1))

with tab_sri:
    # --- SOLUCIÓN DEL CRASH FRONTEND PARA EL SRI ---
    sub_sri = st.radio("Selecciona descarga:", ["Facturas", "Notas Crédito", "Retenciones"], horizontal=True, label_visibility="collapsed")
    st.markdown("---")

    def bloque_sri_persistente(titulo, tipo_filtro, key):
        st.subheader(titulo); up = st.file_uploader(f"TXT {titulo}", type=["txt"], key=f"up_{key}")
        st.info(f"👉 Sube el archivo TXT descargado del portal del SRI para extraer masivamente los XMLs de {titulo}.")
        if up and st.button(f"🚀 Descargar {titulo}", key=f"btn_{key}"):
            claves = list(dict.fromkeys(re.findall(r'\d{49}', up.read().decode("latin-1"))))
            if claves:
                bar, status = st.progress(0), st.empty(); lst, zip_buf = [], io.BytesIO()
                with zipfile.ZipFile(zip_buf, "a", zipfile.ZIP_DEFLATED) as zf:
                    for i, cl in enumerate(claves):
                        try:
                            r = requests.post(URL_WS, data=f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{cl}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>', headers=HEADERS_WS, verify=False, timeout=5)
                            if "<autorizaciones>" in r.text:
                                zf.writestr(f"{cl}.xml", r.text); d = extraer_datos_robusto(io.BytesIO(r.content))
                                if d and (d["TIPO"] == tipo_filtro or (tipo_filtro=="FC" and d["TIPO"]=="LC")): lst.append(d)
                        except: pass
                        bar.progress((i+1)/len(claves)); status.text(f"Procesando {i+1}/{len(claves)}")
                st.session_state.sri_results[key] = {"data": lst, "zip": zip_buf.getvalue()}
                registrar_actividad(st.session_state.usuario_actual, f"DESCARGA SRI: {titulo}", len(lst))
        if key in st.session_state.sri_results:
            res = st.session_state.sri_results[key]
            if res["data"]:
                c1, c2 = st.columns(2)
                with c1: st.download_button(f"📊 Excel {titulo}", generar_excel_multiexcel(data_sri_lista=res["data"], sri_mode=tipo_filtro), f"{titulo}.xlsx", key=f"dl_ex_{key}")
                with c2: st.download_button(f"📦 ZIP XMLs {titulo}", res["zip"], f"{titulo}.zip", key=f"dl_zip_{key}")

    if sub_sri == "Facturas":
        bloque_sri_persistente("Facturas Recibidas", "FC", "sri_fc")
    elif sub_sri == "Notas Crédito":
        bloque_sri_persistente("Notas de Crédito", "NC", "sri_nc")
    elif sub_sri == "Retenciones":
        bloque_sri_persistente("Retenciones", "RET", "sri_ret")

with tab_tutorial:
    st.subheader("🎥 Tutorial: Aprende a usar RAPIDITO AI")
    st.video("https://www.youtube.com/watch?v=0iUAI3NAkww")


