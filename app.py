import streamlit as st
import os
import requests
import json
import sqlite3
import socket
import time  # <-- TA LINIJKA NAPRAWIA BŁĄD ZESKANOWANY NA OBRAZKU
from datetime import datetime
from io import BytesIO
import plotly.express as px

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# --- PATH & PARAMETERS CONFIGURATION ---
UPLOAD_DIR = "/u01/log"
DB_PATH = "/u01/scans.db"
OLLAMA_URL = "http://192.168.95.219:11434/api/chat"
#MODEL_NAME = "qwen2.5-coder:7b-instruct"

# Ensure the upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- DATABASE INITIATION ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            folder_scanned TEXT,
            total_errors INTEGER,
            critical_count INTEGER,
            low_priority_count INTEGER,
            raw_results TEXT
        )
    ''')
    # NOWOŚĆ: Bezpieczne dodanie kolumny na nazwę modelu, jeśli baza już istnieje
    try:
        cursor.execute("ALTER TABLE scans ADD COLUMN model_used TEXT DEFAULT 'Unknown';")
    except sqlite3.OperationalError:
        # Kolumna już istnieje, ignorujemy błąd
        pass
    conn.commit()
    conn.close()

init_db()

def get_installed_ollama_models():
    """Pobiera listę zainstalowanych modeli bezpośrednio z lokalnego API Ollama."""
    try:
        # Odpytujemy lokalne API Ollamy o listę modeli
        response = requests.get("http://192.168.95.219:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models_data = response.json().get("models", [])
            # Wyciągamy same nazwy (tagi) modeli
            return [model["name"] for model in models_data]
    except:
        pass
    # Zwracamy listę domyślną jako fallback, jeśli Ollama nie odpowiada
    return ["qwen2.5-coder:7b-instruct", "deepseek-r1:8b", "llama3.1:latest"]

# --- HELPER FUNCTIONS ---
def ask_ollama_expert_analysis(log_line):
    """Sends a log line to Ollama using the dynamically selected model from the session state."""

    # --- DYNAMICZNE POBIERANIE MODELU Z SESJI ---
    # Jeśli użytkownik kliknął coś w selectboxie, wartość ta jest w st.session_state.selected_model
    if "selected_model" in st.session_state:
        current_model = st.session_state.selected_model
    else:
        current_model = MODEL_NAME # Fallback do zmiennej globalnej

    prompt = (
        "You are an expert Linux system administrator. Analyze the following log line.\n"
        "1. Evaluate if the problem is critical and requires immediate attention (true/false).\n"
        "2. Describe what happened in the 'reason' field IN ENGLISH.\n"
        "3. List 2-3 specific root causes for this failure in the 'possible_causes' array IN ENGLISH.\n\n"
        f"Log line: {log_line}"
    )

    payload = {
        "model": current_model,  # <-- UŻYWAMY MODELU WYBRANEGO PRZEZ UŻYTKOWNIKA
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "is_critical": {"type": "boolean"},
                "reason": {"type": "string"},
                "possible_causes": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["is_critical", "reason", "possible_causes"]
        }
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=360)
        if response.status_code == 200:
            return json.loads(response.json()["message"]["content"])
    except:
        return None

def save_scan_to_db(folder, total, critical, low_priority, alerts, model_name):
    """Zapisuje wyniki skanowania wraz z nazwą używanego modelu."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO scans (date, folder_scanned, total_errors, critical_count, low_priority_count, raw_results, model_used)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (now, folder, total, critical, low_priority, json.dumps(alerts), model_name))
    conn.commit()
    conn.close()

def get_scan_history():
    """Pobiera historię skanowań, w tym nazwę użytego modelu."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, date, total_errors, critical_count, folder_scanned, model_used FROM scans ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def load_scan_from_db(scan_id):
    """Ładuje archiwalne skanowanie wraz z informacją o modelu."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT total_errors, critical_count, low_priority_count, raw_results, folder_scanned, model_used FROM scans WHERE id = ?', (scan_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "total_unique_errors": row[0],
            "critical_alerts": json.loads(row[3]),
            "low_priority_count": row[2],
            "folder": row[4],
            "model_used": row[5] # Pobieramy nazwę modelu z bazy
        }
    return None

def generate_pdf_report(critical_alerts, total_errors, low_priority, folder, detected_host, model_used):
    """Generates a clean PDF diagnostic report in English including the AI model used."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title="AI Log Analysis Report")
    story = []
    styles = getSampleStyleSheet()

    main_font = 'Helvetica'
    bold_font = 'Helvetica-Bold'

    title_style = ParagraphStyle('PT', fontName=bold_font, fontSize=22, leading=26, textColor=colors.HexColor('#d9534f'), spaceAfter=12)
    body_style = ParagraphStyle('PBody', fontName=main_font, fontSize=10, leading=14, textColor=colors.HexColor('#444444'))
    h2_style = ParagraphStyle('PH2', fontName=bold_font, fontSize=13, leading=16, textColor=colors.HexColor('#333333'), spaceBefore=10, spaceAfter=4)
    log_style = ParagraphStyle('PLog', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#222222'), backColor=colors.HexColor('#f4f4f4'), borderColor=colors.HexColor('#d9534f'), borderWidth=0.5, borderPadding=6, spaceBefore=4, spaceAfter=8)

    story.append(Paragraph(f"🛡️ AI LOG ANALYSIS REPORT - {detected_host.upper()}", title_style))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body_style))
    story.append(Paragraph(f"AI Model Used: <b>{model_used}</b>", body_style)) # Dodano informację do nagłówka PDF
    story.append(Paragraph(f"Scanned directory: {folder}", body_style))
    story.append(Spacer(1, 15))


    data = [
        [Paragraph("<b>Metric</b>", body_style), Paragraph("<b>Value</b>", body_style)],
        [Paragraph("Total text errors found", body_style), Paragraph(str(total_errors), body_style)],
        [Paragraph("CRITICAL Alerts (AI)", body_style), Paragraph(str(len(critical_alerts)), body_style)],
        [Paragraph("Ignored (Low priority)", body_style), Paragraph(str(low_priority), body_style)]
    ]
    t = Table(data, colWidths=[200, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (1,0), colors.HexColor('#eeeeee')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('PADDING', (0,0), (-1,-1), 6)
    ]))
    story.append(t)
    story.append(Spacer(1, 15))

    story.append(Paragraph("🛑 Detected Critical Incidents:", h2_style))
    story.append(Spacer(1, 5))

    for idx, alert in enumerate(critical_alerts, 1):
        story.append(Paragraph(f"<b>Incident #{idx} | Source File: {alert['file']}</b>", h2_style))
        story.append(Paragraph(f"<b>Description:</b> {alert['reason']}", body_style))

        causes_html = "<b>Potential Causes:</b><br/>"
        for c_idx, cause in enumerate(alert['causes'], 1):
            causes_html += f"{c_idx}. {cause}<br/>"
        story.append(Paragraph(causes_html, body_style))
        story.append(Paragraph(f"<b>Log Entry:</b><br/>{alert['log']}", log_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# --- WEB INTERFACE (STREAMLIT APP) ---
st.set_page_config(page_title="AI Log Analyzer Portal", page_icon="🛡️", layout="wide")
st.title("🛡️ AI Log Analyzer & Management Portal")

# --- PANEL BOCZNY INTERFEJSU (Z WYBOREM MODELU) ---
with st.sidebar:
    st.header("📥 Method 1: Upload Log Files")
    uploaded_files = st.file_uploader("Drop log files here:", accept_multiple_files=True)

    if uploaded_files:
        for uploaded_file in uploaded_files:
            file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
        st.success(f"Successfully saved {len(uploaded_files)} files to `/u01/log`!")

    st.header("📋 Method 2: Paste Log from Clipboard")
    clipboard_input = st.text_area(
        label="Paste raw log lines or terminal dump here:",
        placeholder="Paste your logs here...",
        height=150
    )

    if clipboard_input.strip():
        clipboard_file_path = os.path.join(UPLOAD_DIR, "clipboard_log.txt")
        with open(clipboard_file_path, "w", encoding="utf-8") as f:
            f.write(clipboard_input)
        st.info("📝 Clipboard content detected. It will be scanned as 'clipboard_log.txt'.")

    st.header("🗑️ Log Management")
    if st.button("🚨 Clear All Uploaded Logs", type="secondary"):
        try:
            files_to_delete = os.listdir(UPLOAD_DIR)
            if not files_to_delete:
                st.warning("The log directory is already empty!")
            else:
                for file_name in files_to_delete:
                    file_path = os.path.join(UPLOAD_DIR, file_name)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                st.success("💥 Successfully deleted all files from `/u01/log`!")
                time.sleep(1)
                st.rerun()
        except Exception as e:
            st.error(f"Error during file deletion: {e}")

    # --- NOWOŚĆ: DYNAMICZNY WYBÓR MODELU Z OLLAMY ---
    st.header("🤖 AI Model Selection")
    installed_models = get_installed_ollama_models()

    # Przechowujemy wybrany model w stanie sesji
    if "selected_model" not in st.session_state:
        st.session_state.selected_model = installed_models[0] if installed_models else "qwen2.5-coder:7b-instruct"

    # Pole wyboru w GUI
    MODEL_NAME = st.selectbox(
        "Choose LLM for log analysis:",
        options=installed_models,
        index=installed_models.index(st.session_state.selected_model) if st.session_state.selected_model in installed_models else 0
    )
    st.session_state.selected_model = MODEL_NAME # Aktualizacja w pamięci programu

    # Sekcja uruchamiania analizy
    st.header("⚙️ New AI Analysis")
    st.markdown("Analyze all log assets residing inside `/u01/log` (including clipboard content).")

    folder_to_scan = UPLOAD_DIR
    run_button = st.button("🚀 Uruchom analizę AI", type="primary")

    st.header("📜 Scan History")
    history = get_scan_history()
    if history:
        options = {}
        for h in history:
            # FIX: Dodaliśmy szóstą zmienną 'saved_model' na końcu, aby przyjąć 6 wartości z bazy danych
            scan_id, scan_date, total_err, crit_count, saved_host, saved_model = h

            # Budujemy czytelny format wpisu na listę rozwijaną
            label = f"🖥️ {saved_host} | 📅 {scan_date} | 🚨 Alerts: {crit_count} | 🤖 {saved_model}"
            options[label] = scan_id

        selected_scan_str = st.selectbox("Select historical scan:", list(options.keys()))
        load_button = st.button("📂 Załaduj z bazy danych")
    else:
        st.info("No scans found in database.")

# SESSION STATES
if "display_results" not in st.session_state:
    st.session_state.display_results = None

# LOGIC: RECOVER FROM HISTORY
if 'load_button' in locals() and load_button:
    scan_id = options[selected_scan_str]
    loaded_data = load_scan_from_db(scan_id)
    if loaded_data:
        st.session_state.display_results = loaded_data
        st.success("Historical report recovered successfully from SQLite database!")

# LOGIC: EXECUTE NEW SCAN
if run_button:
    if not os.path.exists(folder_to_scan):
        st.error(f"Directory '{folder_to_scan}' does not exist!")
    else:
        st.info("📂 Initializing log aggregation process with aggressive pattern deduplication...")
        keywords = ["error", "crit", "fail", "exception", "fatal"]
        logs_to_analyze = {}
        total_unique_errors = 0

        import re # Importujemy moduł wyrażeń regularnych do czyszczenia zmiennych

        # 1. Agregacja plików z usuwaniem liczb i zmiennych dynamicznych (czasów, ID)
        for root, dirs, files in os.walk(folder_to_scan):
            for file in files:
                if file.endswith((".log", ".txt")) or "." not in file:
                    full_file_path = os.path.join(root, file)
                    if file.endswith(".gz") or "scans.db" in file:
                        continue
                    try:
                        matched_lines = {} # Słownik eliminujący powtórzenia wzorców
                        with open(full_file_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                line_clean = line.strip()
                                if any(k in line_clean.lower() for k in keywords):

                                    # --- AGRESYWNA NORMALIZACJA WZORCA BŁĘDU ---
                                    # Usunięcie daty i czasu z początku standardowego logu (pierwsze 15 znaków)
                                    pattern_key = line_clean[15:] if len(line_clean) > 15 else line_clean

                                    # Usunięcie wszelkich liczb zmiennoprzecinkowych (np. duration_seconds=0.000291312 zamienia na duration_seconds=)
                                    pattern_key = re.sub(r'\d+\.\d+', '', pattern_key)

                                    # Usunięcie wszelkich liczb całkowitych (np. ID procesów node_exporter[1743] zamienia na node_exporter[])
                                    pattern_key = re.sub(r'\d+', '', pattern_key)

                                    # Usunięcie wielokrotnych spacji powstałych po czyszczeniu liczb
                                    pattern_key = re.sub(r'\s+', ' ', pattern_key).strip()

                                    # Jeśli taki oczyszczony wzorzec błędu jeszcze nie istnieje, zapisujemy go
                                    if pattern_key not in matched_lines:
                                        matched_lines[pattern_key] = line_clean

                        if matched_lines:
                            logs_to_analyze[full_file_path] = set(matched_lines.values())
                            total_unique_errors += len(matched_lines)
                    except:
                        continue

        if total_unique_errors == 0:
            st.success("🎉 No active system errors or critical events found in the targeted directory.")
            st.session_state.display_results = None
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            critical_alerts = []
            low_priority_count = 0
            current_index = 0

            st.header("⏳ Live Incidents Streaming Feed")
            live_placeholder = st.empty()
            live_html_content = ""

            current_active_model = st.session_state.get("selected_model", MODEL_NAME)

            # 2. Potok analizy AI w pętli dla wyizolowanych unikalnych błędów
            for file_path, lines in logs_to_analyze.items():
                for line in lines:
                    current_index += 1
                    status_text.text(f"Analyzing log item {current_index} of {total_unique_errors} via {current_active_model}...")
                    progress_bar.progress(current_index / total_unique_errors)

                    analysis = ask_ollama_expert_analysis(line)
                    if analysis:
                        if analysis.get("is_critical"):

                            # Dynamiczne wyciąganie prawidłowej nazwy hosta z linii logu
                            log_parts = line.split()
                            if len(log_parts) >= 4:
                                extracted_hostname = log_parts[3].replace(':', '')
                            else:
                                extracted_hostname = socket.gethostname()

                            alert_item = {
                                "hostname": extracted_hostname,
                                "file": os.path.basename(file_path),
                                "full_path": file_path,
                                "reason": analysis.get("reason"),
                                "causes": analysis.get("possible_causes", []),
                                "log": line
                            }
                            critical_alerts.append(alert_item)

                            # Generowanie i strumieniowanie kodu HTML dla linków rozwijanych na żywo
                            causes_list = "".join([f"<li>{c}</li>" for c in alert_item['causes']])
                            live_html_content += f"""
                            <details style="background-color: #1e1e1e; padding: 10px; border-radius: 5px; margin-bottom: 8px; border-left: 5px solid #d9534f; color: #ffffff;">
                                <summary style="cursor: pointer; font-weight: bold; color: #f0ad4e;">
                                    🚨 Host: [{alert_item['hostname']}] | CRITICAL: [{alert_item['file']}] - {alert_item['reason']}
                                </summary>
                                <div style="padding-top: 10px; font-size: 14px;">
                                    <p><strong>Path:</strong> <code>{alert_item['full_path']}</code></p>
                                    <p><strong>Potential Root Causes:</strong></p>
                                    <ul>{causes_list}</ul>
                                    <p><strong>Log Raw Entry:</strong></p>
                                    <pre style="background-color: #2d2d2d; padding: 8px; border-radius: 3px; font-family: monospace; white-space: pre-wrap; color: #ff5555;">{alert_item['log']}</pre>
                                </div>
                            </details>
                            """
                            live_placeholder.markdown(live_html_content, unsafe_allow_html=True)
                        else:
                            low_priority_count += 1

            status_text.empty()
            progress_bar.empty()

            # --- NOWOŚĆ: BEZBŁĘDNE WYCIĄGANIE HOSTA DLA ZAPISU BAZY ---
            # Sprawdzamy pierwszy wykryty alert. Jeśli istnieje, bierzemy z niego host.
            if critical_alerts:
                final_hostname = critical_alerts[0]["hostname"]
            else:
                # Jeśli brak alertów krytycznych, próbujemy wyciągnąć host z losowej linii z logs_to_analyze
                try:
                    sample_line = list(list(logs_to_analyze.values())[0])[0]
                    log_parts = sample_line.split()
                    final_hostname = log_parts[3].replace(':', '') if len(log_parts) >= 4 else socket.gethostname()
                except:
                    final_hostname = socket.gethostname()

            # Pobieramy model, który był fizycznie zaznaczony podczas tego skanu
            current_active_model = st.session_state.get("selected_model", MODEL_NAME)

            # Zapis do bazy danych SQLite - przekazujemy final_hostname jako klucz identyfikacyjny!
            save_scan_to_db(final_hostname, total_unique_errors, len(critical_alerts), low_priority_count, critical_alerts, current_active_model)

            # Zapisanie wyników do stanu sesji aplikacji
            st.session_state.display_results = {
                "total_unique_errors": total_unique_errors,
                "critical_alerts": critical_alerts,
                "low_priority_count": low_priority_count,
                "folder": final_hostname,
                "model_used": current_active_model
            }
            st.success("Analysis completed successfully and stored in the database!")
            st.button("🔄 Refresh View & Generate PDF Report")


# --- VISUAL PRESENTATION ---
if st.session_state.display_results:
    res = st.session_state.display_results

    st.header(f"📊 Analysis Metrics for: {res['folder']}")

    # 1. Dynamiczne wyciąganie hosta do nazwy pliku
    if res["critical_alerts"]:
        pdf_host = res["critical_alerts"][0].get("hostname", "Unknown")
    else:
        pdf_host = res.get("folder", "Unknown")

    # 2. NOWOŚĆ: Prawidłowe wyciąganie nazwy użytego modelu z sesji raportu
    model_name_report = res.get("model_used", "Unknown_Model")

    # Wyświetlamy czytelną informację o modelu nad metrykami w portalu
    st.markdown(f"🤖 **AI Model Used for this scan:** `{model_name_report}`")

    file_date = datetime.now().strftime('%Y%m%d_%H%M%S')
    custom_filename = f"{pdf_host}_{file_date}.pdf"

    # FIX: Przekazujemy dokładnie 6 wymaganych parametrów (w tym model_name_report)
    pdf_data = generate_pdf_report(
        res["critical_alerts"],
        res["total_unique_errors"],
        res["low_priority_count"],
        res["folder"],
        pdf_host,
        model_name_report  # <-- Ten argument naprawia błąd TypeError!
    )

    st.download_button(
        label="📥 Download Diagnostic Report (PDF)",
        data=pdf_data,
        file_name=custom_filename,
        mime="application/pdf"
    )
    st.markdown("<br/>", unsafe_allow_html=True)

    # Pola metryczne podsumowania w GUI
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Log Lines Scanned", res["total_unique_errors"])
    col2.metric("🚨 Critical Incidents (AI)", len(res["critical_alerts"]), delta_color="inverse")
    col3.metric("ℹ️ Low Priority (Ignored)", res["low_priority_count"])

    # Generowanie wykresu kołowego Plotly Express
    fig_data = {"Status": ["Critical Alerts", "Low Priority"], "Count": [len(res["critical_alerts"]), res["low_priority_count"]]}
    fig = px.pie(fig_data, values="Count", names="Status", title="AI Incident Severity Distribution", color_discrete_sequence=['#d9534f', '#5cb85c'])
    st.plotly_chart(fig, width='stretch')

    # Sekcja stałych akordeonów podsumowania raportu
    st.header("🛑 Detected Critical Incidents Summary")
    if not res["critical_alerts"]:
        st.info("No critical incidents recorded.")
    else:
        for idx, alert in enumerate(res["critical_alerts"], 1):
            with st.expander(f"💻 Host: {alert.get('hostname', 'Unknown')} | 📅 {datetime.now().strftime('%Y-%m-%d')} | ⚠️ {alert['reason']}"):
                st.markdown(f"**Source File:** `{alert['file']}` (`{alert['full_path']}`)")
                st.markdown(f"##### 💡 Potential Root Causes:")
                for c_idx, cause in enumerate(alert['causes'], 1):
                    st.markdown(f"{c_idx}. {cause}")
                st.markdown("**📄 Original Log Entry:**")
                st.code(alert['log'], language="bash")

