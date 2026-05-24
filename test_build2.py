import py_compile
py_compile.compile("remote_manager.py", doraise=True)

# Test the template format
src = open("remote_manager.py", encoding="utf-8").read()
idx = src.find("ENCRYPTED_LOADER_TEMPLATE")
start = src.index("'''", idx) + 3
end = src.index("'''", start)
template = src[start:end]

try:
    result = template.format(pdf_name="test.pdf", enc_key="aabbccdd", enc_data=repr([1,2,3]))
    compile(result, "<loader>", "exec")
    print("Template formats and compiles OK")
    print(f"Template length: {len(template)} chars")
    print(f"Result length: {len(result)} chars")
except Exception as e:
    print(f"ERROR: {e}")

# Test minimal loader execution (without ctypes)
import_ok = all(m in __builtins__.__dict__ or m in dir() for m in ["sys", "os", "subprocess", "base64", "zlib", "ctypes", "time"])
print(f"All loader modules available: {import_ok}")
