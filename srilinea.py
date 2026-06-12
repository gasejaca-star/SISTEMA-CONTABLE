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
URL_API_VIRAL = "https://script.google.com/macros/s/AKfycby34vXKtymcy2zt3I8DXHVTDLXL-ZPNdfSEn9E1qRNKbz3dRzB9c7xN5uX_T0Fd5Q8/exec" 

# --- FUNCIONES DE SOPORTE ---
def obtener_ip_usuario():
    try:
        # Intentamos obtener la IP a través de las cabeceras de Streamlit
        headers = st.context.headers
        
        # Primero probamos X-Forwarded-For (común en despliegues en la nube)
        if "X-Forwarded-For" in headers:
            return headers["X-Forwarded-For"].split(",")[0].strip()
        
        # Alternativa: ver si la IP viene en la cabecera 'Remote-Addr'
        if "Remote-Addr" in headers:
            return headers["Remote-Addr"]
            
        return "IP_Privada_o_Local"
    except Exception as e:
        return f"Error: {str(e)}"
def to_date(fecha_str):
    if not fecha_str: return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try: return datetime.strptime(fecha_str.strip(), fmt)
        except: pass
    return None

def norm_fecha(fecha_str):
    return fecha_str.strip() if fecha_str else ""
    
def norm_periodo(periodo_str):
    if not periodo_str: return ""
    # Si viene MM/YYYY, lo mantiene limpio
    return periodo_str.strip()
def conectar_api(payload):
    try:
        r = requests.post(URL_API_VIRAL, json=payload, timeout=10)
        return r.json()
    except: return {"exito": False, "mensaje": "Error de conexión"}

def registrar_actividad(usuario, accion, cantidad=None, sugerencia=None):
    URL_LOGGING = "https://script.google.com/macros/s/AKfycbwur1tNR80874Djv78LG1ed9ZwUOdJbaWC9Ctc39l3510zVSPs_ycntW4-lwo2UOLbm/exec"
    detalle = f"{accion} ({cantidad} XMLs)" if cantidad is not None else accion
    payload = {"usuario": str(usuario), "accion": str(detalle)}
    if sugerencia: payload["sugerencia"] = str(sugerencia)
    try: requests.post(URL_LOGGING, json=payload, timeout=5); return True
    except: return False

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
            
            # ---> AQUÍ AGREGAMOS EL REGISTRO DE LA ACTIVIDAD DEL LOGIN <---
            registrar_actividad(st.session_state.usuario_actual, "LOGIN EXITOSO")
            
            st.rerun()
        else: st.sidebar.error("Credenciales incorrectas")
    st.stop()

# --- 3. LA PARED DE BLOQUEO (PAYWALL) ---
if not st.session_state.es_premium and st.session_state.invitaciones_disponibles > 0:
    st.title("🚧 ACCESO RESTRINGIDO")
    st.error(f"### Lo sentimos, {st.session_state.usuario_actual}")
    st.markdown(f"Para desbloquear el sistema, debes agotar tus **{st.session_state.invitaciones_disponibles} invitaciones** mensuales o adquirir la versión Premium. Invita a tus colegas o contadores conocidos. Después de agotar tus invitaciones oprime F5 y vuelve a ingresar")
    
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
            else: st.error("Ingresa un correo.")

    with col_w2:
        st.subheader("👑 Versión Premium")
        with st.expander("💎 VER VALOR Y DATOS DE PAGO", expanded=True):
            st.markdown(f"""
            ### 💰 Costo: \$2.99 / MES        -----         \$20 / ANUAL
            **Transferencia Bancaria (Ecuador):**
            * **Banco:** Banco Pichincha (Ahorros) 2205082283
            * **Beneficiario:** Gabriel  Jácome 
            * **Enviar el comprobante al siguiente whatsapp para la activación** 0982258418
            """)
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
            ret_cols = {
                "ivabase10": 0.0, "ivaret10": 0.0,
                "ivabase20": 0.0, "ivaret20": 0.0,
                "ivabase30": 0.0, "ivaret30": 0.0,
                "ivabase70": 0.0, "ivaret70": 0.0,
                "ivabase100": 0.0, "ivaret100": 0.0,
                "ftecodret": [], "ftebase": 0.0, "fteporcen": [], "ftevalret": 0.0
            }
            cod_doc_sustento = ""
            num_doc_sustento = ""
            fec_doc_sustento = ""
            f_norm = fecha  # Asignamos la fecha encontrada
            fec_autori = buscar(["fechaAutorizacion"]) or fecha

            for item in (xml_data.findall(".//impuesto") + xml_data.findall(".//retencion")):
                try:
                    codigo = item.find("codigo").text.strip() if item.find("codigo") is not None else ""
                    base = float(item.find("baseImponible").text or 0)
                    p_ret = float(item.find("porcentajeRetener").text or 0)
                    val_ret = float(item.find("valorRetenido").text or 0)
                    
                    if item.find("codDocSustento") is not None:
                        cod_doc_sustento = item.find("codDocSustento").text.strip()
                    if item.find("numDocSustento") is not None:
                        num_doc_sustento = item.find("numDocSustento").text.strip().replace("-", "")
                    if item.find("fechaEmisionDocSustento") is not None:
                        fec_doc_sustento = norm_fecha(item.find("fechaEmisionDocSustento").text)

                    if codigo == "2": # Mapeo IVA exacto por tramos
                        p_rounded = round(p_ret)
                        if p_rounded == 10: ret_cols["ivabase10"] += base; ret_cols["ivaret10"] += val_ret
                        elif p_rounded == 20: ret_cols["ivabase20"] += base; ret_cols["ivaret20"] += val_ret
                        elif p_rounded == 30: ret_cols["ivabase30"] += base; ret_cols["ivaret30"] += val_ret
                        elif p_rounded == 70: ret_cols["ivabase70"] += base; ret_cols["ivaret70"] += val_ret
                        elif p_rounded == 100: ret_cols["ivabase100"] += base; ret_cols["ivaret100"] += val_ret
                    elif codigo == "1": # Estructuración de Fuente Renta
                        c_ret = item.find("codigoRetencion").text.strip() if item.find("codigoRetencion") is not None else ""
                        if c_ret: ret_cols["ftecodret"].append(c_ret)
                        ret_cols["ftebase"] += base
                        ret_cols["fteporcen"].append(f"{p_ret}%")
                        ret_cols["ftevalret"] += val_ret
                except: continue

            fte_cod = ",".join(dict.fromkeys(ret_cols["ftecodret"]))
            fte_pct = ",".join(dict.fromkeys(ret_cols["fteporcen"]))
            
            # Cálculo de diferencia de días
            dias_dif = 0
            try:
                d1 = to_date(f_norm)
                d2 = to_date(fec_doc_sustento)
                if d1 and d2: dias_dif = abs((d1 - d2).days)
            except: pass

            # Estructura del nuevo reporte solicitado
            data.update({
                "ruc_recep": ruc_cli, "nomrecep": nom_cli, "ruc_emisor": ruc_emisor, "razonsocial": razon_social,
                "claveacceso": buscar(["claveAcceso"]), "numautori": buscar(["numeroAutorizacion"]) or buscar(["claveAcceso"]),
                "fecautori": fec_autori or f_norm, "numreten": num_fact, "fechaemi": f_norm, "dias_dif": dias_dif,
                "periodo": norm_periodo(buscar(["periodoFiscal"])), "coddocum": cod_doc_sustento or "01",
                "numdoc": num_doc_sustento, "fecdocum": fec_doc_sustento,
                "ivabase10": ret_cols["ivabase10"], "ivaret10": ret_cols["ivaret10"],
                "ivabase20": ret_cols["ivabase20"], "ivaret20": ret_cols["ivaret20"],
                "ivabase30": ret_cols["ivabase30"], "ivaret30": ret_cols["ivaret30"],
                "ivabase70": ret_cols["ivabase70"], "ivaret70": ret_cols["ivaret70"],
                "ivabase100": ret_cols["ivabase100"], "ivaret100": ret_cols["ivaret100"],
                "ftecodret": fte_cod, "ftebase": ret_cols["ftebase"], "fteporcen": fte_pct, "ftevalret": ret_cols["ftevalret"],
                # Mantenimiento de compatibilidad
                "numfact": num_doc_sustento, "baserenta": ret_cols["ftebase"], "rt_renta": ret_cols["ftevalret"],
                "baseiva": ret_cols["ivabase30"] + ret_cols["ivabase70"], "rt_iva": ret_cols["ivaret30"] + ret_cols["ivaret70"],
                "TOTAL RET": ret_cols["ftevalret"] + ret_cols["ivaret30"] + ret_cols["ivaret70"], "SUSTENTO": num_doc_sustento, "fechaemi": f_norm
            })
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
                cols = [
                    "ruc_recep", "nomrecep", "ruc_emisor", "razonsocial", "claveacceso", 
                    "numautori", "fecautori", "numreten", "fechaemi", "dias_dif", 
                    "periodo", "coddocum", "numdoc", "fecdocum", 
                    "ivabase10", "ivaret10", "ivabase20", "ivaret20", 
                    "ivabase30", "ivaret30", "ivabase70", "ivaret70", 
                    "ivabase100", "ivaret100", 
                    "ftecodret", "ftebase", "fteporcen", "ftevalret"
                ]
                fmt_h = f_verd; sh_nm = "RETENCIONES"
            
            for c in cols: 
                if c not in df.columns: df[c] = ""
            ws = wb.add_worksheet(sh_nm)
            ws.set_footer(texto_pie)
            for i, c in enumerate(cols): ws.write(0, i, c, fmt_h)
            for r, row in enumerate(df[cols].values, 1):
                for c, v in enumerate(row): ws.write(r, c, v, f_num if isinstance(v, (float,int)) else wb.add_format({'border':1}))
            ws.set_column(0, len(cols)-1, 15)

        # === MODO MANUAL (CON CÁLCULOS) ===
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
                
                # TOTALES COMPRAS
                ft = len(df_c) + 1; ws_c.write(ft, 0, "TOTAL", f_tot)
                for ci in range(9, 19):
                    l = xlsxwriter.utility.xl_col_to_name(ci)
                    ws_c.write_formula(ft, ci, f"=SUM({l}2:{l}{ft})", f_tot)

                # REPORTE ANUAL
                ws_ra = wb.add_worksheet('REPORTE ANUAL')
                ws_ra.set_footer(texto_pie)
                ws_ra.set_column('A:K', 14)
                ws_ra.merge_range('B1:B2', "Negocios y\nServicios", f_azul)
                cats=["VIVIENDA","SALUD","EDUCACION","ALIMENTACION","VESTIMENTA","TURISMO","NO DEDUCIBLE","SERVICIOS BASICOS"]
                icos=["🏠","❤️","🎓","🛒","🧢","✈️","🚫","💡"]
                for i,(ct,ic) in enumerate(zip(cats,icos)):
                    ws_ra.write(0,i+2,ic,f_azul); ws_ra.write(1,i+2,ct.title(),f_azul)
                ws_ra.merge_range('K1:K2',"Total Mes",f_azul); ws_ra.write('B3',"PROFESIONALES",f_gris); ws_ra.merge_range('C3:J3',"GASTOS PERSONALES",f_gris)
                
                # Columnas de sumatoria: P(BASE 0), Q(BASE 12), O(NO OBJ), N(EXENTO), J(OTRA BASE), M(PROPINA)
                cl_sum = ["P","Q","O","N","J","M"]
                for r, mes in enumerate(meses):
                    f_idx = r+4
                    ws_ra.write(r+3, 0, mes.title(), f_num)
                    # Suma Profesionales
                    f_pr = "+".join([f"SUMIFS('COMPRAS'!${l}:${l},'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$I:$I,\"PROFESIONAL\")" for l in cl_sum])
                    ws_ra.write_formula(r+3, 1, "="+f_pr, f_num)
                    # Suma por categorías
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
                
                # PROYECCION
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

# AQUI SE CREA LA TERCERA PESTAÑA: "📺 Aprende a usarme"
tab_xml, tab_sri, tab_tutorial = st.tabs(["📂 Subir XMLs (Manual/ZIP)", "📡 Descarga SRI (TXT)", "📺 Aprende a usarme"])

with tab_xml:
    m1, m2, m3, m4 = st.tabs(["🛒 Compras y NC", "💰 Ventas y Retenciones", "📑 Informe Integral", "📑 Reporte de Retenciones"])
    with m1:
        up = st.file_uploader("Compras (XML/ZIP)", type=["xml","zip"], accept_multiple_files=True, key=f"c_{st.session_state.id_proceso}")
        # --- NUEVA LÍNEA DE DESCRIPCIÓN ---
        st.info("💡 **Módulo de Compras:** Sube tus facturas recibidas y notas de crédito en formato xml o zip con xmls.  Este reporte contiene la pestaña de compras y gasto anual. El sistema clasificará automáticamente tus gastos deducibles.")
        if up and st.button("Procesar Compras"):
            data = [extraer_datos_robusto(x) for x in procesar_archivos_entrada(up)]
            data = [d for d in data if d and d["TIPO"] in ["FC","NC"]]
            st.session_state.data_compras_cache = data
            registrar_actividad(st.session_state.usuario_actual, "PROCESÓ COMPRAS MANUAL", len(data))
            st.download_button("📥 Excel Compras", generar_excel_multiexcel(data_compras=data), "Compras.xlsx")
    with m2:
        up = st.file_uploader("Ventas (XML/ZIP)", type=["xml","zip"], accept_multiple_files=True, key=f"v_{st.session_state.id_proceso}")
        # --- NUEVA LÍNEA DE DESCRIPCIÓN ---
        st.info("💡 **Módulo de Ventas:** Carga tus facturas emitidas y retenciones recibidas en formato xml o zip con xmls. El sistema cruzará la información usando los números de sustento.")
        if up and st.button("Procesar Ventas"):
            raw = [extraer_datos_robusto(x) for x in procesar_archivos_entrada(up)]
            data = procesar_ventas_con_retenciones([d for d in raw if d])
            st.session_state.data_ventas_cache = data
            registrar_actividad(st.session_state.usuario_actual, "PROCESÓ VENTAS MANUAL", len(data))
            st.download_button("📥 Excel Ventas", generar_excel_multiexcel(data_ventas_ret=data), "Ventas.xlsx")
    with m3:
        # --- NUEVA LÍNEA DE DESCRIPCIÓN ---
        st.info("💡 **Informe Integral:** Consumo de datos cruzados. Este reporte contiene la pestaña de compras, reporte anual, ventas y proyección. Asegúrate de haber presionado primero el botón de procesar compras y ventas de las dos anteriores pestañas en orden para generar el reporte anual completo.")
        if st.button("🚀 Generar Informe Integral"):
            if st.session_state.data_compras_cache and st.session_state.data_ventas_cache:
                st.download_button("📥 DESCARGAR INTEGRAL", generar_excel_multiexcel(st.session_state.data_compras_cache, st.session_state.data_ventas_cache), "Integral.xlsx")
            else: st.error("Falta procesar Compras y Ventas.")
            registrar_actividad(st.session_state.usuario_actual, "GENERÓ INFORME INTEGRAL")
    with m4:
        up_ret = st.file_uploader("Retenciones (XML/ZIP)", type=["xml","zip"], accept_multiple_files=True, key=f"ret_{st.session_state.id_proceso}")
        st.info("💡 **Reporte de Retenciones:** Sube comprobantes de retención (XML/ZIP) para estructurarlos de forma extendida.")
        if up_ret and st.button("Generar Reporte Retenciones"):
            raw_data = [extraer_datos_robusto(x) for x in procesar_archivos_entrada(up_ret)]
            rets_data = [d for d in raw_data if d and d["TIPO"] == "RET"]
            if rets_data:
                excel_ret = generar_excel_multiexcel(data_sri_lista=rets_data, sri_mode="RET")
                st.success(f"¡Se procesaron {len(rets_data)} comprobantes con el nuevo formato!")
                st.download_button("📥 Descargar Reporte Retenciones", excel_ret, "Reporte_Retenciones.xlsx")
                registrar_actividad(st.session_state.usuario_actual, "PROCESÓ REPORTE RETENCIONES EXTENDIDO", len(rets_data))
            else: st.warning("No se encontraron XMLs de retención válidos.")
with tab_sri:
    def bloque_sri_persistente(titulo, tipo_filtro, key):
        st.subheader(titulo); up = st.file_uploader(f"TXT {titulo}", type=["txt"], key=f"up_{key}")
        # --- NUEVA LÍNEA DE DESCRIPCIÓN ---
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

    s1, s2, s3 = st.tabs(["Facturas", "Notas Crédito", "Retenciones"])
    with s1: bloque_sri_persistente("Facturas Recibidas", "FC", "sri_fc")
    with s2: bloque_sri_persistente("Notas de Crédito", "NC", "sri_nc")
    with s3: bloque_sri_persistente("Retenciones", "RET", "sri_ret")

# AQUI SE CONFIGURA LA NUEVA PESTAÑA CON EL VIDEO DE YOUTUBE
with tab_tutorial:
    st.subheader("🎥 Tutorial: Aprende a usar RAPIDITO AI")
    # st.video automáticamente carga el reproductor en grande dentro de la pestaña y permite darle play
    st.video("https://youtu.be/0iUAI3NAkww?si=aR-Xf9F-GeD1Kj1S")







