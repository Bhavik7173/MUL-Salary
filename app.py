# streamlit_mul_app.py
# MUL Salary Tracker - Streamlit prototype
# Save as streamlit_mul_app.py and run: streamlit run streamlit_mul_app.py

# Phone number ID: 795690100304535
# WhatsApp Business Account ID: 1461490331576770
# Access Token : EAANXBTTBzQcBP7kYg24HQwxZCe1odAn7BotFdur4JMX7TlHIni3sWrbSj8vDCfiZA6d0BjdU2rFJLqbUWNXR0rykePGZAYZBuX5gor0pZA4etbEun8JS97FqfdDGIMeZBhXg6sJs7iBfdJOpxvOkWmrCpRZBsnMrq2pjJj3YL90nyZAqVarvpMSZBt3VHZCpQmW84DLGIlTWFe9Y8h2XLdx6ZBgP53xzuAbQwphAPDnAXZC27E84mpHLtcrU1zEmEptEzFqwQt1CdGgSkZBaLb8kUVwFAMtYZATixqo4e0zUevJgZDZD

# Account SID : ACa784d95bb8df4036d7c7b6a8df6e478c
# Auth Token : cb1de20d421f56c9dcecbed3842a8b55
# My Twilio phone number : +14155238886


import streamlit as st
import pandas as pd
import sqlite3
from sqlalchemy import create_engine
from datetime import datetime, time as dtime
from dateutil import parser
import requests
import os
import io
import matplotlib.pyplot as plt
from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ---- CONFIG ----
CSV_PATH = "work_log.csv"        # local CSV (will be created in running folder)
SQLITE_PATH = "work_log.db"      # local SQLite (will be created in running folder)
TABLE_NAME = "daily_records"
HOURLY_RATE = 14.53
TAX_RATE = 0.2764
CONTRACT_HOURS = 151.67
BONUS_AMOUNT = 6.0

st.set_page_config(page_title="MUL Salary Tracker", layout="wide")

# ---- UTILITIES ----
def ensure_storage():
    # ensure CSV exists
    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=[
            "date","day","public_holiday","start_time","end_time","break_hours",
            "working_hours","bonus","travel_eur","gross_pay","tax","net_pay","gross_hourly","source","notes"
        ])
        df.to_csv(CSV_PATH, index=False)
    # ensure sqlite table exists
    engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)
    with engine.connect() as conn:
        if not engine.dialect.has_table(conn, TABLE_NAME):
            df = pd.read_csv(CSV_PATH)
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
    gross = gross_hourly + bonus + (travel if not pd.isna(travel) else 0.0)
    tax = gross_hourly * TAX_RATE  # tax applies only on base hourly pay
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
    return df

def send_notification(phone=None, email=None, message=""):
    if phone:
        send_whatsapp_cloud(phone, message)
    if email:
        send_email(email, "Notification", message)

def send_whatsapp_cloud(token, phone_id, to_whatsapp, body):
    url = f"https://graph.facebook.com/v16.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_whatsapp,
        "type": "text",
        "text": {"body": body}
    }
    r = requests.post(url, headers=headers, json=data)
    r.raise_for_status()
    return r.json()

def send_whatsapp_twilio(account_sid, auth_token, from_whatsapp, to_whatsapp, body):
    client = Client(account_sid, auth_token)
    msg = client.messages.create(body=body, from_=from_whatsapp, to="whatsapp:" + to_whatsapp)
    return msg.sid

def send_email(to_email, subject, body):
    sender_email = "mrbhavik2711@gmail.com"
    sender_password = "sygw sbsh mqua jrwd".replace(" ", "")  # Use App Password, not normal login password
    
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print(f"✅ Email sent to {to_email}")
    except Exception as e:
        print(f"❌ Email failed: {e}")

def save_to_storage(df, engine=None):
    df.to_csv(CSV_PATH, index=False)
    engine = engine or create_engine(f"sqlite:///{SQLITE_PATH}")
    with engine.connect() as conn:
        df.to_sql(TABLE_NAME, conn, index=False, if_exists="replace")

tw_sid = "ACa784d95bb8df4036d7c7b6a8df6e478c"
tw_token = "cb1de20d421f56c9dcecbed3842a8b55"
tw_from = "+14155238886"
wa_token = "EAANXBTTBzQcBP78A5a7v3anBqPGUhIY75ZA5KQkfVvhHTxUfOrscLGHFwaJi7ahQ6t3osc9njiszBUk9Dy1A7I1v92Y6hegScml4ZBy9OnZA3kxZCgBLOZCZBnah5ZAASbBxfWErmvOHF4BgfVETF3wS7RhEJhFhnQd1oqtohPwA9T4lnePQZAZCoi8lxBTXVEUQaAZC84UZCGZCAx7qtEdle5F9JPSkCmPA2vacUb8YPmImOC6ClgPgXR5IMCytwBIMMP0guYN4Mb156ZB9YDOMFzZAa6cwtTEUf3NVTJRIBqdgZDZD"
wa_phone_id = "795690100304535"

# ---- INITIALIZE STORAGE ----
engine = ensure_storage()


# ---- UI ----
st.title("MUL Salary Tracker (Streamlit)")
st.markdown("Enter daily work data, upload Excel/CSV, or import CSV. App calculates hours, AZK, tax, bonus and summary.")

tabs = st.tabs(["Daily Entry", "Upload Excel/CSV", "Monthly Summary", "Settings"])

# ---- DAILY ENTRY TAB ----
with tabs[0]:
    st.header("Daily Entry")
    col1, col2, col3 = st.columns(3)
    with col1:
        date_input = st.date_input("Date", value=datetime.today().date())
        public_holiday = st.checkbox("Public Holiday", value=False)
        travel = st.number_input("Travel (€)", min_value=0.0, value=0.0, step=0.5)
    with col2:
        start_time = st.text_input("Start Time (HH:MM) - leave blank if holiday", value="08:00")
        end_time = st.text_input("End Time (HH:MM) - leave blank if holiday", value="16:30")
    with col3:
        break_hours = st.number_input("Break (hours)", min_value=0.0, value=0.5, step=0.25)
        notes = st.text_input("Notes (optional)")
    save_btn = st.button("Save Day")

    if save_btn:
        row = {
            "date": date_input,
            "day": date_input.strftime("%A"),
            "public_holiday": "Y" if public_holiday else "N",
            "start_time": start_time if start_time else "",
            "end_time": end_time if end_time else "",
            "break_hours": break_hours,
            "travel_eur": travel,
            "source": "manual",
            "notes": notes
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
        df = load_data(engine)
        # Replace append with concat
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        save_to_storage(df, engine)
        st.success(f"Saved {date_input} | Hours: {wh} | Net: €{net}")


    st.markdown("---")
    st.subheader("This month's entries (editable via CSV/DB)")
    df = load_data(engine)
    if not df.empty:
        current_month = datetime.today().month
        df['date'] = pd.to_datetime(df['date']).dt.date
        df_m = df[[d.month == current_month for d in pd.to_datetime(df['date'])]]
        if df_m.empty:
            st.info("No entries for this month yet.")
        else:
            st.dataframe(df_m.sort_values('date'))

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
                df = load_data(engine)
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
    df = load_data(engine)
    if df.empty:
        st.info("No records yet. Add data via Daily Entry or Upload.")
    else:
        df['date'] = pd.to_datetime(df['date']).dt.date
        months = sorted(list(set([(d.year, d.month) for d in df['date']] )), reverse=True)
        month_sel = st.selectbox("Select year-month", options=months, format_func=lambda ym: f"{ym[0]}-{ym[1]:02d}")
        year, month = month_sel
        # df_m = df[[d.year==year and d.month==month for d in pd.to_datetime(df['date'])]]
        df_dates = pd.to_datetime(df['date'])
        # df_m = df[(df_dates.dt.year == year) & (df_dates.dt.month == month)]
        df_m = df[df_dates.dt.month == current_month]
        df_m = df_m.sort_values('date')
        st.subheader(f"Entries for {year}-{month:02d}")
        st.dataframe(df_m)
        total_hours = df_m['working_hours'].sum()
        payable_hours = min(total_hours, CONTRACT_HOURS)
        azk_hours = total_hours - CONTRACT_HOURS
        bonus_total = df_m['bonus'].sum()
        travel_total = df_m['travel_eur'].sum()
        salario = round(payable_hours * HOURLY_RATE,2)
        tax_total = round(payable_hours * HOURLY_RATE * TAX_RATE,2)
        net_total = round(salario - tax_total + bonus_total + travel_total,2)
        col1, col2, col3 = st.columns(3)
        col1.metric("Total hours", f"{total_hours:.2f} h", delta=f"{azk_hours:.2f} h")
        col2.metric("Payable hours", f"{payable_hours:.2f} h")
        col3.metric("AZK (this month)", f"{azk_hours:.2f} h")
        st.write("---")
        st.write("Financial Summary")
        st.write(f"Salary for payable hours ({payable_hours:.2f} h): €{salario:.2f}")
        st.write(f"Tax (27.64% on hourly pay): €{tax_total:.2f}")
        st.write(f"Bonus total: €{bonus_total:.2f}")
        st.write(f"Travel total: €{travel_total:.2f}")
        st.write(f"Net pay (Salary - Tax + Bonus + Travel): €{net_total:.2f}")
        st.write("---")
        fig, ax = plt.subplots(figsize=(8,3))
        ax.bar(df_m['date'].astype(str), df_m['working_hours'])
        ax.axhline(CONTRACT_HOURS/22, linestyle='--', label='Avg required (approx/day)')
        ax.set_ylabel("Hours")
        ax.set_xlabel("Date")
        plt.xticks(rotation=45, ha='right')
        st.pyplot(fig)
        st.write("---")
        st.subheader("Send WhatsApp Summary")
        wa_method = st.selectbox("Choose WhatsApp method", options=["Twilio (recommended)", "WhatsApp Cloud API (Meta)", "None"])
        phone = st.text_input("WhatsApp number (international format, e.g. +49172...)", value="")
        # Construct the WhatsApp message
        message = (
            f"MUL Company - Monthly Summary ({year}-{month:02d})\n"
            f"Total worked hours: {total_hours:.2f} h\n"
            f"Payable hours: {payable_hours:.2f} h\n"
            f"AZK change: {azk_hours:.2f} h\n"
            f"Gross (hourly pay): €{salario:.2f}\n"
            f"Tax: €{tax_total:.2f}\n"
            f"Bonus total: €{bonus_total:.2f}\n"
            f"Travel total: €{travel_total:.2f}\n"
            f"Net pay: €{net_total:.2f}"
        )

        # if wa_method == "Twilio (recommended)":
        #     if st.session_state.get("tw_sid") and st.session_state.get("tw_token") and st.session_state.get("tw_from") and phone:
        #         sid = send_whatsapp_twilio(st.session_state["tw_sid"], st.session_state["tw_token"], st.session_state["tw_from"], phone, message)
        
        if st.button("Preview message"):
            message = (f"MUL Company - Monthly Summary ({year}-{month:02d})\n"
                       f"Total worked hours: {total_hours:.2f} h\n"
                       f"Payable hours: {payable_hours:.2f} h\n"
                       f"AZK change: {azk_hours:.2f} h\n"
                       f"Gross (hourly pay): €{salario:.2f}\n"
                       f"Tax: €{tax_total:.2f}\n"
                       f"Bonus total: €{bonus_total:.2f}\n"
                       f"Travel total: €{travel_total:.2f}\n"
                       f"Net pay: €{net_total:.2f}\n")
            st.text_area("Preview message", value=message, height=220)
            # send_email("bhavikpatel7173@gmail.com", "MUL Salary", "Hello Bhavik, "+message)
            
        if st.button("Send WhatsApp"):
            if wa_method == "Twilio (recommended)":
                if tw_sid and tw_token and tw_from and phone:
                    try:
                        sid = send_whatsapp_twilio(tw_sid, tw_token, tw_from, phone, message)
                        send_email("bhavikpatel7173@gmail.com", "MUL Salary", "Hello Bhavik, \n \n \n"+ message)
                        st.success(f"Message sent via Twilio! SID: {sid}")
                    except Exception as e:
                        st.error(f"Twilio send failed: {e}")
                else:
                    st.warning("Please enter all Twilio credentials and recipient phone number.")
            
            elif wa_method == "WhatsApp Cloud API (Meta)":
                if wa_token and wa_phone_id and phone:
                    try:
                        resp = send_whatsapp_cloud(wa_token, wa_phone_id, phone, message)
                        send_email("bhavikpatel7173@gmail.com", "MUL Salary", "Hello Bhavik, \n \n \n"+ message)
                        st.success(f"Message sent via WhatsApp Cloud! Response: {resp}")
                    except Exception as e:
                        st.error(f"WhatsApp Cloud send failed: {e}")
                else:
                    st.warning("Please enter WhatsApp Cloud API token, phone ID, and recipient number.")
            
            else:
                st.info("Send method is None. No message sent.")
        if st.button("Export monthly CSV"):
            towrite = io.BytesIO()
            df_out = df_m.copy()
            df_out.to_csv(towrite, index=False)
            towrite.seek(0)
            st.download_button(label="Download CSV", data=towrite, file_name=f"mul_summary_{year}_{month:02d}.csv", mime="text/csv")

# ---- SETTINGS TAB ----
with tabs[3]:
    st.header("Settings & Integration")
    st.markdown("Configure storage and WhatsApp integration keys. Keys are NOT persisted securely in this prototype; for production use a secure secret store.")
    with st.expander("Twilio Settings"):
        tw_sid = st.text_input("TWILIO_ACCOUNT_SID", value="", placeholder="AC...")
        tw_token = st.text_input("TWILIO_AUTH_TOKEN", value="", placeholder="auth token")
        tw_from = st.text_input("TWILIO_WHATSAPP_FROM (e.g. whatsapp:+1415...)", value="")
        if st.button("Save Twilio (session only)"):
            st.session_state["tw_sid"] = tw_sid
            st.session_state["tw_token"] = tw_token
            st.session_state["tw_from"] = tw_from
            st.success("Session stored (temporary).")
    with st.expander("WhatsApp Cloud API (Meta) settings"):
        wa_token = st.text_input("WHATSAPP_CLOUD_TOKEN", value="", placeholder="token")
        wa_phone_id = st.text_input("WHATSAPP_PHONE_ID", value="", placeholder="phone id")
        if st.button("Save WhatsApp Cloud (session only)"):
            st.session_state["wa_token"] = wa_token
            st.session_state["wa_phone_id"] = wa_phone_id
            st.success("Session stored (temporary).")

st.sidebar.markdown("Quick actions")
if st.sidebar.button("Open CSV"):
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r") as f:
            st.sidebar.code(f.read()[:4000])
    else:
        st.sidebar.info("No CSV yet.")

st.sidebar.info("Prototype: manual entry, Excel upload, monthly summary, CSV/SQLite storage. WhatsApp sending simulated.")

# Persist current state
try:
    df_current = load_data(engine)
    save_to_storage(df_current, engine)
except Exception:
    pass
