@echo off
rem Interface Mocap : ouvre http://127.0.0.1:8765/ dans le navigateur.
rem Fermer cette fenetre arrete le serveur (et un traitement en cours).
title Mocap
cd /d "%~dp0"
"%~dp0tools\venv\Scripts\python.exe" "%~dp0ui\server.py" %*
if errorlevel 1 pause
