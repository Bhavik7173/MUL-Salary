
import streamlit as st
import pandas as pd
import os
import io
import json
import matplotlib.pyplot as plt
from sqlalchemy import create_engine
from datetime import datetime, date
from dateutil import parser
from datetime import time as dtime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib import pagesizes
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from datetime import datetime
import hashlib
# from pdf_utils import generate_payslip_pdf_bytes

from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from io import BytesIO
from datetime import datetime

# ---------------- CONFIG ----------------
CSV_PATH = "work_log.csv"
SQLITE_PATH = "work_log.db"
TABLE_NAME = "daily_records"
SETTINGS_PATH = "mul_settings.json"
LOGO_PATH = "logo.png"

# HOURLY_RATE = 14.53
TAX_RATE = 0.2764
CONTRACT_HOURS = 151.67
BONUS_AMOUNT = 6.0
# INITIAL_AZK=65.77

# default sender fallback (prefer user to set via Settings tab)
DEFAULT_SENDER_EMAIL = ""
DEFAULT_SENDER_PASSWORD = ""

st.set_page_config(page_title="MUL Salary Tracker", layout="wide")

# ---------------- UTILITIES ----------------
def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def save_settings(obj):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

settings = load_settings()

def ensure_storage():
    engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)

    with engine.connect() as conn:

        # USERS TABLE
        if not engine.dialect.has_table(conn, "users"):
            users_df = pd.DataFrame(columns=["id", "email", "password_hash"])
            users_df.to_sql("users", conn, index=False, if_exists="replace")

        # DAILY RECORDS TABLE
        if not engine.dialect.has_table(conn, TABLE_NAME):
            df = pd.DataFrame(columns=[
                "id",
                "user_id",   # ✅ NEW COLUMN
                "date","day","public_holiday","start_time","end_time","break_hours",
                "working_hours","bonus","travel_eur","gross_pay","tax","net_pay","gross_hourly",
                "source","notes"
            ])
            df.to_sql(TABLE_NAME, conn, index=False, if_exists="replace")

    return engine


def parse_time(t):
    if pd.isna(t) or t == "":
        return None
    if isinstance(t, dtime):
        return t
    try:
        dt = parser.parse(str(t)).time()
        return dt
    except Exception:
        return None

def compute_hours(row):
    if str(row.get("public_holiday")).strip().upper() in ["Y","YES","TRUE","1"]:
        wh = 7.0
    else:
        stime = parse_time(row.get("start_time"))
        etime = parse_time(row.get("end_time"))
        brk = row.get("break_hours") if row.get("break_hours") not in [None,""] else 0.0
        try:
            brk = float(brk)
        except Exception:
            brk = 0.0
        if stime and etime:
            dt0 = datetime(2000,1,1, stime.hour, stime.minute, stime.second)
            dt1 = datetime(2000,1,1, etime.hour, etime.minute, etime.second)
            diff = (dt1 - dt0).total_seconds() / 3600.0
            if diff < 0:
                diff += 24.0
            wh = max(0.0, diff - brk)
        else:
            wh = 0.0
    return round(wh, 3)

def compute_row_financials(working_hours, travel):
    bonus = BONUS_AMOUNT if (working_hours >= 6.0) else 0.0
    gross_hourly = working_hours * HOURLY_RATE
    travel_val = travel if (travel is not None and not pd.isna(travel)) else 0.0
    gross = gross_hourly + bonus + float(travel_val)
    tax = gross_hourly * TAX_RATE
    net = gross - tax
    return bonus, round(gross,2), round(tax,2), round(net,2), round(gross_hourly,2)

def load_data(engine=None):
    try:
        engine = engine or create_engine(f"sqlite:///{SQLITE_PATH}")
        with engine.connect() as conn:
            df = pd.read_sql_table(TABLE_NAME, conn)
    except Exception:
        df = pd.read_csv(CSV_PATH)
    if "date" in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
    # coerce numeric
    for col in ["working_hours","bonus","travel_eur","gross_pay","tax","net_pay","gross_hourly","break_hours"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    return df

def ensure_id_column(df):
    if "id" not in df.columns:
        df = df.copy()
        df.insert(0, "id", range(1, len(df) + 1))
    df["id"] = df["id"].astype(int)
    return df

def save_to_storage(df, engine=None):
    df.to_csv(CSV_PATH, index=False)
    engine = engine or create_engine(f"sqlite:///{SQLITE_PATH}")
    with engine.connect() as conn:
        df.to_sql(TABLE_NAME, conn, index=False, if_exists="replace")

def calculate_azk_bank(df, target_year, target_month, initial_azk=0.0):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    azk_bank = initial_azk
    month_change = 0.0

    for (y, m), g in df.groupby([df["date"].dt.year, df["date"].dt.month]):
        worked = g["working_hours"].sum()
        diff = worked - CONTRACT_HOURS

        azk_bank += diff

        if y == target_year and m == target_month:
            month_change = diff
            break   # ⛔ stop here, do NOT continue

    return round(azk_bank, 2), round(month_change, 2)

# ---------------- EMAIL (HTML + attachment) ----------------
def build_email_html(summary, company_name="MUL Company"):
    # Simple branded HTML template - inline CSS
    html = f"""
    <html>
    <body style="font-family: Arial, Helvetica, sans-serif; color:#222;">
      <div style="max-width:1000px; margin:auto; border:1.2px solid #e0e0e0;">
        <div style="background:#0b5ed7; padding:18px; color:white;">
          <h2 style="margin:0">{company_name} - Monthly Summary</h2>
        </div>
        <div style="padding:18px;">
          <pre style="font-family:inherit; white-space:pre-wrap;">{summary}</pre>
        </div>
        <div style="padding:14px; background:#f6f6f6; color:#444; font-size:13px;">
          This is an automated payslip summary generated by MUL Salary Tracker.
        </div>
      </div>
    </body>
    </html>
    """
    return html

def send_email_with_attachment(to_email, subject, html_body, attachment_bytes=None, attachment_name="payslip.pdf",
                               sender_email=None, sender_password=None):
    sender_email = sender_email or st.secrets["SENDER_EMAIL"]
    sender_password = sender_password or st.secrets["SENDER_PASSWORD"]

    if not sender_email or not sender_password:
        return False, "Sender credentials not configured. Set them in Settings."

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = subject

    # attach HTML
    msg.attach(MIMEText(html_body, "html"))

    # attach binary (PDF)
    if attachment_bytes:
        part = MIMEApplication(attachment_bytes.getvalue(), _subtype="pdf")
        part.add_header('Content-Disposition', 'attachment', filename=attachment_name)
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

# ---------------- PDF Payslip generation (Option C: Branded) ----------------
def generate_payslip_pdf(df_month, year, month, logo_path=None, company_name="MUL Company"):
    """
    Generate branded monthly payslip PDF from DataFrame.
    Returns: BytesIO
    """

    from io import BytesIO
    buf = BytesIO()

    doc = SimpleDocTemplate(buf, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # ===== Title =====
    title = Paragraph(f"<b>{company_name} - Payslip ({year}-{month:02d})</b>", styles["Heading1"])
    elements.append(title)
    elements.append(Spacer(1, 12))

    # ===== Monthly Calculations =====
    total_hours = df_month['working_hours'].sum()
    payable_hours = min(total_hours, CONTRACT_HOURS)

    bonus_total = df_month.get('bonus', pd.Series([0])).sum()
    travel_total = df_month.get('travel_eur', pd.Series([0])).sum()

    gross_salary = payable_hours * HOURLY_RATE
    tax_amount = gross_salary * TAX_RATE
    net_salary = gross_salary - tax_amount + bonus_total + travel_total

    summary_data = [
        ["Total Worked Hours", f"{total_hours:.2f} h"],
        ["Payable Hours", f"{payable_hours:.2f} h"],
        ["Gross Salary", f"€ {gross_salary:.2f}"],
        ["Tax", f"€ {tax_amount:.2f}"],
        ["Bonus", f"€ {bonus_total:.2f}"],
        ["Travel", f"€ {travel_total:.2f}"],
        ["Net Salary", f"€ {net_salary:.2f}"],
    ]

    summary_table = Table(summary_data, colWidths=[200, 200])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 20))

    # ===== Daily Breakdown Table =====
    table_data = [["Date", "Start", "End", "Hours", "Gross", "Tax"]]

    for _, r in df_month.iterrows():
        table_data.append([
            str(r.get("date", "")),
            str(r.get("start_time", "")),
            str(r.get("end_time", "")),
            f"{r.get('working_hours', 0):.2f}",
            f"€{r.get('gross_hourly', 0):.2f}",
            f"€{r.get('tax', 0):.2f}",
        ])

    daily_table = Table(table_data, repeatRows=1)
    daily_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    elements.append(daily_table)

    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Generated by MUL Salary Tracker", styles["Normal"]))

    doc.build(elements)

    buf.seek(0)
    return buf


# def generate_payslip_pdf_bytes(df_month, year, month, logo_path=LOGO_PATH, company_name="MUL Company"):
#     """
#     Generates a branded payslip PDF for month (DataFrame df_month).
#     Returns BytesIO containing PDF.
#     """
#     from io import BytesIO
#     buf = BytesIO()
#     c = canvas.Canvas(buf, pagesize=A4)
#     width, height = A4

#     # header background
#     c.setFillColorRGB(11/255, 94/255, 215/255)  # deep blue
#     c.rect(0, height - 70, width, 70, fill=1, stroke=0)

#     # logo if available (fit into left of header)
#     if os.path.exists(logo_path):
#         try:
#             c.drawImage(logo_path, 20, height - 60, width=80, height=40, preserveAspectRatio=True, mask='auto')
#         except Exception:
#             pass

#     # header text
#     c.setFillColorRGB(1,1,1)
#     c.setFont("Helvetica-Bold", 18)
#     c.drawString(110, height - 45, f"{company_name} - Payslip")
#     c.setFont("Helvetica", 10)
#     c.drawString(110, height - 60, f"Month: {year}-{month:02d}")

#     # summary box
#     left_x = 30
#     y = height - 110
#     c.setFillColorRGB(0.96,0.96,0.96)
#     c.roundRect(left_x, y - 135, width - 60, 120, 8, fill=1)
#     c.setFillColorRGB(0,0,0)
#     c.setFont("Helvetica-Bold", 12)
#     c.drawString(left_x + 8, y - 10, "Monthly Summary")
#     c.setFont("Helvetica", 10)

#     total_hours = df_month['working_hours'].sum()
#     payable_hours = min(total_hours, CONTRACT_HOURS)
#     bonus_total = df_month['bonus'].sum() if 'bonus' in df_month.columns else 0.0
#     travel_total = df_month['travel_eur'].sum() if 'travel_eur' in df_month.columns else 0.0
#     salario = round(payable_hours * HOURLY_RATE,2)
#     tax_total = round(payable_hours * HOURLY_RATE * TAX_RATE,2)
#     net_total = round(salario - tax_total + bonus_total + travel_total,2)

#     lines = [
#         f"Total worked hours: {total_hours:.2f} h",
#         f"Payable hours: {payable_hours:.2f} h",
#         f"Gross (hourly pay): €{salario:.2f}",
#         f"Tax: €{tax_total:.2f}",
#         f"Bonus total: €{bonus_total:.2f}",
#         f"Travel total: €{travel_total:.2f}",
#         f"Net pay: €{net_total:.2f}",
#     ]
#     tx = left_x + 12
#     ty = y - 30
#     for ln in lines:
#         c.drawString(tx, ty, ln)
#         ty -= 16

#     # Table of daily entries (compact)
#     table_y = y - 160
#     c.setFont("Helvetica-Bold", 10)
#     c.drawString(left_x, table_y + 10, "Date")
#     c.drawString(left_x + 80, table_y + 10, "Hours")
#     c.drawString(left_x + 140, table_y + 10, "Gross")
#     c.drawString(left_x + 220, table_y + 10, "Tax")
#     c.setFont("Helvetica", 9)
#     cur_y = table_y - 6
#     for _, r in df_month.iterrows():
#         if cur_y < 60:
#             c.showPage()
#             cur_y = height - 60
#         c.drawString(left_x, cur_y, str(r['date']))
#         c.drawRightString(left_x + 110, cur_y, f"{r.get('working_hours',0):.2f}")
#         c.drawRightString(left_x + 200, cur_y, f"€{r.get('gross_hourly',0):.2f}")
#         c.drawRightString(left_x + 260, cur_y, f"€{r.get('tax',0):.2f}")
#         cur_y -= 14

#     # footer
#     c.setFillColorRGB(0.2,0.2,0.2)
#     c.setFont("Helvetica-Oblique", 8)
#     c.drawString(30, 30, "Generated by MUL Salary Tracker")

#     c.showPage()
#     c.save()
#     buf.seek(0)
#     return buf

# ---------------- AUTO MONTHLY SENDER ----------------
def try_auto_send_on_start():
    """
    If auto_send enabled in settings, and today is the configured day (default 1),
    and last_auto_sent is not this month, then attempt to send.
    """
    cfg = settings.get("auto_email", {})
    enabled = cfg.get("enabled", False)
    send_day = cfg.get("day", 1)
    recipient = cfg.get("recipient", "")
    if not enabled or not recipient:
        return
    today = date.today()
    if today.day != send_day:
        return
    last_sent = cfg.get("last_sent", "")
    last_sent_ident = f"{today.year}-{today.month:02d}"
    if last_sent == last_sent_ident:
        return  # already sent this month
    # prepare and send using existing logic (but do not forcibly attach if no data)
    df = load_data()
    if df.empty:
        return
    df['date'] = pd.to_datetime(df['date']).dt.date
    # build this month's summary
    df_dates = pd.to_datetime(df['date'])
    df_m = df[(df_dates.dt.year == today.year) & (df_dates.dt.month == today.month)]
    if df_m.empty:
        return
    total_hours = df_m['working_hours'].sum()
    payable_hours = min(total_hours, CONTRACT_HOURS)
    bonus_total = df_m['bonus'].sum() if 'bonus' in df_m.columns else 0.0
    travel_total = df_m['travel_eur'].sum() if 'travel_eur' in df_m.columns else 0.0
    salario = round(payable_hours * HOURLY_RATE,2)
    tax_total = round(payable_hours * HOURLY_RATE * TAX_RATE,2)
    net_total = round(salario - tax_total + bonus_total + travel_total,2)
    summary = (
        f"MUL Company - Monthly Summary ({today.year}-{today.month:02d})\n"
        f"Total worked hours: {total_hours:.2f} h\n"
        f"Payable hours: {payable_hours:.2f} h\n"
        f"Gross (hourly pay): €{salario:.2f}\n"
        f"Tax: €{tax_total:.2f}\n"
        f"Bonus total: €{bonus_total:.2f}\n"
        f"Travel total: €{travel_total:.2f}\n"
        f"Net pay: €{net_total:.2f}"
    )
    html = build_email_html(summary)
    # PDF
    pdf_bytes = generate_payslip_pdf(df_m, today.year, today.month, logo_path=LOGO_PATH)
    ok, err = send_email_with_attachment(recipient, f"MUL Salary Summary {today.year}-{today.month:02d}", html,
                                        attachment_bytes=pdf_bytes)
    if ok:
        settings.setdefault("auto_email", {})["last_sent"] = f"{today.year}-{today.month:02d}"
        save_settings(settings)

# Try auto-send on app start (opt-in only)
try_auto_send_on_start()

# ---------------- AUTH SYSTEM ----------------

if "user" not in st.session_state:
    st.session_state.user = None

def login_page():
    st.title("MUL Salary Tracker - Login")

    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Login"):
            df_users = pd.read_sql_table("users", engine)

            user = df_users[
                (df_users["email"] == email) &
                (df_users["password_hash"] == hash_password(password))
            ]

            if not user.empty:
                st.session_state.user = user.iloc[0].to_dict()
                st.success("Login successful")
                st.rerun()
            else:
                st.error("Invalid email or password")

    with tab2:
        email = st.text_input("Register Email")
        password = st.text_input("Register Password", type="password")

        if st.button("Create Account"):
            df_users = pd.read_sql_table("users", engine)

            if email in df_users["email"].values:
                st.error("User already exists")
            else:
                new_user = {
                    "id": len(df_users) + 1,
                    "email": email,
                    "password_hash": hash_password(password)
                }
                df_users = pd.concat([df_users, pd.DataFrame([new_user])])
                df_users.to_sql("users", engine, if_exists="replace", index=False)
                st.success("Account created. Please login.")


# ---------------- INITIALIZE STORAGE ----------------
engine = ensure_storage()

if st.session_state.user is None:
    login_page()
    st.stop()


# ---------------- UI ----------------
st.title("MUL Salary Tracker (Streamlit) — Branded Payslip & Email")
st.markdown("Enter daily work data, upload Excel/CSV, or import CSV. App calculates hours, AZK, tax, bonus and summary.")

if st.sidebar.button("Logout"):
    st.session_state.user = None
    st.rerun()


# dark mode toggle (simple CSS)
if settings.get("dark_mode", False):
    dark = True
else:
    dark = False

col_dark, _ = st.columns([1, 4])
with col_dark:
    if st.checkbox("Dark mode", value=dark):
        settings["dark_mode"] = True
        save_settings(settings)
        # inject CSS for dark
        st.markdown("""
            <style>
                .stApp { background-color: #0f1720; color: #e6eef8; }
                .css-18e3th9 { background-color: #0f1720; }
                .st-bc { background-color: #0f1720; }
            </style>
        """, unsafe_allow_html=True)
    else:
        settings["dark_mode"] = False
        save_settings(settings)
        # no extra CSS (defaults)

tabs = st.tabs(["Daily Entry", "Upload Excel/CSV", "Monthly Summary", "Settings"])

# ---- DAILY ENTRY TAB ----
with tabs[0]:
    st.header("Daily Entry")
    INITIAL_AZK = st.number_input("Over Time Hours", value=0.00)
    HOURLY_RATE = st.number_input("Hourly Rate (€)", value=14.53)
    col1, col2, col3 = st.columns(3)
    with col1:
        date_input = st.date_input("Date", value=datetime.today().date())
        public_holiday = st.checkbox("Public Holiday", value=False)
        travel = st.number_input("Travel (€)", min_value=0.0, value=0.0, step=0.5)

    with col2:
        start_time = st.text_input("Start Time (HH:MM)", value="08:00")
        end_time = st.text_input("End Time (HH:MM)", value="16:30")

    with col3:
        break_hours = st.number_input("Break (hours)", min_value=0.0, value=0.5, step=0.25)
        notes = st.text_input("Notes")

    # ---- SAVE DAY ----
    if st.button("Save Day"):
        df = ensure_id_column(load_data(engine))
        df = df[df["user_id"] == st.session_state.user["id"]]
        
        if "id" not in df.columns:
            df["id"] = []

        new_id = int(df["id"].max() + 1) if not df.empty else 1

        row = {
            "user_id": st.session_state.user["id"],  # ✅ IMPORTANT
            "id": new_id,
            "date": date_input,
            "day": date_input.strftime("%A"),
            "public_holiday": "Y" if public_holiday else "N",
            "start_time": start_time,
            "end_time": end_time,
            "break_hours": break_hours,
            "travel_eur": travel,
            "notes": notes,
            "source": "manual"
        }

        wh = compute_hours(row)
        bonus, gross, tax, net, gross_hourly = compute_row_financials(wh, travel)

        row.update({
            "working_hours": wh,
            "bonus": bonus,
            "gross_pay": gross,
            "tax": tax,
            "net_pay": net,
            "gross_hourly": gross_hourly
        })

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        save_to_storage(df, engine)
        st.success(f"Saved | Hours: {wh} | Net: €{net}")

    st.markdown("---")
    st.subheader("✏️ Edit / Delete Records")

    df = ensure_id_column(load_data(engine))
    df = df[df["user_id"] == st.session_state.user["id"]]
    
    if df.empty:
        st.info("No data available.")
    else:
        # Show only current month
        df["date"] = pd.to_datetime(df["date"]).dt.date
        current_month = datetime.today().month
        df_m = df[df["date"].apply(lambda d: d.month) == current_month]

        edited_df = st.data_editor(
            df_m,
            use_container_width=True,
            disabled=[
                "working_hours", "gross_pay",
                "tax", "net_pay", "gross_hourly", "bonus"
            ],
            key="crud_editor"
        )

        col_u, col_d = st.columns(2)

        # ---- UPDATE ----
        with col_u:
            if st.button("💾 Save Changes"):
                updated_rows = []

                for _, r in edited_df.iterrows():
                    wh = compute_hours(r)
                    bonus, gross, tax, net, gross_hourly = compute_row_financials(
                        wh, r.get("travel_eur", 0)
                    )

                    r["working_hours"] = wh
                    r["bonus"] = bonus
                    r["gross_pay"] = gross
                    r["tax"] = tax
                    r["net_pay"] = net
                    r["gross_hourly"] = gross_hourly

                    updated_rows.append(r)

                updated_df = pd.DataFrame(updated_rows)

                # Merge back with full dataset
                updated_df = ensure_id_column(updated_df)
                df = ensure_id_column(df)

                df_rest = df[~df["id"].isin(updated_df["id"])]
                final_df = pd.concat([df_rest, updated_df], ignore_index=True)

                save_to_storage(final_df, engine)
                st.success("✅ Records updated successfully")

        # ---- DELETE ----
        with col_d:
            delete_id = st.number_input("Enter ID to delete", min_value=1, step=1)
            if st.button("🗑️ Delete Row"):
                df = df[df["id"] != delete_id]
                save_to_storage(df, engine)
                st.success(f"❌ Deleted record with ID {delete_id}")


# ---- UPLOAD TAB ----
with tabs[1]:
    st.header("Upload Excel or CSV")
    st.markdown("Upload a file with columns: Date, Start Time, End Time, Break (hours), Public Holiday (Y/N), Travel (€), Notes")
    
    uploaded = st.file_uploader("Upload Excel (.xlsx) or CSV", type=["xlsx","csv"])
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith(".csv"):
                uploaded_df = pd.read_csv(uploaded)
            else:
                uploaded_df = pd.read_excel(uploaded, engine="openpyxl")
            st.write("Preview (first 10 rows)")
            st.dataframe(uploaded_df.head(10))
            col_map = {}
            for c in uploaded_df.columns:
                lc = c.strip().lower()
                if "date" in lc:
                    col_map[c] = "date"
                elif "start" in lc:
                    col_map[c] = "start_time"
                elif "end" in lc:
                    col_map[c] = "end_time"
                elif "break" in lc:
                    col_map[c] = "break_hours"
                elif "public" in lc or "holiday" in lc:
                    col_map[c] = "public_holiday"
                elif "travel" in lc:
                    col_map[c] = "travel_eur"
                elif "note" in lc or "notes" in lc:
                    col_map[c] = "notes"
            uploaded_df = uploaded_df.rename(columns=col_map)
            for must in ["date"]:
                if must not in uploaded_df.columns:
                    st.error(f"Uploaded file must contain a date column. Missing: {must}")
                    uploaded_df = None
                    break
            if uploaded_df is not None:
                processed = []
                for _, r in uploaded_df.iterrows():
                    row = {}
                    row["user_id"] = st.session_state.user["id"]
                    row['date'] = pd.to_datetime(r.get('date')).date()
                    row['day'] = row['date'].strftime("%A")
                    row['public_holiday'] = ("Y" if str(r.get('public_holiday')).strip().upper() in ["Y","YES","TRUE","1"] else "N")
                    row['start_time'] = r.get('start_time', "") if 'start_time' in uploaded_df.columns else ""
                    row['end_time'] = r.get('end_time', "") if 'end_time' in uploaded_df.columns else ""
                    row['break_hours'] = r.get('break_hours', 0.0) if 'break_hours' in uploaded_df.columns else 0.0
                    row['travel_eur'] = r.get('travel_eur', 0.0) if 'travel_eur' in uploaded_df.columns else 0.0
                    row['notes'] = r.get('notes', "")
                    wh = compute_hours(row)
                    bonus, gross, tax, net, gross_hourly = compute_row_financials(wh, row['travel_eur'])
                    row.update({
                        "working_hours": wh,
                        "bonus": bonus,
                        "gross_pay": gross,
                        "tax": tax,
                        "net_pay": net,
                        "gross_hourly": gross_hourly,
                        "source": uploaded.name
                    })
                    processed.append(row)
                new_df = pd.DataFrame(processed)
                df = ensure_id_column(load_data(engine))
                df = df[df["user_id"] == st.session_state.user["id"]]                
                for d in new_df['date'].unique():
                    df = df[df['date'] != pd.to_datetime(d).date()]
                df = pd.concat([df, new_df], ignore_index=True, sort=False)
                save_to_storage(df, engine)
                st.success(f"Uploaded and merged {len(new_df)} rows. Saved to storage.")
        except Exception as e:
            st.error("Error processing uploaded file: " + str(e))

# ---- MONTHLY SUMMARY TAB ----
with tabs[2]:
    st.header("Monthly Summary & Payslip")
    df = ensure_id_column(load_data(engine))
    df = df[df["user_id"] == st.session_state.user["id"]]    
    if df.empty:
        st.info("No records yet. Add data via Daily Entry or Upload.")
    else:
        df['date'] = pd.to_datetime(df['date']).dt.date
        months = sorted(list(set([(d.year, d.month) for d in pd.to_datetime(df['date'])])), reverse=True)
        if not months:
            st.info("No date data available.")
        else:
            month_sel = st.selectbox("Select year-month", options=months, format_func=lambda ym: f"{ym[0]}-{ym[1]:02d}")
            year, month = month_sel

            df_dates = pd.to_datetime(df['date'])
            df_m = df[(df_dates.dt.year == year) & (df_dates.dt.month == month)]
            df_m = df_m.sort_values('date')
            st.subheader(f"Entries for {year}-{month:02d}")

            df_m = ensure_id_column(df_m)

            # Add delete flag column
            if "delete" not in df_m.columns:
                df_m["delete"] = False

            edited_df = st.data_editor(
                df_m,
                use_container_width=True,
                disabled=[
                    "id", "working_hours", "gross_pay",
                    "tax", "net_pay", "gross_hourly", "bonus"
                ],
                column_config={
                    "delete": st.column_config.CheckboxColumn(
                        "🗑️ Delete",
                        help="Select row to delete"
                    )
                },
                key="monthly_crud"
            )

            col_u, col_d = st.columns(2)

            # ---- UPDATE ----
            with col_u:
                if st.button("💾 Save Monthly Changes"):
                    updated_rows = []

                    for _, r in edited_df.iterrows():
                        wh = compute_hours(r)
                        bonus, gross, tax, net, gross_hourly = compute_row_financials(
                            wh, r.get("travel_eur", 0)
                        )

                        r["working_hours"] = wh
                        r["bonus"] = bonus
                        r["gross_pay"] = gross
                        r["tax"] = tax
                        r["net_pay"] = net
                        r["gross_hourly"] = gross_hourly

                        updated_rows.append(r)

                    updated_df = pd.DataFrame(updated_rows)

                    df_rest = df[~df["id"].isin(updated_df["id"])]
                    final_df = pd.concat([df_rest, updated_df.drop(columns=["delete"])], ignore_index=True)

                    save_to_storage(final_df, engine)
                    st.success("✅ Monthly records updated")

            # ---- DELETE ----
            with col_d:
                if st.button("🗑️ Delete Selected Rows"):
                    delete_ids = edited_df.loc[edited_df["delete"] == True, "id"].tolist()

                    if not delete_ids:
                        st.warning("No rows selected for deletion")
                    else:
                        df = df[~df["id"].isin(delete_ids)]
                        save_to_storage(df, engine)
                        st.success(f"❌ Deleted {len(delete_ids)} row(s)")
            # st.dataframe(df_m)
            azk_bank, azk_change = calculate_azk_bank(df, year, month, INITIAL_AZK)

            total_hours = df_m['working_hours'].sum()
            payable_hours = min(total_hours, CONTRACT_HOURS)

            bonus_total = df_m['bonus'].sum() if 'bonus' in df_m.columns else 0.0
            travel_total = df_m['travel_eur'].sum() if 'travel_eur' in df_m.columns else 0.0

            salario = round(payable_hours * HOURLY_RATE, 2)
            tax_total = round(salario * TAX_RATE, 2)
            net_total = round(salario - tax_total + bonus_total + travel_total, 2)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total hours", f"{total_hours:.2f} h")
            col2.metric("Payable hours", f"{payable_hours:.2f} h")
            col3.metric("AZK change (this month)", f"{azk_change:.2f} h")
            col4.metric("AZK bank (end of month)", f"{azk_bank:.2f} h")

            st.write("---")
            st.write("Financial Summary")
            st.write(f"Salary for payable hours ({payable_hours:.2f} h): €{salario:.2f}")
            st.write(f"Tax (27.64% on hourly pay): €{tax_total:.2f}")
            st.write(f"Bonus total: €{bonus_total:.2f}")
            st.write(f"Travel total: €{travel_total:.2f}")
            st.write(f"Net pay (Salary - Tax + Bonus + Travel): €{net_total:.2f}")
            st.write("---")

            # chart
            fig, ax = plt.subplots(figsize=(8,3))
            if not df_m.empty:
                ax.bar(df_m['date'].astype(str), df_m['working_hours'])
                ax.axhline(CONTRACT_HOURS/22, linestyle='--', label='Avg required (approx/day)')
                ax.set_ylabel("Hours")
                ax.set_xlabel("Date")
                plt.xticks(rotation=45, ha='right')
                st.pyplot(fig)

            # build summary message and HTML
            summary = (
                f"MUL Company - Monthly Summary ({year}-{month:02d})\n"
                f"Total worked hours: {total_hours:.2f} h\n"
                f"Payable hours: {payable_hours:.2f} h\n"
                f"AZK change: {azk_bank:.2f} h\n"
                f"Gross (hourly pay): €{salario:.2f}\n"
                f"Tax: €{tax_total:.2f}\n"
                f"Bonus total: €{bonus_total:.2f}\n"
                f"Travel total: €{travel_total:.2f}\n"
                f"Net pay: €{net_total:.2f}"
            )
            html = build_email_html(summary)

            if st.button("Preview message"):
                st.text_area("Preview message (HTML below)", value=summary, height=220)

            st.subheader("📧 Email with Branded PDF Payslip")
            email_to = st.text_input("Recipient Email", value=settings.get("auto_email",{}).get("recipient",""))

            col_e1, col_e2 = st.columns([2,1])
            with col_e1:
                if st.button("Generate PDF Payslip (preview)", width="stretch"):
                    if df_m.empty:
                        st.warning("No data this month to generate payslip.")
                    else:
                        pdf_buf = generate_payslip_pdf(
                            df_m, year, month, logo_path=LOGO_PATH
                        )

                        st.download_button(
                            "Download Payslip PDF",
                            data=pdf_buf,
                            file_name=f"payslip_{year}_{month:02d}.pdf",
                            mime="application/pdf",
                            width="stretch"
                        )

            with col_e2:
                if st.button("Send Email (with PDF)", width="stretch"):
                    if not email_to.strip():
                        st.error("Please enter recipient email.")
                    elif df_m.empty:
                        st.error("No data for this month to send.")
                    else:
                        pdf_buf = generate_payslip_pdf(
                            df_m, year, month, logo_path=LOGO_PATH
                        )

                        ok, err = send_email_with_attachment(
                            email_to.strip(),
                            f"MUL Payslip {year}-{month:02d}",
                            html,
                            attachment_bytes=pdf_buf,
                            attachment_name=f"payslip_{year}_{month:02d}.pdf"
                        )

                        if ok:
                            st.success(f"Email sent to {email_to.strip()} (with PDF).")
                        else:
                            st.error(f"Failed to send email: {err}")

            # Excel export
            if st.button("Export monthly Excel"):
                towrite = io.BytesIO()
                with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
                    df_m.to_excel(writer, index=False, sheet_name="Summary")
                    # summary sheet
                    summary_df = pd.DataFrame({
                        "Metric":["Total hours","Payable hours","Gross","Tax","Bonus total","Travel total","Net pay"],
                        "Value":[total_hours,payable_hours,salario,tax_total,bonus_total,travel_total,net_total]
                    })
                    summary_df.to_excel(writer, index=False, sheet_name="Totals")
                    writer.save()
                towrite.seek(0)
                st.download_button(label="Download Excel", data=towrite, file_name=f"mul_summary_{year}_{month:02d}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---- SETTINGS TAB ----
with tabs[3]:
    st.header("Settings & Integration")
    st.markdown("Configure storage, email sender credentials, logo, and auto-monthly email. Settings are saved locally to `mul_settings.json` (not secure). Use Streamlit secrets in production.")

    with st.expander("Email Settings"):
        sender_email = st.text_input("Sender Email (e.g. your@gmail.com)", value=settings.get("sender_email",""))
        sender_password = st.text_input("Sender App Password", value=settings.get("sender_password",""), type="password")
        if st.button("Save Email (session & settings)"):
            settings["sender_email"] = sender_email.strip()
            settings["sender_password"] = sender_password.strip()
            st.session_state["sender_email"] = sender_email.strip()
            st.session_state["sender_password"] = sender_password.strip()
            save_settings(settings)
            st.success("Email sender saved locally (use App Password for Gmail).")

    with st.expander("Logo (for branded payslip)"):
        st.markdown("Upload a PNG/JPG logo (will be saved as logo.png in app folder)")
        logo_file = st.file_uploader("Upload logo image", type=["png","jpg","jpeg"])
        if logo_file is not None:
            with open(LOGO_PATH, "wb") as f:
                f.write(logo_file.getbuffer())
            st.success("Logo saved as logo.png")
            st.image(LOGO_PATH, width=200)

    with st.expander("Auto monthly email"):
        st.markdown("Automatically send monthly summary & payslip on the configured day of month (server must run / app must be opened that day).")
        auto_enabled = st.checkbox("Enable automatic monthly email", value=settings.get("auto_email",{}).get("enabled", False))
        auto_recipient = st.text_input("Recipient for auto emails", value=settings.get("auto_email",{}).get("recipient",""))
        auto_day = st.number_input("Day of month to send (1-28 recommended)", min_value=1, max_value=28, value=settings.get("auto_email",{}).get("day",1))
        if st.button("Save Auto Email settings"):
            settings.setdefault("auto_email", {})["enabled"] = bool(auto_enabled)
            settings["auto_email"]["recipient"] = auto_recipient.strip()
            settings["auto_email"]["day"] = int(auto_day)
            save_settings(settings)
            st.success("Auto-monthly settings saved.")

    st.markdown("---")
    st.info("Security note: this prototype stores email credentials locally in a JSON file. For production use Streamlit Secrets or an environment-based secret manager.")

st.sidebar.markdown("Quick actions")
if st.sidebar.button("Open CSV"):
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            st.sidebar.code(f.read()[:4000])
    else:
        st.sidebar.info("No CSV yet.")

st.sidebar.info("Prototype: branded PDF payslip generation, HTML + PDF email, Excel export, dark mode toggle, auto-monthly option.")

# Persist current state
try:
    df_current = load_data(engine)
    save_to_storage(df_current, engine)
except Exception:
    pass
