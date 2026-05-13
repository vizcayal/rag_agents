import streamlit as st
import boto3
import json
import os
import uuid

# Configuración de la página
st.set_page_config(page_title="RAG Agent UI", page_icon="🤖")
st.title("🤖 RAG Agent: EU AI Act Explorer")

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
        if "citations" in message:
            with st.expander("Ver citas"):
                for chunk in message["citations"]:
                    st.info(f"ID: {chunk}")

# Entrada del usuario
if prompt := st.chat_input("¿Qué quieres saber sobre la Ley de IA?"):
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
                    payload=json.dumps({"prompt": prompt}),
                    contentType="application/json",
                    runtimeSessionId=st.session_state.get("session_id", str(uuid.uuid4())),
                )

                # La respuesta usa 'response' (EventStream), no 'body'
                events = []
                for event in response.get("response", []):
                    if isinstance(event, bytes):
                        try:
                            events.append(event.decode("utf-8"))
                        except UnicodeDecodeError:
                            pass
                    else:
                        events.append(event)

                result_raw = "".join(str(e) for e in events)
                result = json.loads(result_raw)
                
                answer = result.get("response", "No se generó respuesta.")
                citations = result.get("cited_chunks", [])
                
                st.markdown(answer)
                
                if citations:
                    with st.expander("Ver citas"):
                        for chunk in citations:
                            st.info(f"ID: {chunk}")
                
                # Guardar respuesta en el historial
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": answer,
                    "citations": citations
                })
                
            except Exception as e:
                st.error(f"Error al llamar al agente: {str(e)}")
