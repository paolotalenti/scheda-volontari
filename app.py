import os
import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_file
from fpdf import FPDF
import csv
from io import BytesIO, StringIO
from datetime import datetime
import schedule
import time
import threading
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')

# Connessione al database PostgreSQL
def get_db_connection():
    try:
        return psycopg.connect(os.getenv('DATABASE_URL'))
    except psycopg.OperationalError as e:
        print(f"Errore di connessione al database: {e}")
        raise

# Backup automatico
def backup_automatico():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Backup visite
        cur.execute("""
            SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
                   vol.cognome, vol.nome, ass.citta
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
        """)
        visite = cur.fetchall()

        # Backup volontari
        cur.execute("SELECT email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione FROM volontari")
        volontari = cur.fetchall()

        # Backup assistiti
        cur.execute("SELECT nome_sigla, citta FROM assistiti")
        assistiti = cur.fetchall()

        # Crea directory backups se non esiste
        os.makedirs('backups', exist_ok=True)
        filename = f"backups/backup_dati_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        with open(filename, 'w', newline='', encoding='utf-8') as output_file:
            writer = csv.writer(output_file, lineterminator='\n')

            # Scrittura visite
            writer.writerow(['--- Visite ---'])
            writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare'])
            for visita in visite:
                writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4] or '', visita[5] or ''])

            # Scrittura volontari
            writer.writerow(['--- Volontari ---'])
            writer.writerow(['Email', 'Cognome', 'Nome', 'Telefono', 'Competenze', 'Disponibilità', 'Data Iscrizione'])
            for volontario in volontari:
                writer.writerow([volontario[0], volontario[1], volontario[2], volontario[3] or '', volontario[4] or '', volontario[5] or '', volontario[6] or ''])

            # Scrittura assistiti
            writer.writerow(['--- Assistiti ---'])
            writer.writerow(['Nome Sigla', 'Città'])
            for assistito in assistiti:
                writer.writerow([assistito[0], assistito[1]])

    except psycopg.OperationalError as e:
        print(f"Errore nel backup automatico: {e}")
    finally:
        cur.close()
        conn.close()

# Avvia il backup automatico ogni giorno alle 02:00
def run_scheduler():
    schedule.every().day.at("02:00").do(backup_automatico)
    while True:
        schedule.run_pending()
        time.sleep(60)

# Avvia il thread per il backup automatico
threading.Thread(target=run_scheduler, daemon=True).start()

# Homepage
@app.route('/')
def home():
    return redirect(url_for('inserisci_visita'))

# Favicon
@app.route('/favicon.ico')
def favicon():
    return send_file('static/favicon.ico')

# Login amministratore
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    try:
        if request.method == 'POST':
            password = request.form.get('password')
            if not password:
                flash('Inserisci una password.', 'error')
                return render_template('admin_login.html')
            if password == 'admin123':
                session['logged_in'] = True
                return redirect(url_for('report'))
            else:
                flash('Password errata. Riprova.', 'error')
                return render_template('admin_login.html')
        return render_template('admin_login.html')
    except Exception as e:
        print(f"Errore in /admin_login: {e}")
        raise

# Report
@app.route('/report', methods=['GET', 'POST'])
def report():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Filtri
    volontario_email = request.form.get('volontario_email', '')
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
    
    if volontario_email:
        query += " AND v.volontario_email = %s"
        params.append(volontario_email)
    if data_inizio:
        query += " AND v.data_visita >= %s"
        params.append(data_inizio)
    if data_fine:
        query += " AND v.data_visita <= %s"
        params.append(data_fine)

    try:
        cur.execute(query, params)
        visite = cur.fetchall()
    
        # Query per il conteggio totale
        count_query = "SELECT COUNT(*) FROM visite"
        if params:
            count_query += " WHERE 1=1"
            if volontario_email:
                count_query += " AND volontario_email = %s"
            if data_inizio:
                count_query += " AND data_visita >= %s"
            if data_fine:
                count_query += " AND data_visita <= %s"
        cur.execute(count_query, params)
        totale_visite = cur.fetchone()[0]

        # Query per accoglienza
        accoglienza_query = "SELECT accoglienza, COUNT(*) FROM visite"
        if params:
            accoglienza_query += " WHERE 1=1"
            if volontario_email:
                accoglienza_query += " AND volontario_email = %s"
            if data_inizio:
                accoglienza_query += " AND data_visita >= %s"
            if data_fine:
                accoglienza_query += " AND data_visita <= %s"
        accoglienza_query += " GROUP BY accoglienza"
        cur.execute(accoglienza_query, params)
        accoglienza_rows = cur.fetchall()
        accoglienza = {'Buona': 0, 'Media': 0, 'Scarsa': 0}
        for row in accoglienza_rows:
            accoglienza[row[0]] = row[1]

        # Query per visite per città
        citta_query = """
            SELECT ass.citta, COUNT(*) 
            FROM visite v 
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
        """
        if params:
            citta_query += " WHERE 1=1"
            if volontario_email:
                citta_query += " AND v.volontario_email = %s"
            if data_inizio:
                citta_query += " AND v.data_visita >= %s"
            if data_fine:
                citta_query += " AND v.data_visita <= %s"
        citta_query += " GROUP BY ass.citta"
        cur.execute(citta_query, params)
        visite_per_citta = dict(cur.fetchall())

        cur.execute("SELECT email, cognome, nome FROM volontari ORDER BY cognome, nome")
        volontari = cur.fetchall()

        statistiche = {
            'totale_visite': totale_visite,
            'accoglienza': accoglienza,
            'visite_per_citta': visite_per_citta
        }
    
        # Salva i filtri nella sessione per CSV, PDF e pulizia
        session['report_filters'] = {
            'volontario_email': volontario_email,
            'data_inizio': data_inizio,
            'data_fine': data_fine
        }
    
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        visite = []
        statistiche = {'totale_visite': 0, 'accoglienza': {'Buona': 0, 'Media': 0, 'Scarsa': 0}, 'visite_per_citta': {}}
        volontari = []
    
    finally:
        cur.close()
        conn.close()
    
    return render_template('report.html', visite=visite, statistiche=statistiche, 
                          volontari=volontari, filtro_volontario=volontario_email, 
                          data_inizio=data_inizio, data_fine=data_fine)

# Download PDF
@app.route('/download_pdf')
def download_pdf():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    # Recupera i filtri dalla sessione
    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
                   vol.cognome, vol.nome, ass.citta
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
            WHERE 1=1
        """
        params = []
        
        if volontario_email:
            query += " AND v.volontario_email = %s"
            params.append(volontario_email)
        if data_inizio:
            query += " AND v.data_visita >= %s"
            params.append(data_inizio)
        if data_fine:
            query += " AND v.data_visita <= %s"
            params.append(data_fine)

        cur.execute(query, params)
        visite = cur.fetchall()

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 8, txt="Report Visite", ln=1, align='C')
        
        for visita in visite:
            pdf.cell(200, 6, txt=f"Volontario: {visita[7]} {visita[6]} ({visita[0]})", ln=1)
            pdf.cell(200, 6, txt=f"Assistito: {visita[1]} ({visita[8]})", ln=1)
            pdf.cell(200, 6, txt=f"Accoglienza: {visita[2]}", ln=1)
            pdf.cell(200, 6, txt=f"Data: {visita[3]}", ln=1)
            pdf.cell(200, 6, txt=f"Necessità: {visita[4] or 'Nessuna'}", ln=1)
            pdf.cell(200, 6, txt=f"Miglioramenti: {visita[5] or 'Nessuno'}", ln=1)
            pdf.cell(200, 6, txt="", ln=1)

        pdf_output = BytesIO()
        pdf_output.write(pdf.output(dest='S').encode('latin1'))
        pdf_output.seek(0)
        
        return send_file(pdf_output, download_name="report_visite.pdf", as_attachment=True)
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('report'))
    finally:
        cur.close()
        conn.close()

# Download CSV
@app.route('/download_csv')
def download_csv():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    # Recupera i filtri dalla sessione
    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = """
            SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
                   vol.cognome, vol.nome, ass.citta
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
            WHERE 1=1
        """
        params = []
        
        if volontario_email:
            query += " AND v.volontario_email = %s"
            params.append(volontario_email)
        if data_inizio:
            query += " AND v.data_visita >= %s"
            params.append(data_inizio)
        if data_fine:
            query += " AND v.data_visita <= %s"
            params.append(data_fine)

        cur.execute(query, params)
        visite = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare'])
        
        for visita in visite:
            writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4] or 'Nessuna', visita[5] or 'Nessuno'])

        csv_output = output.getvalue().encode('utf-8')
        output.close()
        return Response(csv_output, mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=report_visite.csv"})
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('report'))
    finally:
        cur.close()
        conn.close()

# Backup manuale
@app.route('/backup')
def backup():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Backup visite
        cur.execute("""
            SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
                   vol.cognome, vol.nome, ass.citta
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
        """)
        visite = cur.fetchall()

        # Backup volontari
        cur.execute("SELECT email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione FROM volontari")
        volontari = cur.fetchall()

        # Backup assistiti
        cur.execute("SELECT nome_sigla, citta FROM assistiti")
        assistiti = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')

        # Scrittura visite
        writer.writerow(['--- Visite ---'])
        writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Cosa Migliorare'])
        for visita in visite:
            writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4] or '', visita[5] or ''])

        # Scrittura volontari
        writer.writerow(['--- Volontari ---'])
        writer.writerow(['Email', 'Cognome', 'Nome', 'Telefono', 'Competenze', 'Disponibilità', 'Data Iscrizione'])
        for volontario in volontari:
            writer.writerow([volontario[0], volontario[1], volontario[2], volontario[3] or '', volontario[4] or '', volontario[5] or '', volontario[6] or ''])

        # Scrittura assistiti
        writer.writerow(['--- Assistiti ---'])
        writer.writerow(['Nome Sigla', 'Città'])
        for assistito in assistiti:
            writer.writerow([assistito[0], assistito[1]])

        csv_output = output.getvalue().encode('utf-8')
        output.close()
        return Response(csv_output, mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=backup_dati_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"})
    except psycopg.OperationalError as e:
        flash(f"Errore nel backup: {e}", "error")
        return redirect(url_for('report'))
    finally:
        cur.close()
        conn.close()

# Ripristino dati
@app.route('/restore', methods=['GET', 'POST'])
def restore():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        password = request.form.get('password')
        if password != 'admin123':
            flash("Password errata per il ripristino.", "error")
            return render_template('restore.html')

        file = request.files.get('file')
        if not file or not file.filename.endswith('.csv'):
            flash("Carica un file CSV valido.", "error")
            return render_template('restore.html')

        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Pulizia del database
            cur.execute("DELETE FROM visite")
            cur.execute("DELETE FROM volontari")
            cur.execute("DELETE FROM assistiti")
            conn.commit()

            # Leggi il file CSV
            content = file.read().decode('utf-8-sig').splitlines()
            if not content:
                raise Exception("Il file CSV è vuoto.")
            reader = csv.reader(content)
            section = None
            assistiti = []
            volontari = []
            visite = []

            # Raccogli i dati
            for i, row in enumerate(reader):
                if not row or not any(row):  # Ignora righe vuote
                    continue
                if row[0].startswith('---'):
                    section = row[0]
                    continue
                if section == '--- Visite ---' and row[0] != 'Volontario Email':
                    if len(row) < 9:
                        continue
                    visite.append((row[0], row[3], row[5], row[6], row[7] or None, row[8] or None))
                elif section == '--- Volontari ---' and row[0] != 'Email':
                    if len(row) < 7:
                        continue
                    volontari.append((row[0], row[1], row[2], row[3] or None, row[4] or None, row[5] or None, row[6] or None))
                elif section == '--- Assistiti ---' and row[0] != 'Nome Sigla':
                    if len(row) < 2:
                        continue
                    assistiti.append((row[0], row[1]))

            # Inserisci i dati
            for assistito in assistiti:
                cur.execute("INSERT INTO assistiti (nome_sigla, citta) VALUES (%s, %s)", assistito)
            for volontario in volontari:
                cur.execute("""
                    INSERT INTO volontari (email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, volontario)
            for visita in visite:
                cur.execute("""
                    INSERT INTO visite (volontario_email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, visita)

            conn.commit()
            flash("Dati ripristinati con successo!", "success")
            return redirect(url_for('report'))
        except psycopg.OperationalError as e:
            flash(f"Errore nel ripristino: {e}", "error")
        except Exception as e:
            flash(f"Errore generico nel ripristino: {e}", "error")
        finally:
            cur.close()
            conn.close()

    return render_template('restore.html')

# Pulizia visite
@app.route('/clean', methods=['POST'])
def clean():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    # Recupera i filtri dalla sessione
    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        query = "DELETE FROM visite"
        params = []
        if volontario_email or data_inizio or data_fine:
            query += " WHERE 1=1"
            if volontario_email:
                query += " AND volontario_email = %s"
                params.append(volontario_email)
            if data_inizio:
                query += " AND data_visita >= %s"
                params.append(data_inizio)
            if data_fine:
                query += " AND v.data_visita <= %s"
                params.append(data_fine)
        cur.execute(query, params)
        conn.commit()
        flash("Visite eliminate con successo!", "success")
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('report'))

# Pulizia completa
@app.route('/clean_volontari', methods=['POST'])
def clean_volontari():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    # Richiedi conferma password
    password = request.form.get('password')
    if password != 'admin123':
        flash("Password errata per la pulizia completa.", "error")
        return redirect(url_for('report'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM visite")
        cur.execute("DELETE FROM volontari")
        cur.execute("DELETE FROM assistiti")
        conn.commit()
        flash("Tutti i dati sono stati eliminati!", "success")
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('report'))

# Manuale
@app.route('/manuale')
def manuale():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))
    
    return render_template('manuale.html')

# Lista volontari
@app.route('/volontari', methods=['GET'])
def lista_volontari():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione FROM volontari ORDER BY cognome, nome")
        volontari = cur.fetchall()
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento dei volontari: {e}", "error")
        volontari = []
    finally:
        cur.close()
        conn.close()
    
    return render_template('volontari.html', volontari=volontari)

# Aggiungi volontario
@app.route('/volontari/aggiungi', methods=['GET', 'POST'])
def aggiungi_volontario():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        email = request.form.get('email')
        cognome = request.form.get('cognome')
        nome = request.form.get('nome')
        telefono = request.form.get('telefono')
        competenze = request.form.get('competenze')
        disponibilita = request.form.get('disponibilita')

        if not email or not cognome or not nome:
            flash("Email, cognome e nome sono obbligatori.", "error")
            return render_template('aggiungi_volontario.html')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Verifica se l'email esiste già
            cur.execute("SELECT email FROM volontari WHERE email = %s", (email,))
            if cur.fetchone():
                flash(f"L'email {email} è già registrata.", "error")
                return render_template('aggiungi_volontario.html')
            
            cur.execute("""
                INSERT INTO volontari (email, cognome, nome, telefono, competenze, disponibilita)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (email, cognome, nome, telefono, competenze, disponibilita))
            conn.commit()
            flash(f"Volontario {nome} {cognome} aggiunto con successo!", "success")
            return redirect(url_for('lista_volontari'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiunta del volontario: {e}", "error")
        finally:
            cur.close()
            conn.close()
    
    return render_template('aggiungi_volontario.html')

# Modifica volontario
@app.route('/volontari/modifica/<email>', methods=['GET', 'POST'])
def modifica_volontario(email):
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        cognome = request.form.get('cognome')
        nome = request.form.get('nome')
        telefono = request.form.get('telefono')
        competenze = request.form.get('competenze')
        disponibilita = request.form.get('disponibilita')

        if not cognome or not nome:
            flash("Cognome e nome sono obbligatori.", "error")
            return render_template('modifica_volontario.html', volontario={
                'email': email, 'cognome': cognome, 'nome': nome, 'telefono': telefono, 
                'competenze': competenze, 'disponibilita': disponibilita
            })

        try:
            cur.execute("""
                UPDATE volontari 
                SET cognome = %s, nome = %s, telefono = %s, competenze = %s, disponibilita = %s
                WHERE email = %s
            """, (cognome, nome, telefono, competenze, disponibilita, email))
            conn.commit()
            flash(f"Volontario {nome} {cognome} aggiornato con successo!", "success")
            return redirect(url_for('lista_volontari'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiornamento del volontario: {e}", "error")
        finally:
            cur.close()
            conn.close()

    try:
        cur.execute("SELECT email, cognome, nome, telefono, competenze, disponibilita FROM volontari WHERE email = %s", (email,))
        volontario = cur.fetchone()
        if not volontario:
            flash("Volontario non trovato nel database.", "error")
            return render_template('modifica_volontario.html', volontario=None)
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento del volontario: {e}", "error")
        volontario = None
    finally:
        cur.close()
        conn.close()

    return render_template('modifica_volontario.html', volontario=volontario)

# Elimina volontario
@app.route('/volontari/elimina/<email>', methods=['POST'])
def elimina_volontario(email):
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM visite WHERE volontario_email = %s", (email,))
        visite_count = cur.fetchone()[0]
        if visite_count > 0:
            flash("Impossibile eliminare: il volontario ha visite associate.", "error")
            return redirect(url_for('lista_volontari'))

        cur.execute("DELETE FROM volontari WHERE email = %s", (email,))
        conn.commit()
        flash("Volontario eliminato con successo!", "success")
    except psycopg.OperationalError as e:
        flash(f"Errore nell'eliminazione del volontario: {e}", "error")
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('lista_volontari'))

# Inserisci visita (accessibile a tutti)
@app.route('/inserisci_visita', methods=['GET', 'POST'])
def inserisci_visita():
    # Assicurati che la sessione non sia impostata come amministratore
    session['logged_in'] = False

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT nome_sigla, citta FROM assistiti ORDER BY nome_sigla")
        assistiti = cur.fetchall()
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento dei dati: {e}", "error")
        assistiti = []
        cur.close()
        conn.close()
        return render_template('inserisci_visita.html', assistiti=assistiti)
    finally:
        cur.close()
        conn.close()

    if request.method == 'POST':
        volontario_email = request.form.get('volontario_email')
        volontario_cognome = request.form.get('volontario_cognome')
        volontario_nome = request.form.get('volontario_nome')
        telefono = request.form.get('telefono')
        competenze = request.form.get('competenze')
        disponibilita = request.form.get('disponibilita')
        assistito_nome = request.form.get('assistito_nome')
        accoglienza = request.form.get('accoglienza')
        data_visita = request.form.get('data_visita')
        necessita = request.form.get('necessita')
        cosa_migliorare = request.form.get('cosa_migliorare')

        if not volontario_email or not assistito_nome or not accoglienza or not data_visita:
            flash("Email, assistito, accoglienza e data visita sono obbligatori.", "error")
            return render_template('inserisci_visita.html', assistiti=assistiti)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT email, cognome, nome FROM volontari WHERE email = %s", (volontario_email,))
            existing_volontario = cur.fetchone()
            if not existing_volontario:
                if not volontario_cognome or not volontario_nome:
                    flash("Cognome e nome sono obbligatori per un nuovo volontario.", "error")
                    cur.close()
                    conn.close()
                    return render_template('inserisci_visita.html', assistiti=assistiti)
                cur.execute("""
                    INSERT INTO volontari (email, cognome, nome, telefono, competenze, disponibilita)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (volontario_email, volontario_cognome, volontario_nome, telefono, competenze, disponibilita))

            cur.execute("""
                INSERT INTO visite (volontario_email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (volontario_email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare))
            conn.commit()
            flash("Visita inserita con successo!", "success")
            return redirect(url_for('inserisci_visita'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'inserimento della visita: {e}", "error")
        finally:
            cur.close()
            conn.close()
    
    return render_template('inserisci_visita.html', assistiti=assistiti)

# Lista assistiti
@app.route('/assistiti', methods=['GET'])
def lista_assistiti():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT nome_sigla, citta FROM assistiti ORDER BY nome_sigla")
        assistiti = cur.fetchall()
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento degli assistiti: {e}", "error")
        assistiti = []
    finally:
        cur.close()
        conn.close()
    
    return render_template('assistiti.html', assistiti=assistiti)

# Aggiungi assistito
@app.route('/assistiti/aggiungi', methods=['GET', 'POST'])
def aggiungi_assistito():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        nome_sigla = request.form.get('nome_sigla')
        citta = request.form.get('citta')

        if not nome_sigla or not citta:
            flash("Nome sigla e città sono obbligatori.", "error")
            return render_template('aggiungi_assistito.html')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT nome_sigla FROM assistiti WHERE nome_sigla = %s", (nome_sigla,))
            if cur.fetchone():
                flash(f"Il nome sigla {nome_sigla} è già registrato.", "error")
                return render_template('aggiungi_assistito.html')
            
            cur.execute("INSERT INTO assistiti (nome_sigla, citta) VALUES (%s, %s)", (nome_sigla, citta))
            conn.commit()
            flash(f"Assistito {nome_sigla} aggiunto con successo!", "success")
            return redirect(url_for('lista_assistiti'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiunta dell'assistito: {e}", "error")
        finally:
            cur.close()
            conn.close()
    
    return render_template('aggiungi_assistito.html')

# Modifica assistito
@app.route('/assistiti/modifica/<nome_sigla>', methods=['GET', 'POST'])
def modifica_assistito(nome_sigla):
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        citta = request.form.get('citta')
        if not citta:
            flash("La città è obbligatoria.", "error")
            return render_template('modifica_assistito.html', assistito={'nome_sigla': nome_sigla, 'citta': citta})

        try:
            cur.execute("UPDATE assistiti SET citta = %s WHERE nome_sigla = %s", (citta, nome_sigla))
            conn.commit()
            flash(f"Assistito {nome_sigla} aggiornato con successo!", "success")
            return redirect(url_for('lista_assistiti'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiornamento dell'assistito: {e}", "error")
        finally:
            cur.close()
            conn.close()

    try:
        cur.execute("SELECT nome_sigla, citta FROM assistiti WHERE nome_sigla = %s", (nome_sigla,))
        assistito = cur.fetchone()
        if not assistito:
            flash("Assistito non trovato nel database.", "error")
            return render_template('modifica_assistito.html', assistito=None)
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento dell'assistito: {e}", "error")
        assistito = None
    finally:
        cur.close()
        conn.close()

    return render_template('modifica_assistito.html', assistito=assistito)

# Elimina assistito
@app.route('/assistiti/elimina/<nome_sigla>', methods=['POST'])
def elimina_assistito(nome_sigla):
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM visite WHERE assistito_nome = %s", (nome_sigla,))
        visite_count = cur.fetchone()[0]
        if visite_count > 0:
            flash("Impossibile eliminare: l'assistito ha visite associate.", "error")
            return redirect(url_for('lista_assistiti'))

        cur.execute("DELETE FROM assistiti WHERE nome_sigla = %s", (nome_sigla,))
        conn.commit()
        flash(f"Assistito {nome_sigla} eliminato con successo!", "success")
    except psycopg.OperationalError as e:
        flash(f"Errore nell'eliminazione dell'assistito: {e}", "error")
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('lista_assistiti'))

# Logout
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('report_filters', None)
    flash("Logout effettuato con successo!", "success")
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)