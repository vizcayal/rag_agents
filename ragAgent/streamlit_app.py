import streamlit as st
import boto3
import json
import os
import uuid
import urllib.parse

# Page configuration with a professional icon and layout
st.set_page_config(
    page_title="EU AI Act Explorer — Consulta Grounded",
    page_icon="⚖️",
    layout="wide"
)

# Custom CSS for rich aesthetics and premium styling
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
    
    /* Font style propagation */
    html, body, [class*="css"], .stMarkdown {
        font-family: 'Outfit', sans-serif;
    }
    
    /* Overall Background and layout styling */
    .stApp {
        background-color: #0d1117;
        color: #c9d1d9;
    }
    
    /* Glassmorphic Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0b0e14 !important;
        border-right: 1px solid #21262d !important;
    }
    
    /* Sleek gradient title */
    .title-gradient {
        background: linear-gradient(135deg, #58a6ff 0%, #bc8cff 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.8rem;
        font-weight: 700;
        margin-bottom: 2px;
        letter-spacing: -0.5px;
    }
    
    .subtitle {
        color: #8b949e;
        font-size: 1.15rem;
        margin-bottom: 25px;
        font-weight: 300;
        line-height: 1.5;
    }
    
    /* Pulsating Status Dot */
    .status-container {
        display: flex;
        align-items: center;
        gap: 12px;
        background: rgba(22, 27, 34, 0.7);
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 12px 16px;
        margin-bottom: 20px;
    }
    
    .pulsate-dot {
        width: 10px;
        height: 10px;
        background: #3fb950;
        border-radius: 50%;
        box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.7);
        animation: pulsate 2s infinite;
    }
    
    @keyframes pulsate {
        0% {
            transform: scale(0.95);
            box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.7);
        }
        70% {
            transform: scale(1);
            box-shadow: 0 0 0 6px rgba(63, 185, 80, 0);
        }
        100% {
            transform: scale(0.95);
            box-shadow: 0 0 0 0 rgba(63, 185, 80, 0);
        }
    }
    
    .status-text {
        font-size: 0.9rem;
        color: #c9d1d9;
        font-weight: 500;
    }
    
    /* Sidebar info panel cards */
    .sidebar-panel {
        background: rgba(22, 27, 34, 0.5);
        border: 1px solid #30363d;
        border-radius: 10px;
        padding: 15px;
        margin-bottom: 15px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.15);
        transition: border-color 0.2s;
    }
    .sidebar-panel:hover {
        border-color: #58a6ff;
    }
    
    .sidebar-panel-title {
        font-size: 0.75rem;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        font-weight: 600;
        margin-bottom: 8px;
    }
    
    .sidebar-panel-value {
        font-size: 0.95rem;
        color: #e6edf3;
        font-weight: 500;
    }
    
    /* Beautiful RAG Flow Diagram in Sidebar */
    .flow-container {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin-top: 10px;
    }
    .flow-step {
        background: rgba(33, 38, 45, 0.4);
        border: 1px solid rgba(48, 54, 61, 0.5);
        border-radius: 6px;
        padding: 8px 12px;
        font-size: 0.8rem;
        color: #8b949e;
        text-align: center;
        transition: all 0.2s;
    }
    .flow-step.active {
        background: rgba(88, 166, 255, 0.08);
        border-color: rgba(88, 166, 255, 0.4);
        color: #e6edf3;
        font-weight: 500;
    }
    .flow-arrow {
        text-align: center;
        color: #30363d;
        font-size: 0.8rem;
        margin: -2px 0;
    }
    
    /* Grounding metadata section */
    .meta-box {
        margin-top: 15px;
        padding-top: 15px;
        border-top: 1px solid #30363d;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    
    /* Citation Card */
    .citation-card {
        background: rgba(22, 27, 34, 0.6);
        border: 1px solid #30363d;
        border-left: 4px solid #58a6ff;
        border-radius: 8px;
        padding: 14px;
        margin-top: 8px;
        font-size: 0.88rem;
        line-height: 1.4;
        transition: border-color 0.2s, background-color 0.2s, transform 0.2s;
    }
    .citation-card:hover {
        background: rgba(33, 38, 45, 0.8);
        border-left-color: #bc8cff;
        transform: translateX(4px);
    }
    .citation-header {
        font-weight: 600;
        color: #58a6ff;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    /* Dynamic pill badges */
    .badge-passed {
        background: rgba(56, 139, 253, 0.15);
        color: #58a6ff;
        border: 1px solid rgba(56, 139, 253, 0.4);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.82rem;
        font-weight: 600;
        display: inline-block;
        box-shadow: 0 2px 5px rgba(56, 139, 253, 0.1);
    }
    .badge-warning {
        background: rgba(210, 153, 34, 0.15);
        color: #d29922;
        border: 1px solid rgba(210, 153, 34, 0.4);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.82rem;
        font-weight: 600;
        display: inline-block;
        box-shadow: 0 2px 5px rgba(210, 153, 34, 0.1);
    }
    .badge-failed {
        background: rgba(248, 81, 73, 0.15);
        color: #ff7b72;
        border: 1px solid rgba(248, 81, 73, 0.4);
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 0.82rem;
        font-weight: 600;
        display: inline-block;
        box-shadow: 0 2px 5px rgba(248, 81, 73, 0.1);
    }
    
    /* Streamlit Chat Message overrides for glassmorphic styling */
    [data-testid="stChatMessage"] {
        background: rgba(22, 27, 34, 0.4) !important;
        border: 1px solid rgba(48, 54, 61, 0.6) !important;
        border-radius: 12px !important;
        padding: 18px !important;
        margin-bottom: 15px !important;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1) !important;
        transition: border-color 0.2s, background-color 0.2s;
    }
    [data-testid="stChatMessage"]:hover {
        border-color: rgba(88, 166, 255, 0.4) !important;
        background: rgba(33, 38, 45, 0.5) !important;
    }
    
    /* Premium Multi-line card buttons styling */
    div.stButton > button {
        white-space: pre-line !important;
        text-align: left !important;
        display: block !important;
        width: 100% !important;
        background: rgba(22, 27, 34, 0.7) !important;
        border: 1px solid #30363d !important;
        border-radius: 12px !important;
        padding: 20px 18px !important;
        font-size: 0.95rem !important;
        line-height: 1.4 !important;
        color: #8b949e !important;
        transition: all 0.25s cubic-bezier(0.165, 0.84, 0.44, 1) !important;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
    }
    div.stButton > button::first-line {
        font-weight: 600 !important;
        font-size: 1.05rem !important;
        color: #e6edf3 !important;
    }
    div.stButton > button:hover {
        border-color: #58a6ff !important;
        background: rgba(33, 38, 45, 0.8) !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(88, 166, 255, 0.15) !important;
    }
    div.stButton > button:hover::first-line {
        color: #58a6ff !important;
    }
    
    /* Welcome Container background */
    .welcome-container {
        background: radial-gradient(circle at top right, rgba(188, 140, 255, 0.04), transparent 60%);
        padding: 30px;
        border-radius: 16px;
        border: 1px solid rgba(48, 54, 61, 0.3);
        margin-bottom: 25px;
    }
</style>
""", unsafe_allow_html=True)

# Configuration from Environment Variables
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:911268715109:runtime/ragAgent_MyAgent-3AkJyICSTJ")
REGION = os.environ.get("REGION", "us-east-1")

# Initialize Bedrock AgentCore/Runtime client
try:
    client = boto3.client("bedrock-agentcore", region_name=REGION)
except Exception:
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)

# Initialize Session and Message States
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "suggested_query" not in st.session_state:
    st.session_state.suggested_query = None

# ==============================================================================
# SIDEBAR DESIGN
# ==============================================================================
with st.sidebar:
    st.markdown('<div class="status-container"><div class="pulsate-dot"></div><div class="status-text">Conexión AWS Activa</div></div>', unsafe_allow_html=True)
    
    st.markdown("""
    <div class="sidebar-panel">
        <div class="sidebar-panel-title">Modelo de Lenguaje</div>
        <div class="sidebar-panel-value">🤖 Amazon Nova Lite</div>
    </div>
    <div class="sidebar-panel">
        <div class="sidebar-panel-title">Base de Conocimiento</div>
        <div class="sidebar-panel-value">📚 Bedrock KB (ID: 7UZ4I)</div>
    </div>
    <div class="sidebar-panel">
        <div class="sidebar-panel-title">Filtros de Validación</div>
        <div class="sidebar-panel-value">🛡️ Agente LangGraph</div>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    st.markdown('<div class="sidebar-panel-title">Arquitectura RAG</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="flow-container">
        <div class="flow-step">1. Consulta de Usuario</div>
        <div class="flow-arrow">↓</div>
        <div class="flow-step active">2. Recuperación Vectorial (AOSS)</div>
        <div class="flow-arrow">↓</div>
        <div class="flow-step">3. Contexto + Bedrock Nova</div>
        <div class="flow-arrow">↓</div>
        <div class="flow-step active">4. Verificador de Respuestas</div>
    </div>
    """, unsafe_allow_html=True)

# ==============================================================================
# MAIN PAGE DESIGN
# ==============================================================================
st.markdown('<div class="title-gradient">EU AI Act Explorer</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Búsqueda semántica grounded y razonamiento con citación de fuentes para el Reglamento de IA en Europa</div>', unsafe_allow_html=True)

# Render Welcome Screen if no messages
if not st.session_state.messages:
    st.markdown("""
    <div class="welcome-container">
        <h3 style="margin-top:0; color:#e6edf3; font-weight:600;">¡Bienvenido al Explorador del Reglamento de IA de la UE!</h3>
        <p style="color:#8b949e; line-height:1.6; margin-bottom:0;">
            Esta herramienta utiliza Inteligencia Artificial Generativa e indexación semántica sobre el texto oficial del 
            <b>Reglamento de Inteligencia Artificial de la Unión Europea (AI Act)</b>. 
            Todas las consultas son verificadas mediante un pipeline RAG (Generación Recuperada por Búsqueda) para garantizar la veracidad, 
            citando las páginas y fragmentos exactos donde se sustenta la información.
        </p>
    </div>
    <h4 style="color:#e6edf3; font-weight:500; margin-bottom:15px; margin-top:20px;">Preguntas Recomendadas</h4>
    """, unsafe_allow_html=True)
    
    # 2x2 grid of suggestion cards
    col1, col2 = st.columns(2)
    with col1:
        if st.button("⚖️ Prácticas Prohibidas\nVer los sistemas de IA totalmente prohibidos en la UE", key="sug_proh", use_container_width=True):
            st.session_state.suggested_query = "¿Cuáles son las prácticas de IA prohibidas según el reglamento?"
            st.rerun()
        if st.button("⚠️ Sistemas de Alto Riesgo\nConsultar obligaciones para clasificaciones de alto riesgo", key="sug_risk", use_container_width=True):
            st.session_state.suggested_query = "¿Qué sistemas de IA se consideran de alto riesgo y qué obligaciones tienen?"
            st.rerun()
    with col2:
        if st.button("🗓️ Fechas de Aplicación\nVer el calendario oficial de entrada en vigor de la Ley", key="sug_dates", use_container_width=True):
            st.session_state.suggested_query = "¿Cuándo entra en vigor y cuáles son las fechas clave del reglamento?"
            st.rerun()
        if st.button("💸 Multas y Sanciones\nEntender los límites financieros por infracciones", key="sug_fines", use_container_width=True):
            st.session_state.suggested_query = "¿Cuáles son las sanciones y multas económicas aplicables en caso de infracción?"
            st.rerun()

# Render Chat History
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        
        # If assistant has metadata, render the verification cards
        if message["role"] == "assistant" and "confidence" in message:
            confidence = message.get("confidence", 0.0)
            status = message.get("status", "unknown")
            citations = message.get("citations", [])
            
            # Render custom metadata block
            if status == "passed":
                badge_html = f'<div class="badge-passed">✓ Grounded ({int(confidence*100)}% de confianza)</div>'
            elif status == "warning":
                badge_html = f'<div class="badge-warning">⚠ Respuesta Parcial ({int(confidence*100)}% de confianza)</div>'
            elif status == "failed":
                badge_html = f'<div class="badge-failed">✗ No Verificado ({int(confidence*100)}% de confianza)</div>'
            else:
                badge_html = f'<div class="badge-passed">Confianza: {int(confidence*100)}%</div>'
                
            citations_html = ""
            if citations:
                citations_html += '<div style="margin-top: 15px; font-weight: 600; font-size: 0.9rem; color: #8b949e;">Referencias de la Base de Conocimiento (S3 Source):</div>'
                for idx, c in enumerate(citations):
                    decoded = urllib.parse.unquote(c)
                    citations_html += f"""
                    <div class="citation-card">
                        <div class="citation-header">📄 Referencia #{idx + 1}</div>
                        <div style="color: #e6edf3; font-weight: 400; margin-bottom: 8px;">"{decoded}"</div>
                        <div style="color: #8b949e; font-size: 0.78rem;">Ubicación: s3://rag-agents-docs/ai_act.pdf</div>
                    </div>
                    """
            
            st.markdown(f"""
            <div class="meta-box">
                <div>{badge_html}</div>
                {citations_html}
            </div>
            """, unsafe_allow_html=True)

# Handle inputs (either suggested query or chat input)
prompt = None
if st.session_state.suggested_query:
    prompt = st.session_state.suggested_query
    st.session_state.suggested_query = None  # Reset suggestion trigger
else:
    prompt = st.chat_input("Escribe tu pregunta sobre la Ley de IA de la UE...", key="chat_input")

if prompt:
    # Append user prompt and render
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.rerun()  # Rerun to draw user query immediately, then handle generation

# Process last message if it's user and assistant hasn't responded
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    user_prompt = st.session_state.messages[-1]["content"]
    
    # Query the AgentCore runtime
    with st.chat_message("assistant"):
        with st.spinner("Buscando en el Reglamento de la Ley de IA y razonando..."):
            try:
                response = client.invoke_agent_runtime(
                    agentRuntimeArn=RUNTIME_ARN,
                    payload=json.dumps({"prompt": user_prompt}).encode("utf-8"),
                    contentType="application/json",
                    runtimeSessionId=st.session_state.get("session_id", str(uuid.uuid4())),
                )

                # Parse response stream
                result_raw = ""
                for event in response.get("response", []):
                    if isinstance(event, bytes):
                        result_raw += event.decode("utf-8")
                    elif isinstance(event, str):
                        result_raw += event
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
                
                # Extract results
                answer = result.get("response") or result.get("answer")
                citations = result.get("cited_chunks") or result.get("cited_chunk_ids", [])
                confidence = result.get("confidence", 0.0)
                status = result.get("status", "unknown")
                
                if answer is None:
                    st.write("---")
                    st.write("Raw Result from Agent:", result)
                    answer = "No se pudo extraer una respuesta del payload del agente."

                # Render answer markdown
                st.markdown(answer)
                
                # Render metadata block in UI
                if status == "passed":
                    badge_html = f'<div class="badge-passed">✓ Grounded ({int(confidence*100)}% de confianza)</div>'
                elif status == "warning":
                    badge_html = f'<div class="badge-warning">⚠ Respuesta Parcial ({int(confidence*100)}% de confianza)</div>'
                elif status == "failed":
                    badge_html = f'<div class="badge-failed">✗ No Verificado ({int(confidence*100)}% de confianza)</div>'
                else:
                    badge_html = f'<div class="badge-passed">Confianza: {int(confidence*100)}%</div>'
                    
                citations_html = ""
                if citations:
                    citations_html += '<div style="margin-top: 15px; font-weight: 600; font-size: 0.9rem; color: #8b949e;">Referencias de la Base de Conocimiento (S3 Source):</div>'
                    for idx, c in enumerate(citations):
                        decoded = urllib.parse.unquote(c)
                        citations_html += f"""
                        <div class="citation-card">
                            <div class="citation-header">📄 Referencia #{idx + 1}</div>
                            <div style="color: #e6edf3; font-weight: 400; margin-bottom: 8px;">"{decoded}"</div>
                            <div style="color: #8b949e; font-size: 0.78rem;">Ubicación: s3://rag-agents-docs/ai_act.pdf</div>
                        </div>
                        """
                
                st.markdown(f"""
                <div class="meta-box">
                    <div>{badge_html}</div>
                    {citations_html}
                </div>
                """, unsafe_allow_html=True)
                
                # Save assistant response along with metadata in history
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": answer,
                    "confidence": confidence,
                    "status": status,
                    "citations": citations,
                    "raw_payload": result  # Store the raw payload for debugging console
                })
                
            except Exception as e:
                import traceback
                st.error(f"Error al llamar al agente: {str(e)}")
                st.code(traceback.format_exc())
                
            # Trigger rerun to show metadata properly in state flow
            st.rerun()

# Expandable developer drawer for telemetry/raw JSON payload at the bottom
if st.session_state.messages:
    last_assistant_msg = next((m for m in reversed(st.session_state.messages) if m["role"] == "assistant"), None)
    if last_assistant_msg and "raw_payload" in last_assistant_msg:
        st.divider()
        with st.expander("🛠️ Panel de Desarrollador — Inspección de Datos en Crudo (RAG)"):
            st.json(last_assistant_msg["raw_payload"])
