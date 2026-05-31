import os
import secrets
import psycopg
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER
from io import BytesIO, StringIO
from datetime import datetime, timedelta
import time
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import logging
import csv

# Configura il logging
logging.basicConfig(filename='backup.log', level=logging.INFO)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
if not ADMIN_PASSWORD:
    raise ValueError("ADMIN_PASSWORD non definita nel file .env")

BACKUP_TOKEN = os.getenv('BACKUP_TOKEN', secrets.token_hex(32))

# Connessione al database PostgreSQL
def get_db_connection():
    try:
        conn = psycopg.connect(os.getenv('DATABASE_URL'))
        conn.execute("SET TIME ZONE 'Europe/Rome'")
        return conn
    except psycopg.OperationalError as e:
        logging.error(f"Errore di connessione al database: {e}")
        raise

# Inizializza tabella config e migra la password dall'env var
def init_config():
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            INSERT INTO config (key, value)
            VALUES ('admin_password', %s)
            ON CONFLICT (key) DO NOTHING
        """, (ADMIN_PASSWORD,))
        # Migrazione: aggiunge nome e cognome alla tabella assistiti se non esistono
        cur.execute("ALTER TABLE assistiti ADD COLUMN IF NOT EXISTS nome TEXT")
        cur.execute("ALTER TABLE assistiti ADD COLUMN IF NOT EXISTS cognome TEXT")
        conn.commit()
        logging.info("init_config: tabella config verificata/creata. Migrazione assistiti eseguita.")
    except Exception as e:
        logging.error(f"Errore in init_config: {e}")
        if conn:
            conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def init_backups_table():
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                csv_content TEXT NOT NULL
            )
        """)
        conn.commit()
        logging.info("init_backups_table: tabella backups verificata/creata.")
    except Exception as e:
        logging.error(f"Errore in init_backups_table: {e}")
        if conn:
            conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def _genera_csv_backup():
    """Genera il contenuto CSV completo del backup e lo restituisce come stringa."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT v.volontario_email, v.assistito_nome, v.accoglienza, v.data_visita, v.necessita, v.cosa_migliorare,
                   vol.cognome, vol.nome, ass.citta
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
        """)
        visite = cur.fetchall()

        cur.execute("SELECT email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione FROM volontari")
        volontari = cur.fetchall()

        cur.execute("SELECT nome_sigla, citta, nome, cognome FROM assistiti")
        assistiti = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')

        writer.writerow(['--- Visite ---'])
        writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Considerazioni'])
        for visita in visite:
            writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4] or '', visita[5] or ''])

        writer.writerow(['--- Volontari ---'])
        writer.writerow(['Email', 'Cognome', 'Nome', 'Telefono', 'Competenze', 'Disponibilità', 'Data Iscrizione'])
        for volontario in volontari:
            writer.writerow([volontario[0], volontario[1], volontario[2], volontario[3] or '', volontario[4] or '', volontario[5] or '', volontario[6] or ''])

        writer.writerow(['--- Assistiti ---'])
        writer.writerow(['Nome Sigla', 'Città', 'Nome', 'Cognome'])
        for assistito in assistiti:
            writer.writerow([assistito[0], assistito[1], assistito[2] or '', assistito[3] or ''])

        return output.getvalue()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def _salva_backup_nel_db(csv_content, filename):
    """Salva il CSV nella tabella backups e rimuove i backup più vecchi di 30 giorni."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO backups (filename, csv_content) VALUES (%s, %s)",
            (filename, csv_content)
        )
        # Pulizia: mantieni solo gli ultimi 30 giorni
        cutoff = datetime.now() - timedelta(days=30)
        cur.execute("DELETE FROM backups WHERE created_at < %s", (cutoff,))
        conn.commit()
        logging.info(f"Backup salvato nel DB: {filename}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def get_admin_password():
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key = 'admin_password'")
        row = cur.fetchone()
        return row[0] if row else ADMIN_PASSWORD
    except Exception:
        return ADMIN_PASSWORD
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# Backup automatico
def backup_automatico():
    logging.info(f"Inizio backup automatico alle: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    for attempt in range(3):
        try:
            filename = f"backup_dati_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            csv_content = _genera_csv_backup()
            _salva_backup_nel_db(csv_content, filename)
            logging.info(f"Backup automatico completato: {filename}")
            break
        except Exception as e:
            logging.error(f"Tentativo {attempt + 1} fallito: {e}")
            time.sleep(5)

scheduler = BackgroundScheduler(timezone="Europe/Rome")
scheduler.add_job(backup_automatico, 'cron', hour=2, minute=0)
scheduler.start()
init_config()
init_backups_table()

@app.route('/')
def home():
    return redirect(url_for('inserisci_visita'))

@app.route('/favicon.ico')
def favicon():
    return send_file('static/favicon.ico')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    try:
        if request.method == 'POST':
            password = request.form.get('password')
            if not password:
                flash('Inserisci una password.', 'error')
                return render_template('admin_login.html')
            if password == get_admin_password():
                session['logged_in'] = True
                return redirect(url_for('report'))
            else:
                flash('Password errata. Riprova.', 'error')
                return render_template('admin_login.html')
        return render_template('admin_login.html')
    except Exception as e:
        logging.error(f"Errore in /admin_login: {e}")
        raise

@app.route('/report', methods=['GET', 'POST'])
def report():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'GET':
        # Default: dal 1° del mese precedente (nessun filtro su volontario o data fine)
        oggi = datetime.now()
        if oggi.month == 1:
            data_inizio = f"{oggi.year - 1}-12-01"
        else:
            data_inizio = f"{oggi.year}-{oggi.month - 1:02d}-01"
        data_fine = ''
        volontario_email = ''
    else:
        volontario_email = request.form.get('volontario_email', '')
        data_inizio = request.form.get('data_inizio', '')
        data_fine = request.form.get('data_fine', '')

    logging.info(f"volontario_email: {volontario_email}, data_inizio: {data_inizio}, data_fine: {data_fine}")

    try:
        if data_inizio:
            datetime.strptime(data_inizio, '%Y-%m-%d')
        if data_fine:
            datetime.strptime(data_fine, '%Y-%m-%d')
            data_fine = f"{data_fine} 23:59:59"
    except ValueError as e:
        flash(f"Formato data non valido: {e}", "error")
        return render_template('report.html', visite=[], statistiche={'totale_visite': 0, 'accoglienza': {'Buona': 0, 'Media': 0, 'Scarsa': 0}, 'visite_per_citta': {}}, volontari=[], filtro_volontario='', data_inizio='', data_fine='')

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
    
        count_query = "SELECT COUNT(*) FROM visite WHERE 1=1"
        if params:
            if volontario_email:
                count_query += " AND volontario_email = %s"
            if data_inizio:
                count_query += " AND data_visita >= %s"
            if data_fine:
                count_query += " AND data_visita <= %s"
        cur.execute(count_query, params)
        totale_visite = cur.fetchone()[0]

        accoglienza_query = "SELECT accoglienza, COUNT(*) FROM visite WHERE 1=1"
        if params:
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

        citta_query = """
            SELECT ass.citta, COUNT(*) 
            FROM visite v 
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
            WHERE 1=1
        """
        if params:
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
    
        session['report_filters'] = {
            'volontario_email': volontario_email,
            'data_inizio': data_inizio,
            'data_fine': data_fine if not data_fine.endswith('23:59:59') else data_fine[:10]
        }
    
    except psycopg.OperationalError as e:
        logging.error(f"Errore SQL: {e}")
        flash(f"Errore nel database: {e}", "error")
        return render_template('report.html', visite=[], statistiche={'totale_visite': 0, 'accoglienza': {'Buona': 0, 'Media': 0, 'Scarsa': 0}, 'visite_per_citta': {}}, volontari=[], filtro_volontario='', data_inizio='', data_fine='')
    except Exception as e:
        logging.error(f"Errore generico: {e}")
        flash(f"Errore imprevisto: {e}", "error")
        return render_template('report.html', visite=[], statistiche={'totale_visite': 0, 'accoglienza': {'Buona': 0, 'Media': 0, 'Scarsa': 0}, 'visite_per_citta': {}}, volontari=[], filtro_volontario='', data_inizio='', data_fine='')
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    
    return render_template('report.html', visite=visite, statistiche=statistiche, 
                          volontari=volontari, filtro_volontario=volontario_email, 
                          data_inizio=data_inizio, data_fine=data_fine[:10] if data_fine and data_fine.endswith('23:59:59') else data_fine)

@app.route('/download_pdf')
def download_pdf():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    if data_fine:
        try:
            datetime.strptime(data_fine, '%Y-%m-%d')
            data_fine = f"{data_fine} 23:59:59"
        except ValueError:
            flash("Formato data non valido.", "error")
            return redirect(url_for('report'))

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

        pdf_output = BytesIO()
        pdf = canvas.Canvas(pdf_output, pagesize=A4)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(100, 800, "Report Visite")
        
        y = 780
        for visita in visite:
            pdf.drawString(50, y, f"Volontario: {visita[7]} {visita[6]} ({visita[0]})")
            y -= 20
            pdf.drawString(50, y, f"Assistito: {visita[1]} ({visita[8]})")
            y -= 20
            pdf.drawString(50, y, f"Accoglienza: {visita[2]}")
            y -= 20
            pdf.drawString(50, y, f"Data: {visita[3]}")
            y -= 20
            pdf.drawString(50, y, f"Necessità: {visita[4] or 'Nessuna'}")
            y -= 20
            pdf.drawString(50, y, f"Considerazioni: {visita[5] or 'Nessuna'}")
            y -= 40
            if y < 50:
                pdf.showPage()
                y = 800

        pdf.save()
        pdf_output.seek(0)
        
        return send_file(pdf_output, download_name="report_visite.pdf", as_attachment=True)
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('report'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/download_csv')
def download_csv():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    if data_fine:
        try:
            datetime.strptime(data_fine, '%Y-%m-%d')
            data_fine = f"{data_fine} 23:59:59"
        except ValueError:
            flash("Formato data non valido.", "error")
            return redirect(url_for('report'))

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
        writer.writerow(['Volontario Email', 'Cognome', 'Nome', 'Assistito', 'Città', 'Accoglienza', 'Data Visita', 'Necessità', 'Considerazioni'])
        
        for visita in visite:
            writer.writerow([visita[0], visita[7], visita[6], visita[1], visita[8], visita[2], visita[3], visita[4] or 'Nessuna', visita[5] or 'Nessuno'])

        csv_output = output.getvalue().encode('utf-8')
        output.close()
        return Response(csv_output, mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=report_visite.csv"})
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('report'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/backup')
def backup():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    try:
        filename = f"backup_dati_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_content = _genera_csv_backup()
        _salva_backup_nel_db(csv_content, filename)
        flash("Backup creato con successo!", "success")
    except Exception as e:
        logging.error(f"Errore nel backup manuale: {e}")
        flash(f"Errore nel backup: {e}", "error")
    return redirect(url_for('report'))


@app.route('/run_backup')
def run_backup():
    """Endpoint per cron-job.org: esegue il backup senza login, protetto da token."""
    token = request.args.get('token', '')
    if not token or token != BACKUP_TOKEN:
        return Response('Unauthorized', status=401)
    try:
        backup_automatico()
        return Response('OK', status=200)
    except Exception as e:
        logging.error(f"Errore in /run_backup: {e}")
        return Response(f'Error: {e}', status=500)


@app.route('/download_backup/<int:backup_id>')
def download_backup(backup_id):
    """Scarica un backup specifico come file CSV."""
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT filename, csv_content FROM backups WHERE id = %s", (backup_id,))
        row = cur.fetchone()
        if not row:
            flash("Backup non trovato.", "error")
            return redirect(url_for('restore'))
        filename, csv_content = row
        return Response(
            csv_content.encode('utf-8'),
            mimetype='text/csv',
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )
    except Exception as e:
        flash(f"Errore nel download: {e}", "error")
        return redirect(url_for('restore'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/restore', methods=['GET', 'POST'])
def restore():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    # Carica lista backup dal DB
    backup_records = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, filename, created_at FROM backups ORDER BY created_at DESC")
        backup_records = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        flash(f"Errore nella lettura dei backup: {e}", "error")

    if request.method == 'POST':
        password = request.form.get('password')
        if password != get_admin_password():
            flash("Password errata per il ripristino.", "error")
            return render_template('restore.html', backup_records=backup_records)

        selected_id = request.form.get('backup_file_select')
        if selected_id:
            conn = None
            cur = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()

                # Leggi il CSV dal DB
                cur.execute("SELECT filename, csv_content FROM backups WHERE id = %s", (selected_id,))
                row = cur.fetchone()
                if not row:
                    raise Exception("Backup non trovato nel database.")
                selected_filename, csv_content = row

                cur.execute("DELETE FROM visite")
                cur.execute("DELETE FROM volontari")
                cur.execute("DELETE FROM assistiti")
                conn.commit()

                content = csv_content.splitlines()
                if not content:
                    raise Exception("Il backup è vuoto.")
                reader = csv.reader(content)
                section = None
                assistiti = []
                volontari = []
                visite = []

                for i, row in enumerate(reader):
                    if not row or not any(row):
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
                        # Retrocompatibile: vecchi backup hanno 2 colonne, nuovi 4
                        nome_ass = row[2] if len(row) > 2 else None
                        cognome_ass = row[3] if len(row) > 3 else None
                        assistiti.append((row[0], row[1], nome_ass, cognome_ass))

                for assistito in assistiti:
                    cur.execute("INSERT INTO assistiti (nome_sigla, citta, nome, cognome) VALUES (%s, %s, %s, %s)", assistito)
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
                flash(f"Backup '{selected_filename}' ripristinato con successo!", "success")
                return redirect(url_for('report'))
            except psycopg.OperationalError as e:
                flash(f"Errore nel ripristino: {e}", "error")
            except Exception as e:
                flash(f"Errore generico nel ripristino: {e}", "error")
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()

    return render_template('restore.html', backup_records=backup_records)

@app.route('/clean', methods=['POST'])
def clean():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    password = request.form.get('password')
    if password != get_admin_password():
        flash("Password amministratore errata.", "error")
        return redirect(url_for('report'))

    filters = session.get('report_filters', {})
    volontario_email = filters.get('volontario_email', '')
    data_inizio = filters.get('data_inizio', '')
    data_fine = filters.get('data_fine', '')

    if data_fine:
        try:
            datetime.strptime(data_fine, '%Y-%m-%d')
            data_fine = f"{data_fine} 23:59:59"
        except ValueError:
            flash("Formato data non valido.", "error")
            return redirect(url_for('report'))

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
                query += " AND data_visita <= %s"
                params.append(data_fine)
        cur.execute(query, params)
        conn.commit()
        flash("Visite eliminate con successo!", "success")
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for('report'))

@app.route('/clean_volontari', methods=['POST'])
def clean_volontari():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    password = request.form.get('password')
    if password != get_admin_password():
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
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for('report'))

@app.route('/manuale')
def manuale():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))
    
    return render_template('manuale.html')

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
        if cur:
            cur.close()
        if conn:
            conn.close()
    
    return render_template('volontari.html', volontari=volontari)

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
            cur.execute("SELECT email FROM volontari WHERE email = %s", (email,))
            if cur.fetchone():
                flash(f"L'email {email} è già registrata.", "error")
                return render_template('aggiungi_volontario.html')
            
            cet = pytz.timezone('Europe/Rome')
            data_iscrizione = datetime.now(cet)

            cur.execute("""
                INSERT INTO volontari (email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione))
            conn.commit()
            flash(f"Volontario {nome} {cognome} aggiunto con successo!", "success")
            return redirect(url_for('lista_volontari'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiunta del volontario: {e}", "error")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()
    
    return render_template('aggiungi_volontario.html')

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
            if cur:
                cur.close()
            if conn:
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
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template('modifica_volontario.html', volontario=volontario)

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
        if cur:
            cur.close()
        if conn:
            conn.close()
    
    return redirect(url_for('lista_volontari'))

@app.route('/volontari/download_csv')
def download_csv_volontari():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione
            FROM volontari
            ORDER BY cognome, nome
        """)
        volontari = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['Email', 'Cognome', 'Nome', 'Telefono', 'Competenze', 'Disponibilità', 'Data Iscrizione'])
        for v in volontari:
            writer.writerow([
                v[0], v[1], v[2],
                v[3] or '',
                v[4] or '',
                v[5] or '',
                v[6].strftime('%Y-%m-%d') if v[6] else ''
            ])

        csv_output = output.getvalue().encode('utf-8-sig')  # utf-8-sig per compatibilità Excel
        output.close()
        return Response(
            csv_output,
            mimetype='text/csv',
            headers={"Content-Disposition": "attachment;filename=volontari.csv"}
        )
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('lista_volontari'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/inserisci_visita', methods=['GET', 'POST'])
def inserisci_visita():
    session['logged_in'] = False

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT nome_sigla, citta FROM assistiti ORDER BY nome_sigla")
        assistiti = cur.fetchall()
        cur.execute("SELECT email, cognome, nome FROM volontari ORDER BY cognome, nome")
        volontari = cur.fetchall()
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento dei dati: {e}", "error")
        assistiti = []
        volontari = []
        if cur:
            cur.close()
        if conn:
            conn.close()
        return render_template('inserisci_visita.html', assistiti=assistiti, volontari=volontari)
    
    if cur:
        cur.close()
    if conn:
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
            return render_template('inserisci_visita.html', assistiti=assistiti, volontari=volontari)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT email, cognome, nome FROM volontari WHERE email = %s", (volontario_email,))
            existing_volontario = cur.fetchone()
            if not existing_volontario and (not volontario_cognome or not volontario_nome):
                flash("Cognome e nome sono obbligatori per un nuovo volontario.", "error")
                if cur:
                    cur.close()
                if conn:
                    conn.close()
                return render_template('inserisci_visita.html', assistiti=assistiti, volontari=volontari)

            if not existing_volontario:
                cet = pytz.timezone('Europe/Rome')
                data_iscrizione = datetime.now(cet)
                cur.execute("""
                    INSERT INTO volontari (email, cognome, nome, telefono, competenze, disponibilita, data_iscrizione)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (volontario_email, volontario_cognome, volontario_nome, telefono, competenze, disponibilita, data_iscrizione))

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
            if cur:
                cur.close()
            if conn:
                conn.close()
    
    return render_template('inserisci_visita.html', assistiti=assistiti, volontari=volontari)

@app.route('/assistiti', methods=['GET'])
def lista_assistiti():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT nome_sigla, citta, nome, cognome FROM assistiti ORDER BY nome_sigla")
        assistiti = cur.fetchall()
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento degli assistiti: {e}", "error")
        assistiti = []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    
    return render_template('assistiti.html', assistiti=assistiti)

@app.route('/assistiti/aggiungi', methods=['GET', 'POST'])
def aggiungi_assistito():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        nome_sigla = request.form.get('nome_sigla')
        citta = request.form.get('citta')
        nome = request.form.get('nome', '').strip() or None
        cognome = request.form.get('cognome', '').strip() or None

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

            cur.execute("INSERT INTO assistiti (nome_sigla, citta, nome, cognome) VALUES (%s, %s, %s, %s)", (nome_sigla, citta, nome, cognome))
            conn.commit()
            flash(f"Assistito {nome_sigla} aggiunto con successo!", "success")
            return redirect(url_for('lista_assistiti'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiunta dell'assistito: {e}", "error")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()
    
    return render_template('aggiungi_assistito.html')

@app.route('/assistiti/modifica/<nome_sigla>', methods=['GET', 'POST'])
def modifica_assistito(nome_sigla):
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        citta = request.form.get('citta')
        nome = request.form.get('nome', '').strip() or None
        cognome = request.form.get('cognome', '').strip() or None
        if not citta:
            flash("La città è obbligatoria.", "error")
            return render_template('modifica_assistito.html', assistito={'nome_sigla': nome_sigla, 'citta': citta, 'nome': nome, 'cognome': cognome})

        try:
            cur.execute("UPDATE assistiti SET citta = %s, nome = %s, cognome = %s WHERE nome_sigla = %s", (citta, nome, cognome, nome_sigla))
            conn.commit()
            flash(f"Assistito {nome_sigla} aggiornato con successo!", "success")
            return redirect(url_for('lista_assistiti'))
        except psycopg.OperationalError as e:
            flash(f"Errore nell'aggiornamento dell'assistito: {e}", "error")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    try:
        cur.execute("SELECT nome_sigla, citta, nome, cognome FROM assistiti WHERE nome_sigla = %s", (nome_sigla,))
        assistito = cur.fetchone()
        if not assistito:
            flash("Assistito non trovato nel database.", "error")
            return render_template('modifica_assistito.html', assistito=None)
    except psycopg.OperationalError as e:
        flash(f"Errore nel caricamento dell'assistito: {e}", "error")
        assistito = None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template('modifica_assistito.html', assistito=assistito)

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
        if cur:
            cur.close()
        if conn:
            conn.close()
    
    return redirect(url_for('lista_assistiti'))

@app.route('/assistiti/download_csv')
def download_csv_assistiti():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT nome_sigla, citta, nome, cognome FROM assistiti ORDER BY citta, nome_sigla")
        assistiti = cur.fetchall()

        output = StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['Nome Sigla', 'Città', 'Nome', 'Cognome'])
        for a in assistiti:
            writer.writerow([a[0], a[1], a[2] or '', a[3] or ''])

        csv_output = output.getvalue().encode('utf-8-sig')
        output.close()
        return Response(
            csv_output,
            mimetype='text/csv',
            headers={"Content-Disposition": "attachment;filename=assistiti.csv"}
        )
    except psycopg.OperationalError as e:
        flash(f"Errore nel database: {e}", "error")
        return redirect(url_for('lista_assistiti'))
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@app.route('/cambio_password', methods=['GET', 'POST'])
def cambio_password():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        password_attuale = request.form.get('password_attuale', '')
        nuova_password = request.form.get('nuova_password', '')
        conferma_password = request.form.get('conferma_password', '')

        if password_attuale != get_admin_password():
            flash("La password attuale non è corretta.", "error")
            return render_template('cambio_password.html')

        if not nuova_password:
            flash("La nuova password non può essere vuota.", "error")
            return render_template('cambio_password.html')

        if nuova_password != conferma_password:
            flash("La nuova password e la conferma non coincidono.", "error")
            return render_template('cambio_password.html')

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE config SET value = %s WHERE key = 'admin_password'", (nuova_password,))
            conn.commit()
            flash("Password cambiata con successo!", "success")
            return redirect(url_for('report'))
        except Exception as e:
            flash(f"Errore nel cambio password: {e}", "error")
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    return render_template('cambio_password.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    oggi = datetime.now()

    # Determina periodo
    periodo = request.form.get('periodo', 'questo_mese')

    if periodo == 'questo_mese':
        data_inizio = f"{oggi.year}-{oggi.month:02d}-01"
        data_fine = oggi.strftime('%Y-%m-%d')
    elif periodo == 'mese_scorso':
        if oggi.month == 1:
            data_inizio = f"{oggi.year - 1}-12-01"
            data_fine = f"{oggi.year - 1}-12-31"
        else:
            import calendar
            ultimo_giorno = calendar.monthrange(oggi.year, oggi.month - 1)[1]
            data_inizio = f"{oggi.year}-{oggi.month - 1:02d}-01"
            data_fine = f"{oggi.year}-{oggi.month - 1:02d}-{ultimo_giorno:02d}"
    elif periodo == 'bimestre':
        due_mesi_fa = oggi.month - 1
        anno = oggi.year
        if due_mesi_fa <= 0:
            due_mesi_fa += 12
            anno -= 1
        data_inizio = f"{anno}-{due_mesi_fa:02d}-01"
        data_fine = oggi.strftime('%Y-%m-%d')
    elif periodo == 'personalizzato':
        data_inizio = request.form.get('data_inizio', f"{oggi.year}-{oggi.month:02d}-01")
        data_fine = request.form.get('data_fine', oggi.strftime('%Y-%m-%d'))
    else:
        data_inizio = f"{oggi.year}-{oggi.month:02d}-01"
        data_fine = oggi.strftime('%Y-%m-%d')
        periodo = 'questo_mese'

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Totale visite nel periodo
        cur.execute("SELECT COUNT(*) FROM visite WHERE data_visita >= %s AND data_visita <= %s",
                    (data_inizio, data_fine))
        totale_visite = cur.fetchone()[0]

        # Totale volontari e assistiti
        cur.execute("SELECT COUNT(*) FROM volontari")
        totale_volontari = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM assistiti")
        totale_assistiti = cur.fetchone()[0]

        # Volontari più attivi nel periodo
        cur.execute("""
            SELECT vol.email, vol.cognome, vol.nome, COUNT(*) as n_visite
            FROM visite v
            JOIN volontari vol ON v.volontario_email = vol.email
            WHERE v.data_visita >= %s AND v.data_visita <= %s
            GROUP BY vol.email, vol.cognome, vol.nome
            ORDER BY n_visite DESC
        """, (data_inizio, data_fine))
        rows = cur.fetchall()
        volontari_attivi = [{'email': r[0], 'cognome': r[1], 'nome': r[2], 'n_visite': r[3]} for r in rows]

        # Volontari senza visite nel periodo
        cur.execute("""
            SELECT vol.email, vol.cognome, vol.nome, vol.disponibilita
            FROM volontari vol
            WHERE vol.email NOT IN (
                SELECT DISTINCT volontario_email FROM visite
                WHERE data_visita >= %s AND data_visita <= %s
            )
            ORDER BY vol.cognome, vol.nome
        """, (data_inizio, data_fine))
        rows = cur.fetchall()
        volontari_inattivi = [{'email': r[0], 'cognome': r[1], 'nome': r[2], 'disponibilita': r[3]} for r in rows]

        # Assistiti visitati nel periodo
        cur.execute("""
            SELECT ass.nome_sigla, ass.citta, COUNT(*) as n_visite, ass.nome, ass.cognome
            FROM visite v
            JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
            WHERE v.data_visita >= %s AND v.data_visita <= %s
            GROUP BY ass.nome_sigla, ass.citta, ass.nome, ass.cognome
            ORDER BY n_visite DESC
        """, (data_inizio, data_fine))
        rows = cur.fetchall()
        assistiti_visitati = [{'nome_sigla': r[0], 'citta': r[1], 'n_visite': r[2], 'nome': r[3], 'cognome': r[4]} for r in rows]

        # Assistiti NON visitati nel periodo
        cur.execute("""
            SELECT ass.nome_sigla, ass.citta, ass.nome, ass.cognome
            FROM assistiti ass
            WHERE ass.nome_sigla NOT IN (
                SELECT DISTINCT assistito_nome FROM visite
                WHERE data_visita >= %s AND data_visita <= %s
            )
            ORDER BY ass.citta, ass.nome_sigla
        """, (data_inizio, data_fine))
        rows = cur.fetchall()
        assistiti_non_visitati = [{'nome_sigla': r[0], 'citta': r[1], 'nome': r[2], 'cognome': r[3]} for r in rows]

        media = round(totale_visite / totale_volontari, 1) if totale_volontari > 0 else 0

        stats = {
            'totale_visite': totale_visite,
            'totale_volontari': totale_volontari,
            'totale_assistiti': totale_assistiti,
            'volontari_attivi': len(volontari_attivi),
            'volontari_inattivi': len(volontari_inattivi),
            'assistiti_visitati': len(assistiti_visitati),
            'assistiti_non_visitati': len(assistiti_non_visitati),
            'media_visite': media,
        }

    except Exception as e:
        logging.error(f"Errore dashboard: {e}")
        flash(f"Errore nel caricamento della dashboard: {e}", "error")
        stats = {k: 0 for k in ['totale_visite','totale_volontari','totale_assistiti',
                                  'volontari_attivi','volontari_inattivi',
                                  'assistiti_visitati','assistiti_non_visitati','media_visite']}
        volontari_attivi = []
        volontari_inattivi = []
        assistiti_visitati = []
        assistiti_non_visitati = []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    return render_template('dashboard.html',
        periodo=periodo,
        data_inizio=data_inizio,
        data_fine=data_fine,
        stats=stats,
        volontari_attivi=volontari_attivi,
        volontari_inattivi=volontari_inattivi,
        assistiti_visitati=assistiti_visitati,
        assistiti_non_visitati=assistiti_non_visitati,
    )


@app.route('/dashboard/pdf', methods=['POST'])
def dashboard_pdf():
    if not session.get('logged_in', False):
        return redirect(url_for('admin_login'))

    periodo    = request.form.get('periodo', 'questo_mese')
    data_inizio = request.form.get('data_inizio', '')
    data_fine   = request.form.get('data_fine', '')

    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM visite WHERE data_visita >= %s AND data_visita <= %s", (data_inizio, data_fine))
        totale_visite = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM volontari")
        totale_volontari = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM assistiti")
        totale_assistiti = cur.fetchone()[0]

        cur.execute("""
            SELECT vol.cognome, vol.nome, vol.email, COUNT(*) as n
            FROM visite v JOIN volontari vol ON v.volontario_email = vol.email
            WHERE v.data_visita >= %s AND v.data_visita <= %s
            GROUP BY vol.cognome, vol.nome, vol.email ORDER BY n DESC
        """, (data_inizio, data_fine))
        volontari_attivi = cur.fetchall()

        cur.execute("""
            SELECT vol.cognome, vol.nome, vol.email, vol.disponibilita
            FROM volontari vol WHERE vol.email NOT IN (
                SELECT DISTINCT volontario_email FROM visite
                WHERE data_visita >= %s AND data_visita <= %s
            ) ORDER BY vol.cognome, vol.nome
        """, (data_inizio, data_fine))
        volontari_inattivi = cur.fetchall()

        cur.execute("""
            SELECT ass.nome_sigla, ass.citta, COUNT(*) as n, ass.nome, ass.cognome
            FROM visite v JOIN assistiti ass ON v.assistito_nome = ass.nome_sigla
            WHERE v.data_visita >= %s AND v.data_visita <= %s
            GROUP BY ass.nome_sigla, ass.citta, ass.nome, ass.cognome ORDER BY n DESC
        """, (data_inizio, data_fine))
        assistiti_visitati = cur.fetchall()

        cur.execute("""
            SELECT ass.nome_sigla, ass.citta, ass.nome, ass.cognome FROM assistiti ass
            WHERE ass.nome_sigla NOT IN (
                SELECT DISTINCT assistito_nome FROM visite
                WHERE data_visita >= %s AND data_visita <= %s
            ) ORDER BY ass.citta, ass.nome_sigla
        """, (data_inizio, data_fine))
        assistiti_non_visitati = cur.fetchall()

    except Exception as e:
        flash(f"Errore nella generazione del PDF: {e}", "error")
        return redirect(url_for('dashboard'))
    finally:
        if cur: cur.close()
        if conn: conn.close()

    # === GENERA PDF ===
    buf = BytesIO()
    styles = getSampleStyleSheet()

    h1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=14,
                         textColor=colors.HexColor('#1a3a6b'), spaceAfter=6)
    h2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=11,
                         textColor=colors.HexColor('#2e6da4'), spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle('Body', parent=styles['Normal'], fontSize=9, leading=13)
    centro = ParagraphStyle('Centro', parent=styles['Normal'], fontSize=9,
                             alignment=TA_CENTER, textColor=colors.white)

    NOMI_PERIODO = {
        'questo_mese': 'Questo mese',
        'mese_scorso': 'Mese scorso',
        'bimestre':    'Bimestre',
        'personalizzato': 'Personalizzato',
    }

    def tabella(header_row, data_rows, col_widths, header_color='#2e6da4'):
        rows = [header_row] + (data_rows if data_rows else [['—'] * len(header_row)])
        t = Table(rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ('BACKGROUND',  (0,0), (-1,0), colors.HexColor(header_color)),
            ('TEXTCOLOR',   (0,0), (-1,0), colors.white),
            ('FONTNAME',    (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',    (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0f4fa')]),
            ('GRID',        (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
            ('VALIGN',      (0,0), (-1,-1), 'MIDDLE'),
            ('PADDING',     (0,0), (-1,-1), 5),
        ]))
        return t

    media = round(totale_visite / totale_volontari, 1) if totale_volontari > 0 else 0

    story = [
        Paragraph('Associazione TEMPO INSIEME', ParagraphStyle('titolo', parent=styles['Title'],
            fontSize=18, textColor=colors.HexColor('#1a3a6b'), alignment=TA_CENTER)),
        Paragraph('Dashboard Riepilogativa', ParagraphStyle('sub', parent=styles['Normal'],
            fontSize=12, textColor=colors.HexColor('#555'), alignment=TA_CENTER, spaceAfter=4)),
        Paragraph(f'Periodo: <b>{NOMI_PERIODO.get(periodo, periodo)}</b> '
                  f'({data_inizio} → {data_fine})',
                  ParagraphStyle('per', parent=styles['Normal'], fontSize=10,
                                 textColor=colors.HexColor('#555'), alignment=TA_CENTER, spaceAfter=16)),
        HRFlowable(width='100%', thickness=1, color=colors.HexColor('#cccccc'), spaceAfter=12),

        # Riepilogo numerico
        Paragraph('Riepilogo', h1),
        tabella(
            ['Indicatore', 'Valore'],
            [
                ['Visite nel periodo', str(totale_visite)],
                ['Volontari totali', str(totale_volontari)],
                ['Volontari attivi nel periodo', str(len(volontari_attivi))],
                ['Volontari senza visite', str(len(volontari_inattivi))],
                ['Assistiti totali', str(totale_assistiti)],
                ['Assistiti visitati', str(len(assistiti_visitati))],
                ['Assistiti non visitati', str(len(assistiti_non_visitati))],
                ['Media visite per volontario', str(media)],
            ],
            [10*cm, 6*cm]
        ),
        Spacer(1, 16),

        # Volontari attivi
        Paragraph('Volontari più attivi nel periodo', h2),
        tabella(
            ['#', 'Cognome e Nome', 'Email', 'N. Visite'],
            [[str(i+1), f"{r[0]} {r[1]}", r[2], str(r[3])] for i, r in enumerate(volontari_attivi)],
            [1*cm, 5*cm, 7*cm, 3*cm]
        ),
        Spacer(1, 12),

        # Volontari senza visite
        Paragraph('Volontari senza visite nel periodo', h2),
        tabella(
            ['Cognome e Nome', 'Email', 'Disponibilità'],
            [[f"{r[0]} {r[1]}", r[2], r[3] or '—'] for r in volontari_inattivi],
            [5*cm, 7*cm, 5*cm],
            header_color='#c0392b'
        ),
        Spacer(1, 12),

        # Assistiti visitati
        Paragraph('Assistiti visitati nel periodo', h2),
        tabella(
            ['Sigla', 'Nome', 'Cognome', 'Città', 'N. Visite'],
            [[r[0], r[3] or '—', r[4] or '—', r[1], str(r[2])] for r in assistiti_visitati],
            [2.5*cm, 4*cm, 4*cm, 5*cm, 2*cm]
        ),
        Spacer(1, 12),

        # Assistiti non visitati
        Paragraph('Assistiti non visitati nel periodo', h2),
        tabella(
            ['Sigla', 'Nome', 'Cognome', 'Città'],
            [[r[0], r[2] or '—', r[3] or '—', r[1]] for r in assistiti_non_visitati],
            [2.5*cm, 4*cm, 4*cm, 7*cm],
            header_color='#c0392b'
        ),

        Spacer(1, 20),
        HRFlowable(width='100%', thickness=1, color=colors.HexColor('#cccccc'), spaceAfter=6),
        Paragraph(f'Generato il {datetime.now().strftime("%d/%m/%Y alle %H:%M")}',
                  ParagraphStyle('footer', parent=styles['Normal'], fontSize=8,
                                 textColor=colors.grey, alignment=TA_CENTER)),
    ]

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    doc.build(story)
    buf.seek(0)

    filename = f"dashboard_{data_inizio}_{data_fine}.pdf"
    return send_file(buf, download_name=filename, as_attachment=True, mimetype='application/pdf')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('report_filters', None)
    flash("Logout effettuato con successo!", "success")
    return redirect(url_for('admin_login'))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)