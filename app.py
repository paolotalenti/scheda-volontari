from flask import Flask, render_template, request, redirect, url_for, session, send_file
import sqlite3
import psycopg2
import csv
import io
import os
from fpdf import FPDF, XPos, YPos

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

def get_db_connection():
    if 'DATABASE_URL' in os.environ:
        return psycopg2.connect(os.environ['DATABASE_URL'], sslmode='require')
    else:
        return sqlite3.connect('volontari.db')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS volontari (
                      id SERIAL PRIMARY KEY,
                      cognome TEXT NOT NULL,
                      nome TEXT NOT NULL,
                      cellulare TEXT NOT NULL,
                      email TEXT NOT NULL UNIQUE)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS assistiti (
                      id SERIAL PRIMARY KEY,
                      nome_sigla TEXT NOT NULL UNIQUE,
                      citta TEXT NOT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS visite (
                      id SERIAL PRIMARY KEY,
                      volontario_email TEXT NOT NULL,
                      assistito_nome TEXT NOT NULL,
                      accoglienza TEXT NOT NULL,
                      data_visita TEXT NOT NULL,
                      necessita TEXT NOT NULL,
                      cosa_migliorare TEXT NOT NULL,
                      FOREIGN KEY (volontario_email) REFERENCES volontari(email),
                      FOREIGN KEY (assistito_nome) REFERENCES assistiti(nome_sigla))''')
    conn.commit()
    cursor.close()
    conn.close()

@app.route('/', methods=['GET', 'POST'])
def index():
    init_db()
    messaggio = None
    dati_volontario = None
    dati_assistito = None
    email = session.get('email', '').lower()
    if request.method == 'POST':
        if 'check_email' in request.form:
            email = request.form.get('email', '').lower()
            session['email'] = email
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id, cognome, nome, cellulare, email FROM volontari WHERE email = ?', (email,))
            dati_volontario = cursor.fetchone()
            cursor.close()
            conn.close()
            if not dati_volontario:
                messaggio = 'Email non registrata. Completa i dati per registrarti.'
        elif 'cognome' in request.form and 'nome' in request.form and 'cellulare' in request.form:
            email = session.get('email', '').lower()
            cognome = request.form['cognome']
            nome = request.form['nome']
            cellulare = request.form['cellulare']
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('INSERT INTO volontari (cognome, nome, cellulare, email) VALUES (?, ?, ?, ?)',
                              (cognome, nome, cellulare, email))
                conn.commit()
                messaggio = 'Volontario registrato con successo!'
                cursor.execute('SELECT id, cognome, nome, cellulare, email FROM volontari WHERE email = ?', (email,))
                dati_volontario = cursor.fetchone()
                cursor.close()
                conn.close()
            except (sqlite3.IntegrityError, psycopg2.IntegrityError):
                messaggio = 'Errore: Email già registrata.'
                conn.close()
        elif 'assistito_nome' in request.form:
            email = session.get('email', '').lower()
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id, cognome, nome, cellulare, email FROM volontari WHERE email = ?', (email,))
            dati_volontario = cursor.fetchone()
            if dati_volontario:
                assistito_nome = request.form['assistito_nome']
                cursor.execute('SELECT id, nome_sigla, citta FROM assistiti WHERE nome_sigla = ?', (assistito_nome,))
                dati_assistito = cursor.fetchone()
                if not dati_assistito:
                    citta = request.form.get('citta', '')
                    if not citta:
                        messaggio = 'Inserisci la città per un nuovo assistito.'
                        cursor.close()
                        conn.close()
                        return render_template('form.html', messaggio=messaggio, dati_volontario=dati_volontario, dati_assistito=dati_assistito, email=email)
                    cursor.execute('INSERT INTO assistiti (nome_sigla, citta) VALUES (?, ?)', (assistito_nome, citta))
                    conn.commit()
                accoglienza = request.form['accoglienza']
                data_visita = request.form['data_visita']
                necessita = request.form['necessita']
                cosa_migliorare = request.form['cosa_migliorare']
                cursor.execute('INSERT INTO visite (volontario_email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare) VALUES (?, ?, ?, ?, ?, ?)',
                              (email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare))
                conn.commit()
                messaggio = 'Visita registrata con successo!'
                cursor.close()
                conn.close()
            else:
                messaggio = 'Errore: Volontario non trovato.'
                cursor.close()
                conn.close()
    else:
        if email:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id, cognome, nome, cellulare, email FROM volontari WHERE email = ?', (email,))
            dati_volontario = cursor.fetchone()
            cursor.close()
            conn.close()
    return render_template('form.html', messaggio=messaggio, dati_volontario=dati_volontario, dati_assistito=dati_assistito, email=email)

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == os.environ.get('ADMIN_PASSWORD', 'admin123'):
            session['admin'] = True
            return redirect(url_for('report'))
        else:
            return render_template('admin_login.html', errore='Password errata.')
    return render_template('admin_login.html', errore=None)

@app.route('/report', methods=['GET', 'POST'])
def report():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT email, cognome, nome FROM volontari')
    volontari = cursor.fetchall()
    
    filtro_volontario = request.form.get('volontario_email', '') if request.method == 'POST' else session.get('filtro_volontario', '')
    data_inizio = request.form.get('data_inizio', '') if request.method == 'POST' else session.get('data_inizio', '')
    data_fine = request.form.get('data_fine', '') if request.method == 'POST' else session.get('data_fine', '')
    
    session['filtro_volontario'] = filtro_volontario
    session['data_inizio'] = data_inizio
    session['data_fine'] = data_fine
    
    query = 'SELECT v.cognome, v.nome, vi.assistito_nome, a.citta, vi.accoglienza, vi.data_visita, vi.necessita, vi.cosa_migliorare ' \
            'FROM visite vi JOIN volontari v ON vi.volontario_email = v.email ' \
            'JOIN assistiti a ON vi.assistito_nome = a.nome_sigla'
    params = []
    if filtro_volontario:
        query += ' WHERE v.email = %s' if 'DATABASE_URL' in os.environ else ' WHERE v.email = ?'
        params.append(filtro_volontario)
    if data_inizio:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita >= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita >= ?'
        params.append(data_inizio)
    if data_fine:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita <= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita <= ?'
        params.append(data_fine)
    
    cursor.execute(query, params)
    visite = cursor.fetchall()
    
    statistiche = {
        'totale_visite': len(visite),
        'accoglienza': {'Buona': 0, 'Media': 0, 'Scarsa': 0},
        'visite_per_citta': {}
    }
    for visita in visite:
        statistiche['accoglienza'][visita[4]] = statistiche['accoglienza'].get(visita[4], 0) + 1
        statistiche['visite_per_citta'][visita[3]] = statistiche['visite_per_citta'].get(visita[3], 0) + 1
    
    statistiche_volontari = {}
    for v in volontari:
        cursor.execute('SELECT COUNT(*) FROM visite WHERE volontario_email = %s' if 'DATABASE_URL' in os.environ else 'SELECT COUNT(*) FROM visite WHERE volontario_email = ?', (v[0],))
        statistiche_volontari[v[0]] = {'cognome': v[1], 'nome': v[2], 'visite': cursor.fetchone()[0]}
    
    cursor.close()
    conn.close()
    
    return render_template('report.html', statistiche=statistiche, statistiche_volontari=statistiche_volontari, volontari=volontari, visite=visite, filtro_volontario=filtro_volontario, data_inizio=data_inizio, data_fine=data_fine)

@app.route('/download_csv', methods=['GET', 'POST'])
def download_csv():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    filtro_volontario = request.form.get('volontario_email', '') if request.method == 'POST' else session.get('filtro_volontario', '')
    data_inizio = request.form.get('data_inizio', '') if request.method == 'POST' else session.get('data_inizio', '')
    data_fine = request.form.get('data_fine', '') if request.method == 'POST' else session.get('data_fine', '')
    
    query = 'SELECT v.cognome, v.nome, vi.assistito_nome, a.citta, vi.accoglienza, vi.data_visita, vi.necessita, vi.cosa_migliorare ' \
            'FROM visite vi JOIN volontari v ON vi.volontario_email = v.email ' \
            'JOIN assistiti a ON vi.assistito_nome = a.nome_sigla'
    params = []
    if filtro_volontario:
        query += ' WHERE v.email = %s' if 'DATABASE_URL' in os.environ else ' WHERE v.email = ?'
        params.append(filtro_volontario)
    if data_inizio:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita >= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita >= ?'
        params.append(data_inizio)
    if data_fine:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita <= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita <= ?'
        params.append(data_fine)
    
    cursor.execute(query, params)
    visite = cursor.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare'])
    for visita in visite:
        writer.writerow(visita)
    
    cursor.close()
    conn.close()
    
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')), mimetype='text/csv', as_attachment=True, download_name='report.csv')

@app.route('/download_pdf', methods=['GET', 'POST'])
def download_pdf():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    filtro_volontario = request.form.get('volontario_email', '') if request.method == 'POST' else session.get('filtro_volontario', '')
    data_inizio = request.form.get('data_inizio', '') if request.method == 'POST' else session.get('data_inizio', '')
    data_fine = request.form.get('data_fine', '') if request.method == 'POST' else session.get('data_fine', '')
    
    query = 'SELECT v.cognome, v.nome, vi.assistito_nome, a.citta, vi.accoglienza, vi.data_visita, vi.necessita, vi.cosa_migliorare ' \
            'FROM visite vi JOIN volontari v ON vi.volontario_email = v.email ' \
            'JOIN assistiti a ON vi.assistito_nome = a.nome_sigla'
    params = []
    if filtro_volontario:
        query += ' WHERE v.email = %s' if 'DATABASE_URL' in os.environ else ' WHERE v.email = ?'
        params.append(filtro_volontario)
    if data_inizio:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita >= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita >= ?'
        params.append(data_inizio)
    if data_fine:
        query += (' AND' if 'WHERE' in query else ' WHERE') + ' vi.data_visita <= %s' if 'DATABASE_URL' in os.environ else ' vi.data_visita <= ?'
        params.append(data_fine)
    
    cursor.execute(query, params)
    visite = cursor.fetchall()
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(200, 10, text="Report Visite", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)
    pdf.set_font("Helvetica", size=10)
    headers = ['Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare']
    col_widths = [25, 25, 25, 25, 25, 25, 25, 35]
    for header, width in zip(headers, col_widths):
        pdf.cell(width, 10, text=header, border=1)
    pdf.ln()
    for visita in visite:
        for item, width in zip(visita, col_widths):
            item_str = str(item)[:50]
            pdf.cell(width, 10, text=item_str, border=1)
        pdf.ln()
    
    cursor.close()
    conn.close()
    
    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return send_file(pdf_output, mimetype='application/pdf', as_attachment=True, download_name='report.pdf')

@app.route('/clean', methods=['GET', 'POST'])
def clean():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        data_inizio = request.form.get('data_inizio')
        data_fine = request.form.get('data_fine')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM visite WHERE data_visita BETWEEN %s AND %s' if 'DATABASE_URL' in os.environ else 'DELETE FROM visite WHERE data_visita BETWEEN ? AND ?', (data_inizio, data_fine))
        cursor.execute('DELETE FROM assistiti WHERE nome_sigla NOT IN (SELECT assistito_nome FROM visite)')
        conn.commit()
        cursor.close()
        conn.close()
        return render_template('clean.html', messaggio='Pulizia completata con successo!')
    return render_template('clean.html', messaggio=None)

@app.route('/clean_volontari', methods=['GET', 'POST'])
def clean_volontari():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM volontari WHERE email NOT IN (SELECT volontario_email FROM visite)')
        conn.commit()
        cursor.close()
        conn.close()
        return render_template('clean_volontari.html', messaggio='Volontari non associati a visite eliminati con successo!')
    return render_template('clean_volontari.html', messaggio=None)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    session.pop('email', None)
    return redirect(url_for('index'))

@app.route('/manuale')
def manuale():
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(200, 10, text="Manuale per i Volontari", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
    pdf.ln(10)
    pdf.set_font("Helvetica", size=10)
    manual_text = [
        "1. Come registrarsi:",
        "   - Inserisci la tua email e verifica se è già registrata.",
        "   - Se non sei registrato, inserisci cognome, nome e cellulare.",
        "2. Come registrare una visita:",
        "   - Dopo aver verificato la tua email, inserisci i dati dell'assistito.",
        "   - Compila i campi: Nome o Sigla Assistito, Città (se nuovo), Accoglienza, Data, Necessità, Cosa Migliorare.",
        "3. Scaricare il report:",
        "   - Accedi come amministratore con la password fornita.",
        "   - Vai alla sezione Report per scaricare il CSV o PDF.",
        "4. Problemi?:",
        "   - Contatta l'Amministratore Sistema al numero +39 348 5707840 o email paolo.talenti@gmail.com."
    ]
    for line in manual_text:
        pdf.cell(0, 10, text=line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return send_file(pdf_output, mimetype='application/pdf', as_attachment=True, download_name='manuale_volontari.pdf')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)