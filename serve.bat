@echo off
rem Coach Card Checker (version web) - test local avant publication.
rem Ouvre le site dans le navigateur a partir de ce dossier.
cd /d "%~dp0"
start "" "http://127.0.0.1:8090/"
py -3 -m http.server 8090 2>nul || python -m http.server 8090
pause
