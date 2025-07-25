PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE volontari (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cognome TEXT NOT NULL,
                        nome TEXT NOT NULL,
                        cellulare TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE);
INSERT INTO volontari VALUES(2,'Cam Lay','Luzmila','3338184555','lcamlay@gmail.com');
INSERT INTO volontari VALUES(6,'Rossi','Mario','1234567890','nuovoesempio2@gmail.com');
INSERT INTO volontari VALUES(8,'Nuovo','Nuovissimo','12345','nuovonewesempio@gmail.com');
CREATE TABLE assistiti (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        nome_sigla TEXT NOT NULL UNIQUE,
                        citta TEXT NOT NULL);
INSERT INTO assistiti VALUES(4,'BB','Torino');
INSERT INTO assistiti VALUES(6,'aa','milano');
CREATE TABLE visite (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        volontario_email TEXT NOT NULL,
                        assistito_nome TEXT NOT NULL,
                        accoglienza TEXT NOT NULL,
                        data_visita TEXT NOT NULL,
                        necessita TEXT NOT NULL,
                        cosa_migliorare TEXT NOT NULL,
                        FOREIGN KEY (volontario_email) REFERENCES volontari (email),
                        FOREIGN KEY (assistito_nome) REFERENCES assistiti (nome_sigla));
INSERT INTO visite VALUES(10,'lcamlay@gmail.com','BB','Buona','2025-07-15','alta','Boh');
INSERT INTO visite VALUES(12,'nuovoesempio2@gmail.com','aa','Buona','2025-07-15','Boh','Boh');
INSERT INTO visite VALUES(13,'nuovoesempio2@gmail.com','aa','Buona','2025-07-15','Boh','Boh');
INSERT INTO visite VALUES(15,'nuovonewesempio@gmail.com','aa','Buona','2025-07-12','boh','boh');
DELETE FROM sqlite_sequence;
INSERT INTO sqlite_sequence VALUES('volontari',8);
INSERT INTO sqlite_sequence VALUES('assistiti',6);
INSERT INTO sqlite_sequence VALUES('visite',15);
CREATE INDEX idx_volontario_email ON visite(volontario_email);
CREATE INDEX idx_data_visita ON visite(data_visita);
COMMIT;
