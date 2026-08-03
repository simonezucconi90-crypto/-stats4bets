
# Stats4Bets Web App

App utilizzabile da Safari su iPhone.

## Avvio sul PC
1. Installa Python 3.11+
2. Apri il terminale nella cartella
3. Esegui:
   pip install -r requirements.txt
   streamlit run app.py

## Lettura automatica screenshot
Imposta la variabile OPENAI_API_KEY.
Senza chiave l'app funziona, ma apre una scheda vuota da compilare.

## Pubblicazione gratuita su Streamlit Community Cloud
1. Crea un account GitHub.
2. Crea un nuovo repository e carica app.py e requirements.txt.
3. Accedi a Streamlit Community Cloud.
4. Crea una nuova app selezionando repository e app.py.
5. Nelle impostazioni Secrets inserisci:
   OPENAI_API_KEY="la_tua_chiave"
   OPENAI_MODEL="gpt-4.1-mini"

## Nota sul database
Questa versione usa SQLite. È perfetta per prova locale.
Per una versione cloud stabile va collegato un database persistente (ad esempio Supabase).
