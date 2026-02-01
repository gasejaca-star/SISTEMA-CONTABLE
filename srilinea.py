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
import xlsxwriter

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="RAPIDITO AI - Master Web", layout="wide", page_icon="📊")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración del motor Web Service
URL_WS = "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline?wsdl"
HEADERS_WS = {
    "Content-Type": "text/xml;charset=UTF-8",
    "User-Agent": "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.2; WOW64; Trident/7.0; .NET4.0C; .NET4.0E; Zoom 3.6.0)",
    "Cookie": "TS010a7529=0115ac86d2859bb60ce3e314743f6a0cee3bcf365d8cb7ce8e5ef76bbc09c6509733dfb5dcf2a1b1dc29feb273505a1d0838bc427c"
}

# (Las funciones de login y memoria se mantienen igual a tu código original)
# ... [Funciones cargar_usuarios, registrar_actividad, guardar_memoria] ...

# --- MOTOR DE DESCARGA WS CON BARRA DE PROGRESO ---
def descargar_solo_xmls(file_txt):
    try:
        content = file_txt.read().decode("utf-8")
        claves = list(dict.fromkeys(re.findall(r'\d{49}', content)))
        
        if not claves:
            st.warning("⚠️ No se encontraron claves de acceso en el archivo.")
            return None

        zip_buffer = io.BytesIO()
        exitos = 0
        fallidos = 0
        
        # UI: Barra de progreso y texto de estado
        progreso_txt = st.empty()
        barra = st.progress(0)
        
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for i, clave in enumerate(claves):
                payload = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ec="http://ec.gob.sri.ws.autorizacion">
                   <soapenv:Body><ec:autorizacionComprobante><claveAccesoComprobante>{clave}</claveAccesoComprobante></ec:autorizacionComprobante></soapenv:Body>
                </soapenv:Envelope>"""
                
                try:
                    # Intento de conexión con el WS
                    r = requests.post(URL_WS, data=payload, headers=HEADERS_WS, verify=False, timeout=8)
                    
                    if r.status_code == 200 and "<autorizaciones>" in r.text:
                        zip_file.writestr(f"{clave}.xml", r.text)
                        exitos += 1
                    else:
                        fallidos += 1
                except Exception:
                    fallidos += 1
                
                # Actualizar barra de progreso
                porcentaje = (i + 1) / len(claves)
                barra.progress(porcentaje)
                progreso_txt.text(f"Procesando: {int(porcentaje*100)}% ({i+1}/{len(claves)})")
        
        # Notificación final de estado
        if exitos > 0:
            st.success(f"✅ Descarga completada: {exitos} exitosos. {f'❌ {fallidos} fallidos.' if fallidos > 0 else ''}")
            return zip_buffer.getvalue()
        else:
            st.error("🚨 No se pudo conectar con el servidor del SRI o las claves no son válidas.")
            return None
    except Exception as e:
        st.error(f"💥 Error crítico: {str(e)}")
        return None

# --- INTERFAZ PRINCIPAL ---
st.title(f"🚀 RAPIDITO AI - {st.session_state.get('usuario_actual', 'Sistema')}")

col1, col2 = st.columns(2)
with col1:
    st.subheader("📁 Procesar Reporte (Excel)")
    up_xmls = st.file_uploader("Subir XMLs Manuales", type=["xml"], accept_multiple_files=True)
    up_txt_reporte = st.file_uploader("Subir Recibidos.txt para Reporte", type=["txt"], key="reporte")
    
    if st.button("📊 GENERAR REPORTE EXCEL"):
        # Lógica de generación de reporte que ya tenías
        # ... (Extraer datos y mostrar st.download_button para Excel) ...
        pass

with col2:
    st.subheader("📦 Descargar Comprobantes (XML)")
    up_txt_descarga = st.file_uploader("Subir Recibidos.txt para Descarga", type=["txt"], key="descarga")
    
    if st.button("📥 INICIAR DESCARGA XMLs"):
        if up_txt_descarga:
            zip_final = descargar_solo_xmls(up_txt_descarga)
            if zip_final:
                st.download_button(
                    label="💾 GUARDAR ARCHIVO ZIP",
                    data=zip_final,
                    file_name=f"XMLs_SRI_{st.session_state.get('usuario_actual', 'Download')}.zip",
                    mime="application/zip"
                )
        else:
            st.warning("⚠️ Primero sube el archivo .txt en esta sección.")
