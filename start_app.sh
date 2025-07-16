#!/bin/bash
PROJECT_DIR="/Users/paolotalenti/scheda_volontari"
VENV_DIR="$PROJECT_DIR/venv"
APP_FILE="$PROJECT_DIR/app.py"
if [ ! -d "$PROJECT_DIR" ]; then
    echo "Errore: La directory $PROJECT_DIR non esiste."
    exit 1
fi
cd "$PROJECT_DIR" || exit 1
if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment non trovato. Creazione di uno nuovo..."
    python3 -m venv venv
fi
source "$VENV_DIR/bin/activate"
echo "Verifica delle dipendenze..."
pip install flask fpdf2 psycopg2-binary gunicorn
if [ ! -f "$APP_FILE" ]; then
    echo "Errore: Il file $APP_FILE non esiste."
    exit 1
fi
echo "Avvio dell'applicazione..."
python3 app.py