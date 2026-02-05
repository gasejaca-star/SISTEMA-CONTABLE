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

URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
HEADERS_WS = {"Content-Type": "text/xml;charset=UTF-8","User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"}
URL_SHEET = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRrwp5uUSVg8g7SfFlNf0ETGNvpFYlsJ-161Sf6yHS7rSG_vc7JVEnTWGlIsixLRiM_tkosgXNQ0GZV/pub?output=csv"

def registrar_actividad(usuario, accion, cantidad=None):
    URL_PUENTE = "https://script.google.com/macros/s/AKfycbyk0CWehcUec47HTGMjqsCs0sTKa_9J3ZU_Su7aRxfwmNa76-dremthTuTPf-FswZY/exec"
    detalle_accion = f"{accion} ({cantidad} XMLs)" if cantidad is not None else accion
    try: requests.post(URL_PUENTE, json={"usuario": str(usuario), "accion": str(detalle_accion)}, timeout=5)
    except: pass

def cargar_usuarios():
    try:
        df = pd.read_csv(URL_SHEET)
        df.columns = [c.lower().strip() for c in df.columns]
        return {str(row['usuario']).strip(): str(row['clave']).strip() for _, row in df.iterrows() if str(row['estado']).lower().strip() == 'activo'}
    except: return {}

# --- 2. SISTEMA DE LOGIN Y ESTADO ---
if "autenticado" not in st.session_state: st.session_state.autenticado = False
if "id_proceso" not in st.session_state: st.session_state.id_proceso = 0
if "data_compras_cache" not in st.session_state: st.session_state.data_compras_cache = []
if "data_ventas_cache" not in st.session_state: st.session_state.data_ventas_cache = []

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
        else: st.sidebar.error("Usuario o contraseña incorrectos.")
    st.stop()

# --- 3. MEMORIA DE APRENDIZAJE ---
if 'memoria' not in st.session_state:
    archivo_memoria = "conocimiento_contable.json"
    if os.path.exists(archivo_memoria):
        with open(archivo_memoria, "r", encoding="utf-8") as f: st.session_state.memoria = json.load(f)
    else: st.session_state.memoria = {"empresas": {}}

def guardar_memoria():
    with open("conocimiento_contable.json", "w", encoding="utf-8") as f: json.dump(st.session_state.memoria, f, indent=4, ensure_ascii=False)

# --- 4. MOTOR DE EXTRACCIÓN XML ---
def extraer_datos_robusto(xml_file):
    try:
        if isinstance(xml_file, (io.BytesIO, io.StringIO)): xml_file.seek(0)
        tree = ET.parse(xml_file)
        root = tree.getroot()
        xml_data = None
        for elem in root.iter():
            if 'comprobante' in elem.tag.lower() and elem.text and "<" in elem.text:
                try:
                    clean_text = re.sub(r'<\?xml.*?\?>', '', elem.text).strip()
                    xml_data = ET.fromstring(clean_text)
                    break
                except: continue
        if xml_data is None: xml_data = root

        root_tag = xml_data.tag.lower()
        if 'notacredito' in root_tag: tipo_doc = "NC"
        elif 'comprobanteretencion' in root_tag: tipo_doc = "RET"
        elif 'liquidacioncompra' in root_tag: tipo_doc = "LC"
        else: tipo_doc = "FC" 

        def buscar(tags):
            for t in tags:
                f = xml_data.find(f".//{t}")
                if f is not None and f.text: return f.text
            return ""
        def buscar_float(tags):
            val = buscar(tags); return float(val) if val else 0.0

        razon_social = buscar(["razonSocial"]).upper()
        ruc_emisor = buscar(["ruc"])
        num_fact_completo = f"{buscar(['estab'])}-{buscar(['ptoEmi'])}-{buscar(['secuencial'])}"
        fecha_emision = buscar(["fechaEmision"])
        num_autori = buscar(["numeroAutorizacion"]) or buscar(["claveAcceso"])
        
        mes_nombre = "DESCONOCIDO"
        if "/" in fecha_emision:
            try:
                meses_dict = {"01":"ENERO","02":"FEBRERO","03":"MARZO","04":"ABRIL","05":"MAYO","06":"JUNIO","07":"JULIO","08":"AGOSTO","09":"SEPTIEMBRE","10":"OCTUBRE","11":"NOVIEMBRE","12":"DICIEMBRE"}
                mes_nombre = meses_dict.get(fecha_emision.split('/')[1], "DESCONOCIDO")
            except: pass

        base_data = {
            "TIPO": tipo_doc,
            "TIPO DE DOCUMENTO": tipo_doc,
            "MES": mes_nombre, "FECHA": fecha_emision, "N. FACTURA": num_fact_completo, "RUC": ruc_emisor, "NOMBRE": razon_social, "N AUTORIZACION": num_autori
        }

        if tipo_doc == "RET":
            rt_renta, rt_iva = 0.0, 0.0
            sustento_formateado = ""
            for imp in xml_data.findall(".//impuesto"):
                cod = imp.find("codigo").text; val = float(imp.find("valorRetenido").text or 0)
                if cod == "1": rt_renta += val
                elif cod == "2": rt_iva += val
                doc_sus = imp.find("numDocSustento").text if imp.find("numDocSustento") is not None else ""
                if doc_sus and "-" in doc_sus: sustento_formateado = doc_sus

            base_data.update({"RET RENTA": rt_renta, "RET IVA": rt_iva, "TOTAL RET": rt_renta + rt_iva, "SUSTENTO": sustento_formateado})
            return base_data

        else: # FC, LC o NC
            m = -1 if tipo_doc == "NC" else 1
            total = buscar_float(["importeTotal", "total", "valorModificado"]) * m
            propina = buscar_float(["propina"]) * m
            base_0, base_12_15, iva_12_15, no_obj_iva, exento_iva, otra_base, otro_monto_iva, ice_val = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            
            for imp in xml_data.findall(".//totalImpuesto"):
                cod = imp.find("codigo").text; cod_por = imp.find("codigoPorcentaje").text
                base = float(imp.find("baseImponible").text or 0) * m
                valor = float(imp.find("valor").text or 0) * m
                if cod == "2": 
                    if cod_por == "0": base_0 += base
                    elif cod_por in ["2", "3", "4", "5", "8", "10"]: base_12_15 += base; iva_12_15 += valor
                    elif cod_por == "6": no_obj_iva += base
                    elif cod_por == "7": exento_iva += base
                    else: otra_base += base; otro_monto_iva += valor
                elif cod == "3": ice_val += valor

            ruc_cliente = buscar(["identificacionComprador", "identificacionSujetoRetenido"])
            nombre_cliente = buscar(["razonSocialComprador", "razonSocialSujetoRetenido"]).upper()
            
            if tipo_doc == "NC":
                detalle_final, memo_final = "", ""
            else:
                info = st.session_state.memoria["empresas"].get(razon_social, {"DETALLE": "OTROS", "MEMO": "PROFESIONAL"})
                detalle_final = info["DETALLE"]
                memo_final = info["MEMO"]
            
            items = [d.find("descripcion").text for d in xml_data.findall(".//detalle") if d.find("descripcion") is not None]
            subdetalle = " | ".join(items[:5]) if items else ""

            base_data.update({
                "RUC CLIENTE": ruc_cliente, "CLIENTE": nombre_cliente,
                "DETALLE": detalle_final, "MEMO": memo_final, "SUBDETALLE": subdetalle,
                "OTRA BASE IVA": otra_base, "OTRO IVA": otro_monto_iva, "MONTO ICE": ice_val, "PROPINAS": propina,
                "EXENTO DE IVA": exento_iva, "NO OBJ IVA": no_obj_iva, "BASE. 0": base_0, "BASE. 12 / 15": base_12_15,
                "IVA.": iva_12_15, "TOTAL": total, "CONTRIBUYENTE": ruc_cliente
            })
            return base_data
    except: return None

# --- 5. LÓGICA DE INTEGRACIÓN ---
def procesar_ventas_con_retenciones(lista_datos_crudos):
    ventas = []
    retenciones_map = {}
    for dato in lista_datos_crudos:
        if dato["TIPO"] == "FC": ventas.append(dato)
        elif dato["TIPO"] == "RET" and dato.get("SUSTENTO"): retenciones_map[dato["SUSTENTO"]] = dato

    ventas_integradas = []
    for venta in ventas:
        num_fact = venta["N. FACTURA"]
        ret_asociada = retenciones_map.get(num_fact, {})
        fila = {
            "MES": venta.get("MES"), "FECHA": venta.get("FECHA"), "N. FACTURA": num_fact,
            "RUC": venta.get("RUC CLIENTE"), "CLIENTE": venta.get("CLIENTE"),
            "DETALLE": "SERVICIOS", "MEMO": "PROFESIONAL", "MONTO REEMBOLS": 0.0,
            "BASE. 0": venta.get("BASE. 0", 0), "BASE. 12 / 15": venta.get("BASE. 12 / 15", 0),
            "IVA": venta.get("IVA.", 0), "TOTAL": venta.get("TOTAL", 0),
            "FECHA RET": ret_asociada.get("FECHA", ""), "N° RET": ret_asociada.get("N. FACTURA", ""),
            "N° AUTORIZACIÓN": ret_asociada.get("N AUTORIZACION", ""),
            "RET RENTA": ret_asociada.get("RET RENTA", 0), "RET IVA": ret_asociada.get("RET IVA", 0),
            "ISD": 0.0, "TOTAL RET": ret_asociada.get("TOTAL RET", 0)
        }
        ventas_integradas.append(fila)
    return ventas_integradas

# --- 6. GENERADOR MULTI-EXCEL MAESTRO ---
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
        
        # MODO SRI (Descarga Masiva)
        if sri_mode:
            df = pd.DataFrame(data_sri_lista)
            if sri_mode == "NC":
                cols = ["NOMBRE","RUC","N AUTORIZACION","FECHA","TIPO DE DOCUMENTO","N. FACTURA","MES","RUC CLIENTE","CLIENTE","PROPINAS","BASE. 0","NO OBJ IVA","BASE. 12 / 15","IVA.","TOTAL"]
                header_fmt = f_amar
                sheet_name = "NOTAS DE CREDITO"
            elif sri_mode == "RET":
                cols = ["RUC CLIENTE","CLIENTE","FECHA","NOMBRE","RUC","SUSTENTO","N. FACTURA","RET RENTA","RET IVA","TOTAL RET","N AUTORIZACION"]
                header_fmt = f_verd
                sheet_name = "RETENCIONES"
            else: # FC
                cols = ["MES","FECHA","N. FACTURA","TIPO DE DOCUMENTO","RUC","CONTRIBUYENTE","NOMBRE","DETALLE","MEMO","OTRA BASE IVA","OTRO IVA","MONTO ICE","PROPINAS","EXENTO DE IVA","NO OBJ IVA","BASE. 0","BASE. 12 / 15","IVA.","TOTAL","SUBDETALLE"]
                header_fmt = f_azul
                sheet_name = "FACTURAS"

            for c in cols: 
                if c not in df.columns: df[c] = ""
            df = df[cols]
            
            ws = wb.add_worksheet(sheet_name)
            for i, c in enumerate(cols): ws.write(0, i, c, header_fmt)
            for r, row in enumerate(df.values, 1):
                for c, val in enumerate(row): ws.write(r, c, val, f_num if isinstance(val, (int,float)) else wb.add_format({'border':1}))
            return output.getvalue()

        # MODO MANUAL (INTEGRAL)
        meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

        # HOJA COMPRAS
        if data_compras:
            df_c = pd.DataFrame(data_compras)
            orden_c = ["MES","FECHA","N. FACTURA","TIPO DE DOCUMENTO","RUC","CONTRIBUYENTE","NOMBRE","DETALLE","MEMO","OTRA BASE IVA","OTRO IVA","MONTO ICE","PROPINAS","EXENTO DE IVA","NO OBJ IVA","BASE. 0","BASE. 12 / 15","IVA.","TOTAL","SUBDETALLE"]
            for c in orden_c: 
                if c not in df_c.columns: df_c[c] = ""
            df_c = df_c[orden_c]
            
            ws_c = wb.add_worksheet('COMPRAS')
            for i, c in enumerate(orden_c):
                fmt = f_amar if i in range(9, 19) else f_azul
                ws_c.write(0, i, c, fmt)
            for r, row in enumerate(df_c.values, 1):
                for c, val in enumerate(row): ws_c.write(r, c, val, f_num if isinstance(val, (int,float)) else wb.add_format({'border':1}))
            
            # TOTAL COMPRAS
            ft = len(df_c) + 1; ws_c.write(ft, 0, "TOTAL", f_tot)
            for cidx in range(9, 19): 
                l = xlsxwriter.utility.xl_col_to_name(cidx); ws_c.write_formula(ft, cidx, f"=SUM({l}2:{l}{ft})", f_tot)

            # HOJA REPORTE ANUAL
            ws_ra = wb.add_worksheet('REPORTE ANUAL')
            ws_ra.set_column('A:K', 14); ws_ra.merge_range('B1:B2', "Negocios y\nServicios", f_azul)
            cats=["VIVIENDA","SALUD","EDUCACION","ALIMENTACION","VESTIMENTA","TURISMO","NO DEDUCIBLE","SERVICIOS BASICOS"]
            icos=["🏠","❤️","🎓","🛒","🧢","✈️","🚫","💡"]
            for i,(ct,ic) in enumerate(zip(cats,icos)): ws_ra.write(0,i+2,ic,f_azul); ws_ra.write(1,i+2,ct.title(),f_azul)
            ws_ra.merge_range('K1:K2',"Total Mes",f_azul); ws_ra.write('B3',"PROFESIONALES",f_gris); ws_ra.merge_range('C3:J3',"GASTOS PERSONALES",f_gris)
            
            # Índices de columnas de valor en COMPRAS (0-based en Python, pero excel necesita letras)
            # 15=P(Base0), 16=Q(Base12), 17=R(IVA) -> Bases Importantes para Gastos
            cols_gasto = ["P","Q","R"] 
            
            for r, mes in enumerate(meses):
                fila = r+4; ws_ra.write(r+3,0,mes.title(),f_num)
                # Formula Profesional (Suma Bases + IVA si MEMO=PROFESIONAL)
                f_pr = "+".join([f"SUMIFS('COMPRAS'!${l}:${l},'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$I:$I,\"PROFESIONAL\")" for l in ["P","Q","R"]])
                ws_ra.write_formula(r+3,1,"="+f_pr,f_num)
                # Formula Gastos Personales
                for cidx, cat in enumerate(cats):
                    f_pe = "+".join([f"SUMIFS('COMPRAS'!${l}:${l},'COMPRAS'!$A:$A,\"{mes}\",'COMPRAS'!$H:$H,\"{cat}\")" for l in cols_gasto])
                    ws_ra.write_formula(r+3,cidx+2,"="+f_pe,f_num)
                ws_ra.write_formula(r+3,10,f"=SUM(B{fila}:J{fila})",f_num)
            ws_ra.write(15,0,"TOTAL",f_tot)
            for c in range(1,11): l=xlsxwriter.utility.xl_col_to_name(c); ws_ra.write_formula(15,c,f"=SUM({l}4:{l}15)",f_tot)

        # HOJA VENTAS
        if data_ventas_ret:
            df_v = pd.DataFrame(data_ventas_ret)
            orden_v = ["MES","FECHA","N. FACTURA","RUC","CLIENTE","DETALLE","MEMO","MONTO REEMBOLS","BASE. 0","BASE. 12 / 15","IVA","TOTAL","FECHA RET","N° RET","N° AUTORIZACIÓN","RET RENTA","RET IVA","ISD","TOTAL RET"]
            for c in orden_v: 
                if c not in df_v.columns: df_v[c] = ""
            df_v = df_v[orden_v]
            
            ws_v = wb.add_worksheet('VENTAS')
            for i, c in enumerate(orden_v): ws_v.write(0, i, c, f_verd if i >= 12 else f_azul)
            for r, row in enumerate(df_v.values, 1):
                for c, val in enumerate(row): ws_v.write(r, c, val, f_num if isinstance(val, (int,float)) else wb.add_format({'border':1}))
            
            ft_v = len(df_v) + 1; ws_v.write(ft_v, 0, "TOTAL", f_tot)
            for cidx in range(7, 19): l = xlsxwriter.utility.xl_col_to_name(cidx); ws_v.write_formula(ft_v, cidx, f"=SUM({l}2:{l}{ft_v})", f_tot)

            # HOJA PROYECCION
            ws_p = wb.add_worksheet('PROYECCION')
            ws_p.set_column('A:A', 12); ws_p.set_column('B:M', 15)
            ws_p.merge_range('A1:D1', f"PERIODO: {datetime.now().year}", f_azul)
            for i, h in enumerate(["VENTAS", "COMPRAS", "TOTAL"]): ws_p.write(i+2, 0, h, f_azul)
            
            for c, mes in enumerate(meses):
                col = c + 1; l = xlsxwriter.utility.xl_col_to_name(col)
                ws_p.write(1, col, mes, f_azul)
                ws_p.write_formula(2, col, f"=SUMIFS(VENTAS!$I:$I,VENTAS!$A:$A,\"{mes}\") + SUMIFS(VENTAS!$J:$J,VENTAS!$A:$A,\"{mes}\")", f_num)
                if data_compras: ws_p.write_formula(3, col, f"=SUMIFS('COMPRAS'!$P:$P,'COMPRAS'!$A:$A,\"{mes}\") + SUMIFS('COMPRAS'!$Q:$Q,'COMPRAS'!$A:$A,\"{mes}\")", f_num)
                else: ws_p.write(3, col, 0, f_num)
                ws_p.write_formula(4, col, f"={l}3-{l}4", f_tot)
            
            lt = xlsxwriter.utility.xl_col_to_name(len(meses)+1)
            ws_p.write(1, len(meses)+1, "TOTAL", f_azul)
            for r in range(2,5): ws_p.write_formula(r, len(meses)+1, f"=SUM(B{r+1}:{xlsxwriter.utility.xl_col_to_name(len(meses))}{r+1})", f_tot)

    return output.getvalue()

# --- 7. INTERFAZ ---
st.title(f"🚀 RAPIDITO - {st.session_state.usuario_actual}")

with st.sidebar:
    st.header("Menú Principal")
    if st.button("🧹 NUEVO INFORME", type="primary"):
        st.session_state.id_proceso += 1; st.session_state.data_compras_cache = []; st.session_state.data_ventas_cache = []
        st.rerun()
    st.markdown("---")
    if st.session_state.usuario_actual == "GABRIEL":
        st.header("Master Config")
        up_xls = st.file_uploader("Cargar Excel Maestro", type=["xlsx"], key=f"mst_{st.session_state.id_proceso}")
        if up_xls:
            df = pd.read_excel(up_xls); df.columns = [c.upper().strip() for c in df.columns]
            for _, r in df.iterrows():
                nm = str(r.get("NOMBRE","")).upper().strip()
                if nm: st.session_state.memoria["empresas"][nm] = {"DETALLE":str(r.get("DETALLE","OTROS")).upper(),"MEMO":str(r.get("MEMO","PROFESIONAL")).upper()}
            guardar_memoria(); st.success("Memoria actualizada.")
    st.markdown("---")
    if st.button("Cerrar Sesión"):
        registrar_actividad(st.session_state.usuario_actual, "SALIÓ"); st.session_state.autenticado = False; st.rerun()

tab_xml, tab_sri = st.tabs(["📂 Subir XMLs (Manual)", "📡 Descarga SRI (TXT)"])

with tab_xml:
    st1, st2, st3 = st.tabs(["🛒 Compras y NC", "💰 Ventas y Retenciones", "📑 Informe Integral"])
    with st1:
        up_c = st.file_uploader("Subir Compras/NC XML", type=["xml"], accept_multiple_files=True, key=f"c_{st.session_state.id_proceso}")
        if up_c and st.button("Procesar Compras"):
            data = [extraer_datos_robusto(x) for x in up_c]; data = [d for d in data if d and d["TIPO"] in ["FC","NC"]]
            if data:
                st.session_state.data_compras_cache = data
                st.download_button("📥 Reporte Compras", generar_excel_multiexcel(data_compras=data), f"C_{datetime.now().strftime('%H%M')}.xlsx")
    with st2:
        up_v = st.file_uploader("Subir Ventas/Ret XML", type=["xml"], accept_multiple_files=True, key=f"v_{st.session_state.id_proceso}")
        if up_v and st.button("Procesar Ventas"):
            data = [extraer_datos_robusto(x) for x in up_v]; data = [d for d in data if d]
            if data:
                res = procesar_ventas_con_retenciones(data)
                st.session_state.data_ventas_cache = res
                st.download_button("📥 Reporte Ventas", generar_excel_multiexcel(data_ventas_ret=res), f"V_{datetime.now().strftime('%H%M')}.xlsx")
    with st3:
        if st.button("Generar Informe Integral"):
            if st.session_state.data_compras_cache and st.session_state.data_ventas_cache:
                st.download_button("📥 INFORME INTEGRAL", generar_excel_multiexcel(st.session_state.data_compras_cache, st.session_state.data_ventas_cache), f"INT_{datetime.now().strftime('%H%M')}.xlsx")
            else: st.warning("Procese Compras y Ventas primero.")

with tab_sri:
    def bloque_sri(titulo, tipo_filtro, key):
        st.subheader(titulo)
        up = st.file_uploader(f"TXT {titulo}", type=["txt"], key=key)
        if up and st.button(f"Descargar {titulo}", key=f"b_{key}"):
            claves = list(dict.fromkeys(re.findall(r'\d{49}', up.read().decode("latin-1"))))
            if claves:
                bar = st.progress(0); lst = []
                for i, cl in enumerate(claves):
                    try:
                        r = requests.post(URL_WS, data=f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{cl}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>', headers=HEADERS_WS, verify=False, timeout=5)
                        if r.status_code==200: 
                            d = extraer_datos_robusto(io.BytesIO(r.content))
                            if d and d["TIPO"] == tipo_filtro: lst.append(d)
                    except: pass
                    bar.progress((i+1)/len(claves))
                if lst: st.download_button(f"📊 Excel {titulo}", generar_excel_multiexcel(data_sri_lista=lst, sri_mode=tipo_filtro), f"{titulo}.xlsx")

    s1, s2, s3 = st.tabs(["Facturas", "Notas Crédito", "Retenciones"])
    with s1: bloque_sri("Facturas Recibidas", "FC", "sri_fc")
    with s2: bloque_sri("Notas de Crédito", "NC", "sri_nc")
    with s3: bloque_sri("Retenciones", "RET", "sri_ret")

