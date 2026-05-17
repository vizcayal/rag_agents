import streamlit as st
import boto3
import json
import os
import uuid

# Configuración de la página
st.set_page_config(page_title="CONSULTA DE LA LEY DE IA EN EUROPA", page_icon="🤖")
st.title("🤖 CONSULTA DE LA LEY DE IA EN EUROPA")

# Configuración de AWS desde variables de entorno
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:911268715109:runtime/ragAgent_MyAgent-320L5P7elr")
REGION = os.environ.get("REGION", "us-east-1")

# Inicializar cliente de Bedrock AgentCore
# Nota: Dependiendo de tu versión de boto3, el cliente puede ser 'bedrock-agentcore'
try:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
except Exception:
    # Fallback si el nombre del servicio es diferente en versiones antiguas
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)

# Inicializar historial de chat y sesión
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# Mostrar mensajes anteriores
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Entrada del usuario
if prompt := st.chat_input("¿Qué quieres saber sobre la Ley de IA? (ej. What is AI?)", key="chat_input"):
    # Agregar mensaje del usuario al historial
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Llamada al agente
    with st.chat_message("assistant"):
        with st.spinner("Pensando..."):
            try:
                # Llamar al runtime de AgentCore
                response = client.invoke_agent_runtime(
                    agentRuntimeArn=RUNTIME_ARN,
                    payload=json.dumps({"prompt": prompt}).encode("utf-8"),
                    contentType="application/json",
                    runtimeSessionId=st.session_state.get("session_id", str(uuid.uuid4())),
                )

                # El cliente 'bedrock-agentcore' devuelve un EventStream en el campo 'response'
                result_raw = ""
                for event in response.get("response", []):
                    # Si el evento son bytes directos (StreamingBody o fallback)
                    if isinstance(event, bytes):
                        result_raw += event.decode("utf-8")
                    elif isinstance(event, str):
                        result_raw += event
                    # Si el evento es un diccionario (EventStream estándar)
                    elif isinstance(event, dict):
                        if 'chunk' in event:
                            chunk_data = event['chunk'].get('bytes', b'')
                            if isinstance(chunk_data, bytes):
                                result_raw += chunk_data.decode("utf-8")
                            else:
                                result_raw += str(chunk_data)
                        elif 'internalServerException' in event:
                            st.error(f"Error interno del servidor: {event['internalServerException']}")
                        elif 'badRequestException' in event:
                            st.error(f"Petición inválida: {event['badRequestException']}")

                if not result_raw:
                    st.warning("El agente devolvió una respuesta vacía.")
                    st.stop()

                result = json.loads(result_raw)
                
                # Extraer respuesta y citas
                answer = result.get("response") or result.get("answer")
                citations = result.get("cited_chunks") or result.get("cited_chunk_ids", [])
                
                if answer is None:
                    st.write("---")
                    st.write("Raw Result from Agent:", result)
                    answer = "No se pudo extraer una respuesta del payload del agente."

                st.markdown(answer)
                
                # Guardar respuesta en el historial
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": answer
                })
                
            except Exception as e:
                import traceback
                st.error(f"Error al llamar al agente: {str(e)}")
                st.code(traceback.format_exc())
