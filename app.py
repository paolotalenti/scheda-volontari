import os
import psycopg
from flask import Flask, render_template, request, redirect, url_for, session, send_file, Response
from fpdf import FPDF
import csv
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')

def get_db_connection():
    conn = psycopg.connect(os.environ['DATABASE_URL'])
    return conn


@app.route('/report', methods=['GET', 'POST'])
def report():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Filtri
    nome_filtro = request.form.get('nome_filtro', '')
    data_inizio = request.form.get('data_inizio', '')
    data_fine = request.form.get('data_fine', '')

    query = """
        SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
               vol.cognome, vol.nome, ass.citta
        FROM visite v
        JOIN volontari vol ON v.volontario_email = vol.email
        JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
        WHERE 1=1
    """
    params = []
    
    if nome_filtro:
        query += " AND (vol.cognome ILIKE %s OR vol.nome ILIKE %s)"
        params.extend([f'%{nome_filtro}%', f'%{nome_filtro}%'])
    if data_inizio:
        query += " AND v.data_visita >= %s"
        params.append(data_inizio)
    if data_fine:
        query += " AND v.data_visita <= %s"
        params.append(data_fine)

    try:
        cur.execute(query, params)
        visite = cur.fetchall()
    
        # Calcola statistiche
        cur.execute("SELECT COUNT(*) FROM visite")
        totale_visite = cur.fetchone()[0]
        statistiche = {'totale_visite': totale_visite}
    
    except psycopg.OperationalError as e:
        print(f"Database error: {e}")
        visite = []
        statistiche = {'totale_visite': 0}
    
    finally:
        cur.close()
        conn.close()
    
    return render_template('report.html', visite=visite, statistiche=statistiche, 
                          nome_filtro=nome_filtro, data_inizio=data_inizio, data_fine=data_fine)


@app.route('/download_pdf')
def download_pdf():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
               vol.cognome, vol.nome, ass.citta
        FROM visite v
        JOIN volontari vol ON v.volontario_email = vol.email
        JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
    """)
    visite = cur.fetchall()
    cur.close()
    conn.close()

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Report Visite", ln=1, align='C')
    
    for visita in visite:
        pdf.cell(200, 10, txt=f"Volontario: {visita[7]} {visita[6]} ({visita[0]})", ln=1)
        pdf.cell(200, 10, txt=f"Assistito: {visita[1]} ({visita[8]})", ln=1)
        pdf.cell(200, 10, txt=f"Accoglienza: {visita[2]}", ln=1)
        pdf.cell(200, 10, txt=f"Data: {visita[3]}", ln=1)
        pdf.cell(200, 10, txt=f"Necessità: {visita[4]}", ln=1)
        pdf.cell(200, 10, txt=f"Miglioramenti: {visita[5]}", ln=1)
        pdf.cell(200, 10, txt="", ln=1)

    pdf_output = BytesIO()
    pdf_output.write(pdf.output(dest='S').encode('latin1'))
    pdf_output.seek(0)
    
    return send_file(pdf_output, download_name="report_visite.pdf", as_attachment=True)

@app.route('/download_csv')
def download_csv():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
               vol.cognome, vol.nome, ass.citta
        FROM visite v
        JOIN volontari vol ON v.volontario_email = vol.email
        JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
    """)
    visite = cur.fetchall()
    cur.close()
    conn.close()

    output = BytesIO()
    writer = csv.writer(output)
    writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare'])
    
    for visita in visite:
        writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4], visita[5]])

    output.seek(0)
    return Response(output, mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=report_visite.csv"})

@app.route('/clean', methods=['POST'])
def clean():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM visite")
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('report'))

@app.route('/clean_volontari', methods=['POST'])
def clean_volontari():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM visite")
    cur.execute("DELETE FROM volontari")
    cur.execute("DELETE FROM assistiti")
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('report'))

@app.route('/manuale')
def manuale():
    if not session.get('logged_in'):
        return redirect(url_for('admin_login'))
    
    manual_content = """
    Manuale d'uso per l'applicazione Scheda Volontari:
    - Accedi con la password amministratore.
    - Usa il report per visualizzare le visite.
    - Filtra per nome, data inizio, o data fine.
    - Scarica il report in PDF o CSV.
    - Usa 'Pulisci Visite' per eliminare tutte le visite.
    - Usa 'Pulisci Tutto' per eliminare visite, volontari e assistiti.
    - Contatta l'Amministratore Sistema al numero +39 348 5707840 o paolo.talenti@gmail.com.
    """
    return render_template('manuale.html', manuale=manual_content)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    app.run(debug=True)