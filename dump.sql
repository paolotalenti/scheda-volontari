-- Elimina le tabelle se esistono per evitare conflitti
DROP TABLE IF EXISTS visite;
DROP TABLE IF EXISTS assistiti;
DROP TABLE IF EXISTS volontari;

-- Crea la tabella volontari
CREATE TABLE volontari (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    cognome TEXT NOT NULL,
    nome TEXT NOT NULL,
    cellulare TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE
);

-- Crea la tabella assistiti
CREATE TABLE assistiti (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    nome_sigla TEXT NOT NULL UNIQUE,
    citta TEXT NOT NULL
);

-- Crea la tabella visite
CREATE TABLE visite (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    volontario_email TEXT NOT NULL,
    assistito_nome TEXT NOT NULL,
    accoglienza TEXT NOT NULL,
    data_visita TEXT NOT NULL,
    necessita TEXT NOT NULL,
    cosa_migliorare TEXT NOT NULL,
    FOREIGN KEY (volontario_email) REFERENCES volontari(email) ON DELETE RESTRICT,
    FOREIGN KEY (assistito_nome) REFERENCES assistiti(nome_sigla) ON DELETE RESTRICT
);

-- Nota: Inserisci qui i dati esportati da SQLite, se necessario
-- Esempio di sintassi per inserire dati:
-- INSERT INTO volontari (cognome, nome, cellulare, email) VALUES
-- ('Rossi', 'Mario', '+39 123 456 7890', 'mario.rossi@example.com'),
-- ('Bianchi', 'Laura', '+39 987 654 3210', 'laura.bianchi@example.com');
-- INSERT INTO assistiti (nome_sigla, citta) VALUES
-- ('A1', 'Milano'),
-- ('B2', 'Roma');
-- INSERT INTO visite (volontario_email, assistito_nome, accoglienza, data_visita, necessita, cosa_migliorare) VALUES
-- ('mario.rossi@example.com', 'A1', 'Buona', '2025-07-01', 'Supporto psicologico', 'Migliorare tempi di risposta');